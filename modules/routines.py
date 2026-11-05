import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from modules import tool_registry
from modules.logger import get_logger
from modules.result import Result
from modules.tool_registry import BASE_DIR, CRITICAL, SAFE

# Routines: several tool calls run from one phrase ("coding mode").
# They live in routines.json (gitignored, so yours stay private); routines.json.example
# shows the format and is used until you make your own copy.
# Every step is checked when the file is loaded: an unknown tool or a bad argument is
# reported, never guessed.

ROUTINES_FILE = Path(BASE_DIR) / "routines.json"
EXAMPLE_FILE = Path(BASE_DIR) / "routines.json.example"

log = get_logger()

# "start coding mode", "run my morning routine", "coding mode please" all mean the routine
_LEAD = re.compile(r"^(start|run|begin|do|activate|switch to|go to|go into|enter|enable|turn on|lets do|lets start)"
                   r"( my| the| a)? ")
_TAIL = re.compile(r" (routine|mode|now)$")


def _core(phrase):
    phrase = " ".join(re.sub(r"[^\w\s]", " ", phrase.lower().replace("'", "")).split())
    phrase = _LEAD.sub("", phrase)
    while _TAIL.search(phrase):
        phrase = _TAIL.sub("", phrase)
    return phrase


@dataclass
class Step:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class Routine:
    name: str
    phrases: list
    steps: list

    def describe(self):
        """'open vs code, play lofi and switch the power plan to high performance'."""
        said = [tool_registry.get(s.tool).describe(s.args) for s in self.steps]
        return said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]


def parse(data):
    """(routines, problems) from the file's JSON. A routine with any bad step is left out."""
    routines, problems = [], []
    if not isinstance(data, dict) or not isinstance(data.get("routines"), list):
        return [], ['The file needs a "routines" list.']
    for i, item in enumerate(data["routines"], 1):
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        if not name:
            problems.append(f"Routine {i} has no name.")
            continue
        steps, bad = [], []
        for j, raw in enumerate(item.get("steps") or [], 1):
            tool = tool_registry.get(raw.get("tool", "")) if isinstance(raw, dict) else None
            if tool is None:
                named = raw.get("tool") if isinstance(raw, dict) else raw
                bad.append(f"step {j}: there is no tool called {named}")
                continue
            if tool.name == "run_routine":
                bad.append(f"step {j}: a routine can't run another routine")
                continue
            args, error = tool.validate(raw.get("args") or {})
            if error:
                bad.append(f"step {j} ({tool.name}): {error}")
                continue
            steps.append(Step(tool.name, args))
        if not steps and not bad:
            bad.append("it has no steps")
        if bad:
            problems.append(f"{name}: " + "; ".join(bad))
            continue
        phrases = [p for p in (item.get("phrases") or []) if isinstance(p, str)] + [name]
        routines.append(Routine(name, phrases, steps))
    return routines, problems


def load(path=None):
    """(routines, problems, the file used). routines.json if you have one, else the example."""
    for file in ([Path(path)] if path else [ROUTINES_FILE, EXAMPLE_FILE]):
        if not file.exists():
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("Could not read %s: %s", file.name, e)
            return [], [f"{file.name} is not valid JSON: {e}"], file
        routines, problems = parse(data)
        for problem in problems:
            log.warning("Routine skipped in %s: %s", file.name, problem)
        return routines, problems, file
    return [], [], None


_cache = {"key": None, "value": ([], [], None)}


def cached():
    """load(), read again only when routines.json (or the example) changes on disk."""
    key = tuple((str(f), f.stat().st_mtime if f.exists() else None) for f in (ROUTINES_FILE, EXAMPLE_FILE))
    if key != _cache["key"]:
        _cache["key"], _cache["value"] = key, load()
    return _cache["value"]


def find(routines, text):
    """The routine this sentence asks for, or None."""
    said = _core(text)
    if not said:
        return None
    for routine in routines:
        if said in {_core(p) for p in routine.phrases}:
            return routine
    return None


def needs_approval(routine, permissions):
    return any(permissions.needs_approval(tool_registry.get(s.tool)) for s in routine.steps)


def run(routine, runner, confirm, approved=False, source="routine"):
    """Run the steps in order with one approval for the whole routine.

    Critical steps still ask on their own, right before they run. The first step that
    fails stops the routine, and Max says which one and what already ran.
    """
    permissions = runner.permissions
    if not approved and needs_approval(routine, permissions):
        if not confirm(f"Should I run {routine.name}: {routine.describe()}?"):
            return Result(False, "Okay, I didn't run it.")
    done, lines = [], []
    for i, step in enumerate(routine.steps, 1):
        tool, args, problem = runner.prepare(step.tool, step.args)
        if problem is None and tool.level == CRITICAL:
            if not confirm(f"This needs extra care. Should I really {tool.describe(args)}?"):
                problem = Result(False, "you said no")
        if problem is None:
            approval = "not needed" if tool.level == SAFE else "routine approved"
            result = runner.run(tool, args, approval, source=source)
        else:
            result = problem
        lines.append(f"  {i}. {step.tool}: {result.message}")
        if not result.success:
            log.info("Routine %s stopped at step %s: %s", routine.name, i, result.message)
            ran = f" I already did: {', '.join(done)}." if done else " Nothing else ran."
            return Result(False, f"{routine.name} stopped at step {i}, {tool_registry.get(step.tool).describe(step.args)}: "
                                 f"{result.message.rstrip('.')}.{ran}", detail="\n".join(lines))
        done.append(tool_registry.get(step.tool).describe(step.args))
    log.info("Routine %s done (%s steps)", routine.name, len(routine.steps))
    return Result(True, f"{routine.name.capitalize()} is ready: {len(done)} step{'s' if len(done) > 1 else ''} done.",
                  detail="\n".join(lines))


def list_report(routines, problems=(), file=None):
    if not routines:
        message = "You don't have any routines yet. Copy routines.json.example to routines.json to make your own."
    else:
        names = [r.name for r in routines]
        message = (f"You have {len(names)} routine{'s' if len(names) > 1 else ''}: "
                   + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]) + ".")
    if problems:
        message += f" {len(problems)} couldn't be loaded; the details are printed."
    detail = "\n".join(f"  {r.name}: say '{r.phrases[0]}'. {r.describe()}." for r in routines)
    if problems:
        detail += "\n" + "\n".join(f"  Skipped: {p}" for p in problems)
    if file is not None:
        detail += f"\n  From {file.name}"
    return Result(True, message, detail=detail.strip("\n"))
