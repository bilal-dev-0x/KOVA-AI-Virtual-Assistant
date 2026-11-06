"""The KOVA server brain (Ollama on the old laptop) and Gemini-first-server-backup. A fake Ollama:
no request leaves this PC."""
import json

import pytest

from brain import hybrid, server
from brain.base import BrainError, Provider, Reply, Session, ToolCall
from desktop_agent import Agent
from modules import tool_registry
from tests.conftest import FakeProvider

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}
CABLE, TAILSCALE = server.DEFAULT_URLS


class FakeOllama:
    """Answers /api/chat with scripted messages and records every request."""

    def __init__(self, replies=(), up=(CABLE, TAILSCALE)):
        self.replies = list(replies)
        self.up = set(up)
        self.requests = []   # (url, body)

    def reachable(self, url):
        return url in self.up

    def urlopen(self, request, timeout=None):
        url = request.full_url.rsplit("/api/", 1)[0]
        if url not in self.up:
            raise OSError("no route")
        body = json.loads(request.data)
        self.requests.append((url, body))
        message = self.replies.pop(0) if self.replies else {"role": "assistant", "content": "Done."}
        return _Response({"message": message, "prompt_eval_count": 10, "eval_count": 5})


class _Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(server, "_reachable", fake.reachable)
    monkeypatch.setattr(server.urllib.request, "urlopen", fake.urlopen)
    return fake


def call(tool, /, **args):
    return {"function": {"name": tool, "arguments": args}}


# ---------- the KOVA server ----------

def test_reply_with_tool_calls():
    reply = server._reply({"content": "", "tool_calls": [call("close_app", name="discord"),
                                                         {"function": {"name": "open_app",
                                                                       "arguments": '{"name": "notepad"}'}}]})
    assert [(c.name, c.args) for c in reply.calls] == [("close_app", {"name": "discord"}),
                                                       ("open_app", {"name": "notepad"})]


def test_a_conversation_with_tools(ollama):
    ollama.replies = [{"role": "assistant", "content": "", "tool_calls": [call("get_datetime")]},
                      {"role": "assistant", "content": "It's 5 pm."}]
    provider = server.ServerProvider({})
    session = provider.session("You are KOVA.", tool_registry.all_tools())
    reply = session.send("what time is it")
    assert reply.calls[0].name == "get_datetime"
    assert session.send_results([(reply.calls[0], {"ok": True, "message": "5 pm"})]).text == "It's 5 pm."
    first, second = ollama.requests[0][1], ollama.requests[1][1]
    assert first["model"] == "kova-cmd" and first["think"] is False and first["stream"] is False
    assert first["messages"][0]["content"].startswith("You are KOVA. Rules for you: you cannot see the screen")
    assert second["messages"][-1]["role"] == "tool" and second["messages"][-1]["tool_name"] == "get_datetime"
    # Only the short list of tools is sent: every description is read with every request
    assert {t["function"]["name"] for t in first["tools"]} == set(server.DEFAULT_TOOLS) & \
        {t.name for t in tool_registry.all_tools()}


def test_your_own_tool_list_and_model():
    provider = server.ServerProvider({"SERVER_TOOLS": ["open_app"], "SERVER_MODEL": "kova-big"})
    session = provider.session("x", tool_registry.all_tools())
    assert [t.name for t in session.tools] == ["open_app"] and provider.model == "kova-big"


def test_tailscale_when_the_cable_is_out(ollama):
    ollama.up = {TAILSCALE}
    server.ServerProvider({}).session("x", []).send("hi")
    assert ollama.requests[0][0] == TAILSCALE


def test_server_off(ollama):
    ollama.up = set()
    with pytest.raises(BrainError, match="can't reach the KOVA server"):
        server.ServerProvider({}).session("x", []).send("hi")


def test_old_turns_are_dropped_at_a_user_message(ollama):
    session = server.ServerProvider({}).session("x", [])
    for i in range(server.MAX_MESSAGES):
        session.send(f"message {i}")
    assert len(session.messages) <= server.MAX_MESSAGES + 1
    assert session.messages[0]["role"] == "user"


# ---------- Gemini first, the KOVA server as the backup ----------

class DownSession(Session):
    def __init__(self, log):
        self.log = log

    def send(self, text):
        self.log.append(text)
        raise BrainError("Every Gemini model I can use has run out of free requests for today.")

    def reset(self):
        pass


