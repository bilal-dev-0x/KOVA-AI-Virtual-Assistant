import re

from brain.base import BrainError
from modules.actions import result_for_ai
from modules.logger import get_logger
from modules.result import Result

# The agent loop: the AI plans, asks for tools, reads their results and
# continues until it can answer. KOVA decides what actually runs.

MAX_STEPS = 6  # rounds of tool calls per request, so a confused plan can't run forever

# "I've created the folder" with no create tool run: a small model once said so after doing nothing.
# Each claim needs one of its tools to have worked in this request, or the answer isn't spoken.
CLAIMS = {
    "created": {"create_item"}, "renamed": {"rename_item"},
    "deleted": {"delete_items", "forget_memory", "forget_all_memories", "cancel_reminder"},
    "closed": {"close_app", "close_window", "close_tab", "force_close", "end_process", "close_terminals"},
    "opened": {"open_app", "open_file", "open_folder", "new_window", "open_web_search", "play_music",
               "open_start_menu"},
    "launched": {"open_app"}, "typed": {"ui_type"}, "written": {"ui_type", "create_item"},
    "saved": {"ui_press_keys", "ui_click", "create_item", "remember"}, "clicked": {"ui_click"},
    "muted": {"mute"}, "minimised": {"move_window", "show_desktop"}, "maximised": {"move_window"},
}
CLAIM = re.compile(r"\bI(?:'ve| have)\s+(?:just\s+|now\s+|also\s+|successfully\s+)?(" + "|".join(CLAIMS) + r")\b",
                   re.IGNORECASE)


def unbacked_claim(text, succeeded):
    """The first "I've <done>" in text that no successful tool backs up, or None."""
    if re.search(r"\b(earlier|before|already)\b", text or "", re.IGNORECASE):
        return None  # about something done in an earlier request
    for match in CLAIM.finditer(text or ""):
        if not CLAIMS[match[1].lower()] & succeeded:
            return match[1].lower()
    return None

log = get_logger()


def system_prompt(config):
    name = config.get("ASSISTANT_NAME", "KOVA")
    title = config.get("USER_TITLE", "Commander")
    return (
        f"You are {name}, an AI assistant running on the user's Windows PC. You act on the PC "
        f"only through the provided tools. Address the user as '{title}'. "
        "Your replies are spoken aloud: keep them short (one to three sentences), plain text, "
        "no markdown, lists or emojis. "
        "Use tools for every action and every live fact (system figures, time, news, weather, prices). "
        "Never invent numbers; take them from tool results. "
        "Only say you did something if its tool result is ok, and never say you will do something unless "
        "you call its tool in the same reply. If a tool fails or the user declines, "
        "say so briefly and don't try the same thing again. "
        "Call several tools at once when the steps don't depend on each other. "
        "Approval for risky actions is handled for you, so don't ask for permission yourself. "
        "You cannot run shell commands or install software. "
        "Speech recognition can mishear words: if a request looks garbled, ask the user to repeat it "
        "instead of guessing an action. "
        "A message may start with a note about commands the user ran without you; use it to "
        "understand words like 'it' or 'that'. "
        "It may also include facts known about the user; use them when they help, and use the recall "
        "tool to look up anything else the user told you before. "
        "When the user states a lasting preference or fact about themselves (what they like, where "
        "they keep things, what they do), save it with the remember tool in their words; they are "
        "asked before it is saved. Never save passwords, PINs, keys or card numbers, and don't save "
        "one-off requests. "
        "For tasks inside an app: the ui_ tools work on the window in front; read it with ui_read before "
        "clicking, give window= words of the title you expect, and do one step at a time. If a step says it "
        "stopped, don't try another way; tell the user what happened. Use look_at_screen only when the "
        "user asks about what is on the screen. "
        "Work out sums and simple questions yourself; don't open the calculator or look at the screen for them. "
        "When the user asks to open or click something inside the app in front (a chat, a contact, a tab, a "
        "file in a list), do it with ui_read and ui_click instead of telling them where to click."
    )


def _question(actions):
    if len(actions) == 1:
        return f"Should I {actions[0]}?"
    return f"Should I {', '.join(actions[:-1])} and {actions[-1]}?"


def _args_text(args):
    return ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())


