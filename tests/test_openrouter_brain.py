import io
import json
import urllib.error

import pytest

import brain
from brain import openrouter
from brain.base import BrainError, ToolCall
from brain.hybrid import HybridProvider
from modules import tool_registry

KEY = "sk-or-v1-" + "a" * 40


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener(*answers):
    """Plays the answers in order: a dict is a JSON reply, an int is an HTTP error code."""
    sent = []

    def open_(request, timeout=None):
        sent.append(request)
        answer = answers[len(sent) - 1]
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "error", {}, io.BytesIO(b"{}"))
        return FakeResponse(json.dumps(answer).encode())
    open_.sent = sent
    return open_


def message(text="", calls=()):
    return {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": list(calls) or None}}]}


def provider(*answers, **config):
    return openrouter.OpenRouterProvider({"OPENROUTER_API_KEY": KEY, **config}, opener=opener(*answers))


def test_a_plain_answer_comes_back_and_the_key_goes_only_in_the_header():
    p = provider(message("Hello Commander."))
    reply = p.session("You are Max.", []).send("hi")
    assert reply.text == "Hello Commander." and reply.calls == []
    request = p._open.sent[0]
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert KEY not in request.data.decode()


def test_tool_calls_are_read_and_their_results_go_back_with_the_call_id():
    call = {"id": "c1", "type": "function", "function": {"name": "open_app", "arguments": '{"name": "chrome"}'}}
    p = provider(message(calls=[call]), message("Chrome is open."))
    tools = [t for t in tool_registry.all_tools() if t.name == "open_app"]
    session = p.session("You are Max.", tools)
    reply = session.send("open chrome")
    assert reply.calls == [ToolCall("open_app", {"name": "chrome"}, "c1")]
    body = json.loads(p._open.sent[0].data)
    assert body["tools"][0]["function"]["name"] == "open_app"
    assert session.send_results([(reply.calls[0], {"ok": True, "message": "Opened Chrome."})]).text == "Chrome is open."
    second = json.loads(p._open.sent[1].data)["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
    assert second[-2]["tool_calls"][0]["id"] == "c1"


def test_a_busy_or_used_up_model_hands_over_to_the_next():
    p = provider(429, message("From the second model."))
    assert p.session("x", []).send("hi").text == "From the second model."
    models = [json.loads(r.data)["model"] for r in p._open.sent]
    assert models == openrouter.DEFAULT_MODELS[:2]


def test_a_wrong_key_says_so_and_does_not_try_other_models():
    p = provider(401)
    with pytest.raises(BrainError, match="refused the API key"):
        p.session("x", []).send("hi")
    assert len(p._open.sent) == 1


def test_when_every_model_fails_the_error_is_plain():
    p = provider(503, 503)
    with pytest.raises(BrainError, match="OpenRouter can't answer"):
        p.session("x", []).send("hi")


def test_the_models_can_be_chosen_in_config():
    p = provider(message("ok"), OPENROUTER_MODEL="some/model:free")
    p.session("x", []).send("hi")
    assert json.loads(p._open.sent[0].data)["model"] == "some/model:free"
    assert openrouter.OpenRouterProvider({"OPENROUTER_MODELS": ["a/b"]}).models == ["a/b"]


def test_the_chain_is_gemini_then_openrouter_then_the_server():
    config = {"GEMINI_API_KEY": "AIza" + "x" * 35, "OPENROUTER_API_KEY": KEY}
    chain = brain.get_provider(config)
    assert chain.name == "gemini+openrouter+server"
    assert chain.primary.name == "gemini" and chain.backup.name == "openrouter+server"
    only_openrouter = brain.get_provider({"OPENROUTER_API_KEY": KEY})
    assert only_openrouter.name == "openrouter+server"
    assert brain.get_provider({"OPENROUTER_API_KEY": KEY, "SERVER_BRAIN": False}).name == "openrouter"
    assert brain.get_provider({}) is None
    assert brain.get_provider({"GEMINI_API_KEY": "AIza" + "x" * 35}).name == "gemini+server"


def test_the_inner_switch_names_openrouter_not_gemini(capsys):
    class Down:
        name = "openrouter"
        can_look = False

        def session(self, prompt, tools):
            class S:
                def send(self, text):
                    raise BrainError("OpenRouter can't answer right now.")

                def reset(self):
                    pass
            return S()

    class Up:
        name = "server"
        can_look = False

        def session(self, prompt, tools):
            class S:
                def send(self, text):
                    from brain.base import Reply
                    return Reply(text="From the server.")

                def reset(self):
                    pass
            return S()

    hybrid = HybridProvider(Down(), Up())
    assert hybrid.session("x", []).send("hi").text == "From the server."
    out = capsys.readouterr().out
    assert "OpenRouter can't answer right now, so the KOVA server will" in out


def test_the_footer_names_the_whole_chain():
    from ui.web_app import brain_footer

    class Agent:
        provider = HybridProvider(openrouter.OpenRouterProvider({"OPENROUTER_API_KEY": KEY}),
                                  brain.get_provider({"BRAIN_PROVIDER": "server"}))

    assert brain_footer(Agent()) == "Brain: OpenRouter, with the KOVA server as backup"


def test_the_openrouter_key_box_checks_saves_and_never_echoes(monkeypatch):
    from ui import web_app
    from tests.test_web_6_memory_settings import make_loaded_dashboard
    monkeypatch.setattr(web_app, "use_new_brain", lambda agent: None)
    dashboard, window, controller = make_loaded_dashboard()
    try:
        assert dashboard.api.set_openrouter_key("AIzaNotAnOpenRouterKey1234567890123")["ok"] is False
        answer = dashboard.api.set_openrouter_key(KEY)
        assert answer["ok"] and KEY not in json.dumps(answer)
        assert dashboard.agent.config["OPENROUTER_API_KEY"] == KEY
        assert dashboard.api.settings()["BRAIN"]["openrouter_key"] is True
        assert KEY not in json.dumps(dashboard.api.settings())
    finally:
        dashboard.destroy()