class DownGemini(Provider):
    name = "gemini"
    can_look = True

    def __init__(self):
        self.tried = []

    def session(self, system_prompt, tools):
        return DownSession(self.tried)


def test_the_server_answers_when_gemini_cant(capsys):
    gemini, backup = DownGemini(), FakeProvider([Reply(text="From the KOVA server.")])
    agent = Agent(dict(CONFIG), provider=hybrid.HybridProvider(gemini, backup))
    assert agent.handle("tell me a joke about computers please").message == "From the KOVA server."
    assert "the KOVA server will" in capsys.readouterr().out


def test_gemini_is_rested_for_a_while_then_tried_again(monkeypatch):
    gemini, backup = DownGemini(), FakeProvider()
    provider = hybrid.HybridProvider(gemini, backup)
    session = provider.session("x", [])
    session.send("one")
    session.send("two")
    assert gemini.tried == ["one"]                       # not asked again during the rest
    monkeypatch.setattr(hybrid.time, "time", lambda: provider._primary_back_at + 1)
    session.send("three")
    assert gemini.tried == ["one", "three"]


def test_tool_results_go_back_to_the_brain_that_asked():
    gemini, backup = DownGemini(), FakeProvider([Reply(calls=[ToolCall("get_datetime", {})]), Reply(text="5 pm")])
    agent = Agent(dict(CONFIG), provider=hybrid.HybridProvider(gemini, backup))
    assert agent.handle("what time is it right now in my city and country").message == "5 pm"
    assert len(backup.fake_session.sent) == 2


def test_both_down_says_so(ollama):
    ollama.up = set()
    provider = hybrid.HybridProvider(DownGemini(), server.ServerProvider({}))
    with pytest.raises(BrainError, match="Gemini can't answer right now, and i can't reach the KOVA server"):
        provider.session("x", []).send("hi")


def test_without_ocr_the_screen_stays_with_gemini():
    provider = hybrid.HybridProvider(DownGemini(), server.ServerProvider({}))
    assert provider.can_look and not server.ServerProvider({}).can_look


# ---------- getting the server ready before Gemini runs out ----------

class Budget:
    def __init__(self, requests, limit=100, exhausted=()):
        self.limit, self.data = limit, {"requests": requests, "exhausted": list(exhausted)}

    def _load(self):
        return self.data


class LowGemini(FakeProvider):
    def __init__(self, requests, exhausted=()):
        super().__init__()
        self.budget = Budget(requests, exhausted=exhausted)
        self.models = ["a", "b", "c", "d"]


class WarmBackup(FakeProvider):
    def __init__(self):
        super().__init__()
        self.warmed = []

    def warm_up(self, system_prompt, tools):
        self.warmed.append(system_prompt)


def _wait_for(thread_name="warm-server"):
    import threading
    for thread in threading.enumerate():
        if thread.name == thread_name:
            thread.join(timeout=2)


@pytest.mark.parametrize("requests, exhausted, warms", [(50, (), False), (80, (), True), (10, ("a", "b"), True),
                                                     # already out: the falling-back request reads it itself
                                                     (100, (), False), (10, ("a", "b", "c", "d"), False)])
def test_the_server_is_warmed_once_when_gemini_runs_low(requests, exhausted, warms):
    backup = WarmBackup()
    provider = hybrid.HybridProvider(LowGemini(requests, exhausted), backup)
    session = provider.session("You are KOVA.", [])
    session.send("one")
    session.send("two")
    _wait_for()
    assert backup.warmed == (["You are KOVA."] if warms else [])


def test_warm_up_reads_the_prompt_and_writes_one_token(ollama):
    server.ServerProvider({}).warm_up("You are KOVA.", tool_registry.all_tools())
    body = ollama.requests[0][1]
    assert body["messages"] == [{"role": "system", "content": "You are KOVA." + server.SERVER_RULES}]
    assert body["options"] == {"num_predict": 1} and len(body["tools"]) == len(server.DEFAULT_TOOLS)


