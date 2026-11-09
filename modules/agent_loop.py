import re

from brain.base import BrainError
from modules.actions import result_for_ai
from modules.logger import get_logger
from modules.result import Result
from modules import tool_registry

# The agent loop: the AI plans, asks for tools, reads their results and
# continues until it can answer. KOVA decides what actually runs.

MAX_STEPS = 6  # rounds of tool calls per request, so a confused plan can't run forever

# "I've created the folder", "I'll set a reminder", "Closing the PowerShell terminal": the KOVA server's
# small model said all of these in live tests without running any tool. Each claim needs one of its
# tools to have worked in this request, or the answer isn't spoken.
CLOSE = {"close_app", "close_window", "close_tab", "close_tabs", "force_close", "end_process", "close_terminals"}
OPEN = {"open_app", "open_file", "open_folder", "new_window", "open_web_search", "play_music", "open_start_menu",
        "launch_game", "open_project"}
MOVE = {"move_window", "side_by_side", "show_desktop"}
# verb: (past, -ing, the tools that do it)
CLAIMS = {
    "create": ("created", "creating", {"create_item"}), "make": ("made", "making", {"create_item"}),
    "rename": ("renamed", "renaming", {"rename_item"}),
    "delete": ("deleted", "deleting", {"delete_items", "forget_memory", "forget_all_memories", "cancel_reminder"}),
    "close": ("closed", "closing", CLOSE), "open": ("opened", "opening", OPEN),
    "launch": ("launched", "launching", OPEN), "start": ("started", "starting", OPEN | {"run_tests", "gaming_mode"}),
    "type": ("typed", "typing", {"ui_type"}), "write": ("written", "writing", {"ui_type", "create_item"}),
    "save": ("saved", "saving", {"ui_press_keys", "ui_click", "create_item", "remember"}),
    "click": ("clicked", "clicking", {"ui_click"}), "mute": ("muted", "muting", {"mute"}),
    "minimise": ("minimised", "minimising", MOVE), "minimize": ("minimized", "minimizing", MOVE),
    "maximise": ("maximised", "maximising", MOVE), "maximize": ("maximized", "maximizing", MOVE),
    "move": ("moved", "moving", MOVE), "switch": ("switched", "switching", MOVE | {"set_power_plan", "set_voice"}),
    "commit": ("committed", "committing", {"git_commit"}), "push": ("pushed", "pushing", {"git_push"}),
}
_FORMS = {}
for _verb, (_past, _ing, _tools) in CLAIMS.items():
    for _form in (_verb, _past, _ing):
        _FORMS[_form] = (_verb, _tools)
_WORDS = "|".join(sorted(_FORMS, key=len, reverse=True))
CLAIM = re.compile(
    rf"\bI(?:'ve| have|'ll| will|'m going to| am going to|'m| am)\s+(?:just\s+|now\s+|also\s+|successfully\s+)?"
    rf"(?:force\s+|go ahead and\s+)?"  # "I'll force close Edge now" (voice test): nothing closed it
    rf"(?P<said>{_WORDS})\b|(?:^|[.!?]\s+)(?P<lead>{_WORDS})\b", re.IGNORECASE)
# "I see the Valorant window", "The YouTube window is open and showing a search for film": a
# description of the screen needs a tool that read it in this request
SEE_CLAIM = re.compile(r"\bI (?:can )?(?:see|notice) (?:a|an|the|that|your|it|some|two|three|four|five|you have|you've)\b|\b(?:is|are) (?:now )?(?:open and )?(?:showing|displaying)\b"
                       r"|\bon (?:your|the) screen\b", re.IGNORECASE)
SEE_TOOLS = {"look_at_screen", "ui_read", "open_windows", "list_running_apps"}
# "I've set a reminder" / "I'll set a timer for 6:30": only the reminder tool backs that
SET_CLAIM = re.compile(r"\bI(?:'ve| have|'ll| will)\s+(?:just\s+|now\s+)?set\s+(?:a|an|the|your)\s+"
                       r"(?:reminder|timer|alarm)", re.IGNORECASE)


def unbacked_claim(text, succeeded):
    """The verb of the first "I've / I'll / I'm <doing>" in text that no successful tool backs up, or None."""
    text = text or ""
    if re.search(r"\b(earlier|before|already)\b", text, re.IGNORECASE):
        return None  # about something done in an earlier request
    if SET_CLAIM.search(text) and "set_reminder" not in succeeded:
        return "set"
    if SEE_CLAIM.search(text) and not SEE_TOOLS & succeeded:
        return "see"
    for match in CLAIM.finditer(text):
        word = (match["said"] or match["lead"]).lower()
        if match["lead"] and not word.endswith("ing"):
            continue  # "Open the app yourself" is advice, not a claim; "Closing the app" is a claim
        verb, tools = _FORMS[word]
        if not tools & succeeded:
            return verb
    return None

