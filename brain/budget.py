import json
import os
from datetime import date

from brain.base import BrainError
from modules.logger import LOG_DIR, get_logger

# Counts Gemini requests per day so an agent loop can't quietly use up the free quota,
# and remembers which models already hit their daily limit today.
# GEMINI_DAILY_LIMIT in config.json sets Max's own limit (0 = no limit).

USAGE_FILE = os.path.join(LOG_DIR, "gemini_usage.json")
WARN_AT = 0.8

log = get_logger()


class Budget:
    def __init__(self, limit=100, path=None):
        self.limit = limit
        self.path = path or USAGE_FILE
        self.warned = False

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        if data.get("date") != date.today().isoformat():
            data = {"date": date.today().isoformat(), "requests": 0, "exhausted": []}
        data.setdefault("exhausted", [])
        return data

    def _save(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def used(self):
        return self._load()["requests"]

    def spend(self):
        """Count one request. Raises BrainError once today's limit is reached."""
        data = self._load()
        if self.limit and data["requests"] >= self.limit:
            raise BrainError(f"I've used today's limit of {self.limit} Gemini requests. "
                             "Local commands still work.")
        data["requests"] += 1
        self._save(data)

        if self.limit and not self.warned and data["requests"] >= self.limit * WARN_AT:
            self.warned = True
            print(f"⚠️ {data['requests']} of {self.limit} Gemini requests used today.")
            log.warning("Gemini budget: %s of %s requests used", data["requests"], self.limit)

    def is_exhausted(self, model):
        return model in self._load()["exhausted"]

    def mark_exhausted(self, model):
        data = self._load()
        if model not in data["exhausted"]:
            data["exhausted"].append(model)
            self._save(data)