def run_calls(calls, runner, confirm, trace, done=None, approved_tools=None, succeeded=None):
    """Run one batch of tool calls. Returns [(call, result_dict)] and the Results. done collects
    what ran successfully. After a result that halts (the window changed), the rest is not run.
    approved_tools: tools the user already said yes to in this plan; they aren't asked about again."""
    done = [] if done is None else done
    approved_tools = set() if approved_tools is None else approved_tools
    succeeded = set() if succeeded is None else succeeded  # tool names that worked in this request
    permissions = runner.permissions
    prepared = [(call, *runner.prepare(call.name, call.args)) for call in calls]

    # Whole-plan approval: every controlled step in this batch is approved with one question, and
    # a kind of step said yes to once isn't asked about again in the same plan.
    # Critical steps are asked about separately, right before each one runs.
    asking = [(tool, args) for _, tool, args, problem in prepared
              if problem is None and permissions.needs_approval(tool) and not permissions.asks_every_time(tool)
              and tool.name not in approved_tools]
    # bool(): an answer that is neither yes nor no (None) must count as no, never as "not needed"
    batch_approved = bool(confirm(_question([tool.describe(args) for tool, args in asking]))) if asking else True
    if asking and batch_approved:
        approved_tools.update(tool.name for tool, _ in asking)

    answers, results = [], []
    for call, tool, args, problem in prepared:
        trace(f"  → {call.name}({_args_text(call.args)})")
        if any(r.halt for r in results):
            result = Result(False, "Not done: the plan stopped at an earlier step.")
        elif problem:
            result = problem
        else:
            if permissions.asks_every_time(tool):
                approved = bool(confirm(f"This needs extra care. Should I really {tool.describe(args)}?"))
            elif permissions.needs_approval(tool):
                approved = batch_approved
            else:
                approved = None

            if approved is False:
                result = Result(False, "The user said no, so this was not done.")
                runner.record("agent", tool, args, "declined", result)
            else:
                result = runner.run(tool, args, "approved" if approved else "not needed", source="agent")
                if result.success:
                    done.append(tool.describe(args))
                    succeeded.add(tool.name)
        trace(f"  ← {result.message}")
        answers.append((call, result_for_ai(result)))
        results.append(result)
    return answers, results


def run(session, text, runner, confirm, trace=print, max_steps=MAX_STEPS):
    """Handle one request with the AI. Returns the final spoken Result."""
    details, done, approved_tools, succeeded = [], [], set(), set()
    try:
        reply = session.send(text)
        steps = 0
        while reply.calls:
            if steps == max_steps:
                session.reset()  # never leave unanswered tool calls in the conversation
                log.warning("Agent stopped after %s steps: %s", max_steps, text)
                return Result(False, f"I stopped after {max_steps} steps, so I don't get stuck in a loop. "
                                     "Try asking in smaller parts.", detail="\n".join(details))
            steps += 1
            if reply.text:
                trace(f"  💭 {reply.text}")
            answers, results = run_calls(reply.calls, runner, confirm, trace, done, approved_tools, succeeded)
            details += [r.detail for r in results if r.detail]
            halted = next((r for r in results if r.halt), None)
            if halted:
                # Something looked wrong: stop safely here, not let the brain try something else
                session.reset()
                log.info("Plan halted: %s", halted.message)
                ran = f" I already did: {', '.join(done)}." if done else " Nothing else ran."
                return Result(False, halted.message.rstrip(".") + "." + ran, detail="\n".join(details))
            reply = session.send_results(answers)
    except BrainError as e:
        session.reset()
        log.warning("Agent could not answer: %s", e)
        return Result(False, str(e))
    except KeyboardInterrupt:
        session.reset()
        log.info("Plan cancelled with Ctrl+C: %s", text)
        return Result(False, "Okay, I stopped.")

    log.info("Agent answered after %s step(s): %s", steps, (reply.text or "")[:200])
    claim = unbacked_claim(reply.text, succeeded)
    if claim:
        log.warning("Answer not spoken: it says %r but no tool did that", claim)
        ran = f" What I did do: {', '.join(done)}." if done else ""
        return Result(False, f"Sorry, I said I {claim} it, but I didn't actually manage to.{ran}",
                      detail=f"The brain's answer was: {reply.text}")
    return Result(True, reply.text or "Done.", detail="\n".join(details))
