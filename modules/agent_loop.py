from brain.base import BrainError
from modules.actions import result_for_ai
from modules.logger import get_logger
from modules.result import Result

# The agent loop: the AI plans, asks for tools, reads their results and
# continues until it can answer. KOVA decides what actually runs.

MAX_STEPS = 6  # rounds of tool calls per request, so a confused plan can't run forever

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
        "Only say you did something if its tool result is ok. If a tool fails or the user declines, "
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
        "one-off requests."
    )


def _question(actions):
    if len(actions) == 1:
        return f"Should I {actions[0]}?"
    return f"Should I {', '.join(actions[:-1])} and {actions[-1]}?"


def _args_text(args):
    return ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())


def run_calls(calls, runner, confirm, trace):
    """Run one batch of tool calls. Returns [(call, result_dict)] and the Results."""
    permissions = runner.permissions
    prepared = [(call, *runner.prepare(call.name, call.args)) for call in calls]

    # Whole-plan approval: every controlled step in this batch is approved with one question.
    # Critical steps are asked about separately, right before they run.
    batch = [tool.describe(args) for _, tool, args, problem in prepared
             if problem is None and permissions.needs_approval(tool) and not permissions.asks_every_time(tool)]
    batch_approved = confirm(_question(batch)) if batch else True

    answers, results = [], []
    for call, tool, args, problem in prepared:
        trace(f"  → {call.name}({_args_text(call.args)})")
        if problem:
            result = problem
        else:
            if permissions.asks_every_time(tool):
                approved = confirm(f"This needs extra care. Should I really {tool.describe(args)}?")
            elif permissions.needs_approval(tool):
                approved = batch_approved
            else:
                approved = None

            if approved is False:
                result = Result(False, "The user said no, so this was not done.")
                runner.record("agent", tool, args, "declined", result)
            else:
                result = runner.run(tool, args, "approved" if approved else "not needed", source="agent")
        trace(f"  ← {result.message}")
        answers.append((call, result_for_ai(result)))
        results.append(result)
    return answers, results


def run(session, text, runner, confirm, trace=print, max_steps=MAX_STEPS):
    """Handle one request with the AI. Returns the final spoken Result."""
    details = []
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
            answers, results = run_calls(reply.calls, runner, confirm, trace)
            details += [r.detail for r in results if r.detail]
            reply = session.send_results(answers)
    except BrainError as e:
        session.reset()
        return Result(False, str(e))
    except KeyboardInterrupt:
        session.reset()
        log.info("Plan cancelled with Ctrl+C: %s", text)
        return Result(False, "Okay, I stopped.")

    log.info("Agent answered after %s step(s): %s", steps, (reply.text or "")[:200])
    return Result(True, reply.text or "Done.", detail="\n".join(details))
