from dataclasses import dataclass, field

# Every AI backend (Gemini now, a local Ollama model later) implements these
# two classes, so the agent loop never knows which one it is talking to.


class BrainError(Exception):
    """The AI could not be reached or refused the request; message is safe to show."""


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str = None


@dataclass
class Reply:
    text: str = ""
    calls: list = field(default_factory=list)  # ToolCalls the AI wants run, in order


class Session:
    """One ongoing conversation that remembers earlier turns."""

    def send(self, text) -> Reply:
        raise NotImplementedError

    def send_results(self, results) -> Reply:
        """results: list of (ToolCall, dict) answering every call in the last Reply."""
        raise NotImplementedError

    def reset(self):
        """Forget the conversation (also used after an interrupted plan)."""


class Provider:
    name = "none"

    def session(self, system_prompt, tools) -> Session:
        raise NotImplementedError

    def search(self, query):
        """A short answer from the live web, or None if this provider can't search."""
        return None