def test_a_fallback_waits_for_a_running_warm_up():
    import threading
    order = []
    release = threading.Event()

    class SlowWarm(WarmBackup):
        def warm_up(self, system_prompt, tools):
            release.wait(2)
            order.append("warmed")

        def session(self, system_prompt, tools):
            order.append("asked")
            return super().session(system_prompt, tools)

    gemini = LowGemini(80)
    provider = hybrid.HybridProvider(gemini, SlowWarm())
    session = provider.session("x", [])
    session.send("one")                       # starts the warm-up; Gemini answers
    provider._primary_back_at = float("inf")  # now Gemini is out
    threading.Timer(0.2, release.set).start()
    session.send("two")
    assert order == ["warmed", "asked"]


# ---------- two models on the server: kova-chat talks, kova-cmd acts ----------

@pytest.mark.parametrize("said, talk", [
    ("yaar mera laptop gaming karte waqt garam hota hai, kya karun?", True),
    ("explain what a python virtual environment is", True),
    ("tell me a joke about computers", True),
    ("(Known about the user, in their words: likes cricket)\nwho won the 1992 world cup", True),
    ("open chrome and search for cooling pads", False),
    ("what time is it", False),
    ("remind me at 6 to call ammi", False),
    ("how much ram am i using", False),
    ("(Known about the user: likes cricket)\nclose discord", False),
])
def test_what_counts_as_talk(said, talk):
    assert server.is_talk(said) is talk


def test_talk_goes_to_kova_chat_without_tools(ollama):
    ollama.replies = [{"role": "assistant", "content": "Laptop ko cooling pad par rakhein."}]
    session = server.ServerProvider({}).session("You are KOVA.", tool_registry.all_tools())
    assert session.send("laptop garam hota hai kya karun").text == "Laptop ko cooling pad par rakhein."
    body = ollama.requests[0][1]
    assert body["model"] == "kova-chat" and body["tools"] == []
    assert body["messages"][0]["content"] == server.CHAT_PROMPT.format(name="KOVA", title="Commander")
    assert session.messages[-1] == {"role": "assistant", "content": "Laptop ko cooling pad par rakhein."}


def test_actions_go_straight_to_kova_cmd(ollama):
    session = server.ServerProvider({}).session("You are KOVA.", tool_registry.all_tools())
    session.send("close discord")
    assert [b["model"] for _, b in ollama.requests] == ["kova-cmd"]


@pytest.mark.parametrize("chat_says", ["I need my tools for that.", "TOOLS python, virtualenv", "Main tool_joke tell karta hoon.", "I can't check that from here.", "I'll open it for you now.", ""])
def test_kova_chat_hands_over_to_kova_cmd(ollama, chat_says):
    ollama.replies = [{"role": "assistant", "content": chat_says},
                      {"role": "assistant", "content": "", "tool_calls": [call("get_datetime")]}]
    session = server.ServerProvider({}).session("You are KOVA.", tool_registry.all_tools())
    reply = session.send("is it a good day for a walk")
    assert reply.calls[0].name == "get_datetime"
    assert [b["model"] for _, b in ollama.requests] == ["kova-chat", "kova-cmd"]
    assert ollama.requests[1][1]["messages"][-1] == {"role": "user", "content": "is it a good day for a walk"}


def test_kova_chat_missing_means_kova_cmd_answers(ollama, monkeypatch):
    provider = server.ServerProvider({})
    real_chat = provider.chat

    def chat(messages, tools, options=None, model=None):
        if model == "kova-chat":
            raise BrainError("The KOVA server couldn't answer that.")
        return real_chat(messages, tools, options, model)
    monkeypatch.setattr(provider, "chat", chat)
    assert provider.session("x", []).send("tell me a joke").text == "Done."


def test_one_model_only_when_you_say_so(ollama):
    server.ServerProvider({"SERVER_CHAT_MODEL": ""}).session("x", []).send("tell me a joke")
    assert [b["model"] for _, b in ollama.requests] == ["kova-cmd"]


def test_warm_up_loads_both_models(ollama):
    server.ServerProvider({}).warm_up("You are KOVA.", tool_registry.all_tools())
    assert [(b["model"], b["options"]) for _, b in ollama.requests] == [("kova-cmd", {"num_predict": 1}),
                                                                        ("kova-chat", {"num_predict": 1})]


def test_kova_chat_is_called_by_maxs_name_and_your_title():
    prompt = server.chat_prompt("You are Max, an AI assistant. Address the user as 'Bilal bhai'. Use tools.")
    assert prompt.startswith("You are Max,") and "Call the user 'Bilal bhai'" in prompt
