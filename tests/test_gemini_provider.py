import pytest

import brain
from brain import gemini
from brain.base import BrainError
from brain.budget import Budget

DAY = Exception("429 RESOURCE_EXHAUSTED. Generate content requests per day limit exceeded on Free Tier.")
MINUTE = Exception("429 RESOURCE_EXHAUSTED. Too many requests per minute.")
GONE = Exception("404 NOT_FOUND. This model is no longer available.")


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    p = gemini.GeminiProvider({"GEMINI_API_KEY": "test", "GEMINI_MODELS": ["a", "b", "c"]})
    p.budget = Budget(limit=100, path=str(tmp_path / "usage.json"))
    return p


def failing(errors):
    """A request that raises errors[model] or returns the model name."""
    tried = []

    def call(model):
        tried.append(model)
        if model in errors:
            raise errors[model]
        return model
    return call, tried


def test_switches_model_when_daily_quota_is_used_up(provider):
    call, tried = failing({"a": DAY, "b": GONE})
    assert provider.request(call) == "c"
    assert tried == ["a", "b", "c"]
    # The next request goes straight to the model that still works
    call, tried = failing({})
    provider.request(call)
    assert tried == ["c"]


def test_all_models_used_up(provider):
    call, _ = failing({"a": DAY, "b": DAY, "c": DAY})
    with pytest.raises(BrainError, match="run out of free requests"):
        provider.request(call)


BUSY = Exception("503 UNAVAILABLE. This model is currently experiencing high demand.")


def test_busy_model_cools_down_but_is_not_marked_used_up(provider, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(gemini.time, "time", lambda: clock[0])
    call, tried = failing({"a": MINUTE, "b": BUSY})
    assert provider.request(call) == "c"

    call, tried = failing({})
    provider.request(call)
    assert tried == ["c"]  # a and b are skipped while they cool down

    clock[0] += gemini.COOLDOWN_SECONDS + 1
    call, tried = failing({})
    provider.request(call)
    assert tried == ["a"]


def test_every_model_busy(provider):
    call, tried = failing({"a": MINUTE, "b": BUSY, "c": BUSY})
    with pytest.raises(BrainError, match="busy right now"):
        provider.request(call)
    assert tried == ["a", "b", "c"]


def test_network_error_is_retried_once(provider):
    call, tried = failing({"a": ConnectionError("offline")})
    with pytest.raises(BrainError, match="couldn't reach Gemini"):
        provider.request(call)
    assert tried == ["a", "a"]


def test_budget_limit(tmp_path):
    budget = Budget(limit=2, path=str(tmp_path / "u.json"))
    budget.spend()
    budget.spend()
    with pytest.raises(BrainError, match="today's limit of 2"):
        budget.spend()
    assert budget.used() == 2


def test_search_turns_off_after_grounding_is_refused(provider, monkeypatch):
    calls = []

    def refuse(call):
        calls.append(1)
        raise BrainError("Gemini is getting too many requests. Give me a minute.")
    monkeypatch.setattr(provider, "request", refuse)
    assert provider.search("weather") is None
    assert provider.search("weather again") is None
    assert len(calls) == 1


def test_no_key_means_no_provider():
    assert brain.get_provider({}) is None
    assert brain.get_provider({"GEMINI_API_KEY": "PASTE_YOUR_GEMINI_API_KEY_HERE"}) is None
    assert brain.get_provider({"GEMINI_API_KEY": "x", "BRAIN_PROVIDER": "ollama"}) is None
    assert isinstance(brain.get_provider({"GEMINI_API_KEY": "x"}), gemini.GeminiProvider)
