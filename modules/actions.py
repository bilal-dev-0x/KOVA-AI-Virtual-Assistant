import json
import os
from datetime import datetime

from modules import tool_registry
from modules.logger import LOG_DIR, get_logger
from modules.permission_manager import PermissionManager
from modules.result import Result

# Runs tools for both local commands and the AI agent: checks the arguments,
# records every call in logs/actions.jsonl and remembers what can be undone.

ACTIONS_LOG = os.path.join(LOG_DIR, "actions.jsonl")
UNDO_LIMIT = 10

log = get_logger()


class ActionRunner:
    def __init__(self, permissions=None, log_path=None):
        self.permissions = permissions or PermissionManager()
        self.log_path = log_path
        self.undo_stack = []      # (undo_text, undo) for the latest reversible actions
        self.last_target = None   # the app the last action was about, for "close it"

    def prepare(self, name, args):
        """Find and check a tool call before asking or running it.

        Returns (tool, clean_args, None), or (tool or None, None, Result) explaining
        why it can't run. Nothing is run here.
        """
        tool = tool_registry.get(name)
        if tool is None:
            return None, None, Result(False, f"There is no tool called {name}.")
        if self.permissions.is_blocked(tool):
            return tool, None, Result(False, f"{name} is blocked in permissions.json.")

        clean, error = tool.validate(args)
        if error:
            return tool, None, Result(False, f"Invalid arguments for {name}: {error}.")

        if tool.check:
            problem = tool.check(**clean)
            if problem:
                return tool, None, problem
        return tool, clean, None

    def run(self, tool, args, approval="not needed", source="local"):
        """Run an already-prepared tool call and log it."""
        try:
            result = tool.func(**args)
        except Exception as e:
            log.exception("Tool %s failed", tool.name)
            result = Result(False, f"Sorry, something went wrong with {tool.name.replace('_', ' ')}.",
                            detail=str(e))
        if isinstance(result, str):
            result = Result(True, result)

        if result.success:
            if result.undo:
                self.undo_stack = (self.undo_stack + [(result.undo_text, result.undo)])[-UNDO_LIMIT:]
            if "name" in args:
                self.last_target = args["name"]

        self.record(source, tool, args, approval, result)
        return result

    def undo_last(self):
        if not self.undo_stack:
            return Result(False, "There's nothing I can undo.")
        undo_text, undo = self.undo_stack.pop()
        try:
            result = undo()
        except Exception as e:
            log.exception("Undo failed: %s", undo_text)
            return Result(False, f"I couldn't {undo_text}.", detail=str(e))
        self.record("undo", None, {"undo": undo_text}, "user asked", result)
        return Result(result.success, f"Undone. {result.message}", detail=result.detail)

    def record(self, source, tool, args, approval, result):
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "tool": tool.name if tool else None,
            "level": tool.level if tool else None,
            "args": args,
            "approval": approval,
            "success": result.success,
            "message": result.message,
        }
        path = self.log_path or ACTIONS_LOG
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            log.error("Could not write the action log: %s", e)


def result_for_ai(result):
    """What the AI sees after a tool runs."""
    data = {"ok": result.success, "message": result.message}
    if result.detail:
        data["detail"] = result.detail
    return data
