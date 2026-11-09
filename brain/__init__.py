from brain.base import BrainError, Provider, Reply, Session, ToolCall

PLACEHOLDER_KEYS = {"", "PASTE_YOUR_GEMINI_API_KEY_HERE"}


def is_available(config):
    return config.get("GEMINI_API_KEY", "") not in PLACEHOLDER_KEYS


def get_provider(config):
    """The AI provider from config.json, or None (Max then runs local commands only).

    BRAIN_PROVIDER:
      "gemini" - Gemini first, then OpenRouter (when OPENROUTER_API_KEY is set), then the KOVA server
                 when the others can't answer (SERVER_BRAIN: false = no server)
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
    if choice == "gemini":
        # Gemini, then OpenRouter, then the KOVA server: each one that is set up joins the chain
        from brain import openrouter
        chain = []
        if is_available(config):
            from brain.gemini import GeminiProvider
            chain.append(GeminiProvider(config))
        if openrouter.is_available(config):
            chain.append(openrouter.OpenRouterProvider(config))
        if not chain:
            return None
        if config.get("SERVER_BRAIN", True) is not False:
            from brain.server import ServerProvider
            chain.append(ServerProvider(config))
        from brain.hybrid import HybridProvider
        provider = chain[-1]
        for brain in reversed(chain[:-1]):
            provider = HybridProvider(brain, provider)
        return provider
    return None


__all__ = ["BrainError", "Provider", "Reply", "Session", "ToolCall", "get_provider", "is_available"]
