import json
import os

from modules.logger import get_logger
from modules.tool_registry import BASE_DIR, CRITICAL, SAFE

# permissions.json lets you loosen or tighten the defaults per tool:
#   "always_allow": controlled tools that may run without asking (critical ones still ask)
#   "blocked":      tools Max must never run, at any level

PERMISSIONS_FILE = os.path.join(BASE_DIR, "permissions.json")

log = get_logger()


class PermissionManager:
    def __init__(self, path=None):
        self.always_allow = set()
        self.blocked = set()
        path = path or PERMISSIONS_FILE
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.always_allow = set(data.get("always_allow", []))
            self.blocked = set(data.get("blocked", []))
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as e:
            # A broken file must not silently allow more, so everything keeps its default level
            print("permissions.json is not valid JSON, using the defaults:", e)
            log.error("permissions.json is not valid JSON: %s", e)

    def is_blocked(self, tool):
        return tool.name in self.blocked

    def needs_approval(self, tool):
        if tool.level == SAFE:
            return False
        if tool.level == CRITICAL:
            return True
        return tool.name not in self.always_allow

    def asks_every_time(self, tool):
        """Critical steps are confirmed one by one, right before they run."""
        return tool.level == CRITICAL