log = get_logger()


def system_prompt(config):
    from modules import personas
    name = config.get("ASSISTANT_NAME", "KOVA")
    title = config.get("USER_TITLE", "Commander")
    return (
        f"You are {name}, an AI assistant running on the user's Windows PC. You act on the PC "
        f"only through the provided tools. Address the user as '{title}'. "
        f"{personas.tone(config)} "
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
        "If project tests fail and the user wants a code fix, propose one small unified diff with apply_project_patch; "
        "the tool previews that exact diff and asks before changing one existing project file, and undo restores its backup. "
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


def _intent_problem(tool, args):
    if tool.name == "organise_folder":
        folder = (args or {}).get("folder", "downloads")
        return Result(False, f"Did you want me to organise {folder.title()}? Say it in those words and I will.",
                      halt=True)
    return Result(False, f"I won't {tool.name.replace('_', ' ')} unless you ask for it directly.", halt=True)


def _has_user_intent(tool, user_text):
    text = re.sub(r"[^a-z0-9]+", " ", (user_text or "").lower()).strip()
    for word in tool.intent_words:
        phrase = re.sub(r"[^a-z0-9]+", " ", word.lower()).strip()
        for match in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", text):
            before = text[:match.start()].rstrip()
            if re.search(r"\b(?:dont|do not|never|no|without)(?:\s+\w+){0,3}\s*$", before):
                continue
            return True
    return False


def run_calls(calls, runner, confirm, trace, done=None, approved_tools=None, succeeded=None,
              user_text=None):
    """Run one batch of tool calls. Returns [(call, result_dict)] and the Results. done collects
    what ran successfully. After a result that halts (the window changed), the rest is not run.
    approved_tools: tools the user already said yes to in this plan; they aren't asked about again."""
    done = [] if done is None else done
    approved_tools = set() if approved_tools is None else approved_tools
    succeeded = set() if succeeded is None else succeeded  # tool names that worked in this request
    permissions = runner.permissions
    prepared = []
    for call in calls:
        registered = tool_registry.get(call.name)
        if registered and registered.intent_words and not _has_user_intent(registered, user_text):
            prepared.append((call, registered, dict(call.args or {}), _intent_problem(registered, call.args)))
        else:
            prepared.append((call, *runner.prepare(call.name, call.args)))

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


def run(session, text, runner, confirm, trace=print, max_steps=MAX_STEPS, user_text=None):
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
            answers, results = run_calls(reply.calls, runner, confirm, trace, done, approved_tools, succeeded,
                                         text if user_text is None else user_text)
            details += [r.detail for r in results if r.detail]
            halted = next((r for r in results if r.halt), None)
            if halted:
                # Something looked wrong: stop safely here, not let the brain try something else
                session.reset()
                log.info("Plan halted: %s", halted.message)
                ran = f" I already did: {', '.join(done)}." if done else " Nothing else ran."
                stopped = next(((call, result) for call, result in zip(reply.calls, results) if result is halted),
                               (None, halted))[0]
                resume = ({"tool": stopped.name, "args": dict(stopped.args or {})}
                          if stopped is not None else None)
                return Result(False, halted.message.rstrip(".") + "." + ran, detail="\n".join(details),
                              halt=True, choices=halted.choices, resume=resume)
            reply = session.send_results(answers)
    except BrainError as e:
        session.reset()
        log.warning("Agent could not answer: %s", e)
        message = str(e)
        if not message.lower().startswith(("i couldn't reach ", "i could not reach ")):
            message = "I couldn't reach the AI right now."
        return Result(False, message)
    except KeyboardInterrupt:
        session.reset()
        log.info("Plan cancelled with Ctrl+C: %s", text)
        return Result(False, "Okay, I stopped.")

    log.info("Agent answered after %s step(s): %s", steps, (reply.text or "")[:200])
    claim = unbacked_claim(reply.text, succeeded)
    if claim:
        log.warning("Answer not spoken: it says %r but no tool did that", claim)
        ran = f" What I did do: {', '.join(done)}." if done else ""
        if claim == "see":
            return Result(False, f"Sorry, I was about to describe your screen without looking at it.{ran} "
                                 "Ask me what's on the screen, and I'll read it.",
                          detail=f"The brain's answer was: {reply.text}")
        return Result(False, f"Sorry, I said I'd {claim} it, but nothing actually did that.{ran} "
                             "Please ask again, or say it a little differently.",
                      detail=f"The brain's answer was: {reply.text}")
    return Result(True, reply.text or "Done.", detail="\n".join(details))
