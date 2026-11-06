import json
import os
import time
import uuid
from pathlib import Path

from brain.base import LOOK_PROMPT, BrainError, Provider, Reply, Session, ToolCall
from modules.logger import get_logger

# A testing brain: Max writes each request to logs/relay/request.json and waits for
# Claude Code (watching that folder in a chat session) to write response.json.
# Replies still go through Max's tool registry and permission checks, like Gemini's.
#
# response.json: {"id": "<request id>", "text": "...", "calls": [{"name": "...", "args": {...}}]}
#
# The watcher touches logs/relay/watching every second or so. When that file is old,
# nobody is watching, so Max says so at once instead of going deaf for RELAY_TIMEOUT.

BASE_DIR = Path(__file__).resolve().parent.parent
RELAY_DIR = BASE_DIR / "logs" / "relay"
POLL_SECONDS = 0.3
HEARTBEAT_SECONDS = 15
NOT_WATCHING = "Claude isn't watching right now, so I can't answer that. Local commands still work."

log = get_logger()


def _write_json(path, data):
    """Write the whole file at once, so the reader never sees half of it."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class RelayProvider(Provider):
    name = "relay"
    can_look = True  # Claude Code reads the picture file

    def __init__(self, config, folder=None):
        self.folder = Path(folder or RELAY_DIR)
        self.timeout = config.get("RELAY_TIMEOUT", 180)
        # A screenshot left behind when Max was closed in the middle of a look
        (self.folder / "screen.jpg").unlink(missing_ok=True)
        # Voice mode sets this: it listens briefly and returns True if the user said "cancel"
        self.while_waiting = None

    def watching(self):
        """True if the watcher touched its heartbeat file in the last HEARTBEAT_SECONDS."""
        try:
            return time.time() - (self.folder / "watching").stat().st_mtime < HEARTBEAT_SECONDS
        except OSError:
            return False

    def session(self, system_prompt, tools):
        return RelaySession(self, system_prompt, tools)

    def ask(self, request):
        """Send one request and wait for its answer."""
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.watching():
            raise BrainError(NOT_WATCHING)
        request_file = self.folder / "request.json"
        response_file = self.folder / "response.json"
        response_file.unlink(missing_ok=True)

        request["id"] = uuid.uuid4().hex[:8]
        _write_json(request_file, request)
        print("⏳ Waiting for Claude to answer..." + (" Say cancel to stop waiting." if self.while_waiting else ""))
        deadline = time.time() + self.timeout
        try:
            while time.time() < deadline:
                answer = self._read(response_file, request["id"])
                if answer is not None:
                    return answer
                if not self.watching():
                    raise BrainError("Claude stopped watching, so I gave up on that one.")
                if self.while_waiting:
                    if self.while_waiting():
                        log.info("Relay wait cancelled by the user")
                        raise BrainError("Okay, I stopped waiting.")
                else:
                    time.sleep(POLL_SECONDS)
        finally:
            request_file.unlink(missing_ok=True)
        raise BrainError("Claude didn't answer in time. Is the chat session watching?")

    def look(self, image, question, context=""):
        """The picture goes to logs/relay/screen.jpg for Claude to read, and is deleted
        as soon as the answer is in (or the wait ends)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        picture = self.folder / "screen.jpg"
        picture.write_bytes(image)
        try:
            answer = self.ask({"look": {"image": str(picture), "question": question, "context": context,
                                        "instructions": LOOK_PROMPT}})
        finally:
            picture.unlink(missing_ok=True)
        return str(answer.get("text") or "").strip()

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
