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


LOOK_PROMPT = (
    "You are looking at a screenshot of the user's Windows PC. Answer their question about it in two to four "
    "short spoken sentences: plain text, no markdown, lists or emojis. If it shows an error, say what it means "
    "and the most likely fix. Read out only what the question needs; never read out passwords, codes or "
    "card numbers you happen to see. If the picture is too small or blurred to read, say so."
)


class Provider:
    name = "none"
    can_look = False  # True if look() can answer questions about the screen

    def session(self, system_prompt, tools) -> Session:
        raise NotImplementedError

    def search(self, query):
        """A short answer from the live web, or None if this provider can't search."""
        return None

    def look(self, image, question, context="", read=None):
        """Answer a question about one JPEG picture (bytes). context: earlier questions and
        answers about the same picture; read(): the picture's text, for a brain that can't see
        pictures. Raises BrainError when it can't."""
        raise BrainError("This brain can't look at pictures.")
