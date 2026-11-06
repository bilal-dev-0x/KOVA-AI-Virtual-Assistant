from brain.base import BrainError, Provider, Reply, Session, ToolCall

PLACEHOLDER_KEYS = {"", "PASTE_YOUR_GEMINI_API_KEY_HERE"}


def is_available(config):
    return config.get("GEMINI_API_KEY", "") not in PLACEHOLDER_KEYS


def get_provider(config):
    """The AI provider from config.json, or None (Max then runs local commands only).

    BRAIN_PROVIDER:
      "gemini" - Gemini first, the KOVA server when Gemini can't answer (SERVER_BRAIN: false = Gemini only)
      "server" - only the KOVA server (Ollama on the old laptop)
      "relay"  - Claude Code answers from a chat session, for testing
    """
    choice = config.get("BRAIN_PROVIDER", "gemini")
    if choice == "relay":
        from brain.relay import RelayProvider
        return RelayProvider(config)
    if choice == "server":
        from brain.server import ServerProvider
        return ServerProvider(config)
    if choice == "gemini" and is_available(config):
        from brain.gemini import GeminiProvider
        gemini = GeminiProvider(config)
        if config.get("SERVER_BRAIN", True) is False:
            return gemini
        from brain.hybrid import HybridProvider
        from brain.server import ServerProvider
        return HybridProvider(gemini, ServerProvider(config))
    return None


__all__ = ["BrainError", "Provider", "Reply", "Session", "ToolCall", "get_provider", "is_available"]
