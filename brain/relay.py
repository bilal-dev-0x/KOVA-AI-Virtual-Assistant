import json
import os
import time
import uuid
from pathlib import Path

from brain.base import BrainError, Provider, Reply, Session, ToolCall
from modules.logger import get_logger

# A testing brain: Max writes each request to logs/relay/request.json and waits for
# Claude Code (watching that folder in a chat session) to write response.json.
# Replies still go through Max's tool registry and permission checks, like Gemini's.
#
# response.json: {"id": "<request id>", "text": "...", "calls": [{"name": "...", "args": {...}}]}

BASE_DIR = Path(__file__).resolve().parent.parent
RELAY_DIR = BASE_DIR / "logs" / "relay"
POLL_SECONDS = 0.3

log = get_logger()


def _write_json(path, data):
    """Write the whole file at once, so the reader never sees half of it."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class RelayProvider(Provider):
    name = "relay"

    def __init__(self, config, folder=None):
        self.folder = Path(folder or RELAY_DIR)
        self.timeout = config.get("RELAY_TIMEOUT", 180)

    def session(self, system_prompt, tools):
        return RelaySession(self, system_prompt, tools)

    def ask(self, request):
        """Send one request and wait for its answer."""
        self.folder.mkdir(parents=True, exist_ok=True)
        request_file = self.folder / "request.json"
        response_file = self.folder / "response.json"
        response_file.unlink(missing_ok=True)

        request["id"] = uuid.uuid4().hex[:8]
        _write_json(request_file, request)
        print("⏳ Waiting for Claude to answer...")
        deadline = time.time() + self.timeout
        try:
            while time.time() < deadline:
                answer = self._read(response_file, request["id"])
                if answer is not None:
                    return answer
                time.sleep(POLL_SECONDS)
        finally:
            request_file.unlink(missing_ok=True)
        raise BrainError("Claude didn't answer in time. Is the chat session watching?")

    @staticmethod
    def _read(response_file, request_id):
        try:
            answer = json.loads(response_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # not written yet
        if answer.get("id") != request_id:
            return None  # an old answer
        response_file.unlink(missing_ok=True)
        return answer


def _reply(answer):
    calls = [ToolCall(c["name"], dict(c.get("args") or {}), f"call{i}")
             for i, c in enumerate(answer.get("calls") or []) if c.get("name")]
    return Reply(text=str(answer.get("text") or "").strip(), calls=calls)


class RelaySession(Session):
    def __init__(self, provider, system_prompt, tools):
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools
        self.new = True  # the first request carries the system prompt and tool list

    def _ask(self, request):
        if self.new:
            request["system"] = self.system_prompt
            request["tools"] = {t.name: t.description for t in self.tools}
            self.new = False
        log.info("Relay request: %s", request.get("user") or request.get("results"))
        return _reply(self.provider.ask(request))

    def send(self, text):
        return self._ask({"user": text})

    def send_results(self, results):
        return self._ask({"results": [{"name": call.name, "args": call.args, "result": result}
                                      for call, result in results]})

    def reset(self):
        self.new = True
