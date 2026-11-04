from brain.base import BrainError, Provider, Reply, Session, ToolCall

PLACEHOLDER_KEYS = {"", "PASTE_YOUR_GEMINI_API_KEY_HERE"}


def is_available(config):
    return config.get("GEMINI_API_KEY", "") not in PLACEHOLDER_KEYS


def get_provider(config):
    """The AI provider from config.json, or None (Max then runs local commands only).

    BRAIN_PROVIDER: "gemini", or "relay" (Claude Code answers from a chat session, for testing).
    A local Ollama model plugs in here later.
    """
    choice = config.get("BRAIN_PROVIDER", "gemini")
    if choice == "relay":
        from brain.relay import RelayProvider
        return RelayProvider(config)
    if choice == "gemini" and is_available(config):
        from brain.gemini import GeminiProvider
        return GeminiProvider(config)
    return None


__all__ = ["BrainError", "Provider", "Reply", "Session", "ToolCall", "get_provider", "is_available"]
