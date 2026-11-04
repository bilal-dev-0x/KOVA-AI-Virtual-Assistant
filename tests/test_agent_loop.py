import json

import pytest

import desktop_agent
from brain.base import BrainError, Reply, ToolCall
from desktop_agent import Agent, needs_agent
from modules import actions, agent_loop, performance_monitor, permission_manager, system_control
from modules.result import Result
from tests.conftest import FakeProvider, FakeSession


@pytest.fixture
def apps(monkeypatch):
    """Pretend every app exists and is running; record what gets opened and closed."""
    log = {"opened": [], "closed": []}
    monkeypatch.setattr(system_control, "check_app", lambda name, must_be_running=False: (name, None))
    monkeypatch.setattr(system_control, "app_memory_mb", lambda name: 0 if name in log["closed"] else 300)
    monkeypatch.setattr(system_control, "close_app",
                        lambda name: log["closed"].append(name) or Result(True, f"Closed {name}"))
    monkeypatch.setattr(system_control, "open_target",
                        lambda name, config=None: log["opened"].append(name) or Result(True, f"Opening {name}"))
    return log


def make_agent(replies, confirm=lambda q: True, config=None):
    provider = FakeProvider(replies)
    questions = []
    agent = Agent({"AGENT_TRACE": False, **(config or {})}, provider=provider,
                  confirm=lambda q: questions.append(q) or confirm(q))
    return agent, provider.fake_session, questions


def calls(*items):
    return Reply(calls=[ToolCall(name, args) for name, args in items])


# ---------- the loop ----------

def test_tool_result_goes_back_to_the_ai(monkeypatch):
    monkeypatch.setattr(performance_monitor, "cpu_percent", lambda interval=1.0: 7)
    agent, session, questions = make_agent([calls(("cpu_usage", {})), Reply(text="CPU is at 7 percent.")])

    result = agent.handle("how busy is my processor right now compared to normal")
    assert result.message == "CPU is at 7 percent."
    (call, answer), = session.sent[1]
    assert call.name == "cpu_usage"
    assert answer == {"ok": True, "message": "CPU usage is 7 percent."}
    assert questions == []  # safe tools never ask


def test_controlled_steps_share_one_approval(apps):
    agent, session, questions = make_agent([
        calls(("close_app", {"name": "discord"}), ("close_app", {"name": "chrome"})),
        Reply(text="Closed both."),
    ])
    agent.handle("close discord and chrome")
    assert questions == ["Should I close discord and close chrome?"]
    assert apps["closed"] == ["discord", "chrome"]
    answers = [answer for _, answer in session.sent[1]]
    assert all(a["ok"] for a in answers)
    assert "freeing about 300 MB" in answers[0]["message"]


def test_declined_plan_runs_nothing(apps):
    agent, session, _ = make_agent([calls(("close_app", {"name": "discord"})), Reply(text="Okay.")],
                                   confirm=lambda q: False)
    agent.handle("close discord and steam")
    assert apps["closed"] == []
    (_, answer), = session.sent[1]
    assert answer["ok"] is False and "said no" in answer["message"]


def test_critical_step_asks_separately_even_if_always_allowed(monkeypatch, tmp_path):
    (tmp_path / "p.json").write_text(json.dumps({"always_allow": ["end_process", "close_app"]}))
    monkeypatch.setattr(permission_manager, "PERMISSIONS_FILE", str(tmp_path / "p.json"))
    monkeypatch.setattr(system_control, "check_process", lambda name: None)
    ended = []
    monkeypatch.setattr(system_control, "end_process", lambda name: ended.append(name) or Result(True, "Ended"))

    agent, _, questions = make_agent([calls(("end_process", {"process_name": "game.exe"})), Reply(text="Done.")],
                                     confirm=lambda q: False)
    agent.handle("end the game process and tell me")
    assert questions == ["This needs extra care. Should I really end the process game.exe?"]
    assert ended == []


def test_always_allow_skips_the_question(monkeypatch, tmp_path, apps):
    (tmp_path / "p.json").write_text(json.dumps({"always_allow": ["close_app"]}))
    monkeypatch.setattr(permission_manager, "PERMISSIONS_FILE", str(tmp_path / "p.json"))
    agent, _, questions = make_agent([calls(("close_app", {"name": "discord"})), Reply(text="Done.")])
    agent.handle("close discord and tell me")
    assert questions == []
    assert apps["closed"] == ["discord"]


def test_blocked_tool_never_runs(monkeypatch, tmp_path, apps):
    (tmp_path / "p.json").write_text(json.dumps({"blocked": ["open_app"]}))
    monkeypatch.setattr(permission_manager, "PERMISSIONS_FILE", str(tmp_path / "p.json"))
    agent, session, _ = make_agent([calls(("open_app", {"name": "chrome"})), Reply(text="I can't.")])
    agent.handle("open chrome and youtube")
    assert apps["opened"] == []
    assert "blocked" in session.sent[1][0][1]["message"]


def test_blocked_tool_never_runs_for_a_local_command(monkeypatch, tmp_path, apps):
    """A spoken command must obey permissions.json, not only an AI request does."""
    (tmp_path / "p.json").write_text(json.dumps({"blocked": ["open_app"]}))
    monkeypatch.setattr(permission_manager, "PERMISSIONS_FILE", str(tmp_path / "p.json"))
    agent = Agent({"AGENT_TRACE": False})
    result = agent.handle("open chrome")
    assert not result.success and "blocked" in result.message
    assert apps["opened"] == []


class ExplodingSession(FakeSession):
    """A provider that fails in a way BrainError does not describe."""

    def send(self, text):
        raise ValueError("malformed reply")


class ExplodingProvider(FakeProvider):
    def session(self, system_prompt, tools):
        self.system_prompt = system_prompt
        self.tools = tools
        return ExplodingSession([])


@pytest.mark.parametrize("text", [
    "open chrome and youtube",   # needs_agent() path
    "some unknown waffle",       # _fallback() path
])
def test_a_broken_reply_does_not_kill_the_assistant(text, apps):
    agent = Agent({"AGENT_TRACE": False}, provider=ExplodingProvider())
    result = agent.handle(text)
    assert not result.success
    assert "try again" in result.message.lower()
    assert apps["opened"] == []
    # the half-finished conversation is dropped, so the next request starts clean
    assert agent.session.resets == 1


@pytest.mark.parametrize("call, error", [
    (("format_disk", {}), "no tool called format_disk"),
    (("close_app", {}), "missing argument: name"),
    (("open_app", {"name": "x", "cmd": "del"}), "unknown argument"),
])
def test_bad_calls_from_the_ai_are_refused(call, error, apps):
    agent, session, questions = make_agent([calls(call), Reply(text="Sorry.")])
    agent.handle("do something complicated and risky")
    assert error in session.sent[1][0][1]["message"]
    assert apps["opened"] == apps["closed"] == [] and questions == []


def test_check_runs_before_asking(monkeypatch):
    monkeypatch.setattr(system_control, "check_app",
                        lambda name, must_be_running=False: (None, Result(True, f"{name} is not running.")))
    agent, session, questions = make_agent([calls(("close_app", {"name": "steam"})), Reply(text="It's closed.")])
    agent.handle("close steam if it is open")
    assert questions == []
    assert session.sent[1][0][1]["message"] == "steam is not running."


def test_step_limit(monkeypatch):
    monkeypatch.setattr(performance_monitor, "cpu_percent", lambda interval=1.0: 1)
    loop = [calls(("cpu_usage", {}))] * 20
    agent, session, _ = make_agent(loop)
    result = agent.handle("keep checking cpu forever and ever")
    assert "stopped after 6 steps" in result.message
    assert len(session.sent) == 7  # the request plus 6 rounds of results
    assert session.resets == 1


def test_brain_error_is_reported():
    class Broken(FakeProvider):
        def session(self, system_prompt, tools):
            session = super().session(system_prompt, tools)
            session.send = lambda text: (_ for _ in ()).throw(BrainError("I couldn't reach Gemini right now."))
            return session

    agent = Agent({"AGENT_TRACE": False}, provider=Broken())
    assert agent.handle("explain how rainbows form").message == "I couldn't reach Gemini right now."


def test_actions_are_logged(apps):
    agent, _, _ = make_agent([calls(("close_app", {"name": "discord"})), Reply(text="Done.")])
    agent.handle("close discord and tell me")
    with open(actions.ACTIONS_LOG, encoding="utf-8") as f:
        entry = json.loads(f.readlines()[-1])
    assert entry["tool"] == "close_app" and entry["args"] == {"name": "discord"}
    assert entry["approval"] == "approved" and entry["source"] == "agent" and entry["success"]


def test_trace_shows_steps(monkeypatch):
    monkeypatch.setattr(performance_monitor, "cpu_percent", lambda interval=1.0: 3)
    lines = []
    agent, _, _ = make_agent([calls(("cpu_usage", {})), Reply(text="Fine.")], config={"AGENT_TRACE": True})
    agent.trace = lines.append
    agent.handle("is my cpu fine or is something wrong with it")
    assert lines == ["  → cpu_usage()", "  ← CPU usage is 3 percent."]


def test_system_prompt_forbids_made_up_actions():
    prompt = agent_loop.system_prompt({"ASSISTANT_NAME": "Max", "USER_TITLE": "Boss"})
    assert "Max" in prompt and "Boss" in prompt
    assert "Only say you did something if its tool result is ok" in prompt


# ---------- hybrid routing ----------

@pytest.mark.parametrize("text, expected", [
    ("open chrome", False),
    ("volume up", False),
    ("how much ram am i using", False),
    ("open chrome and youtube", True),
    ("close whatever uses ram except vs code", True),
    ("my laptop feels slow can you figure out the reason", True),
])
def test_needs_agent(text, expected):
    assert needs_agent(text) is expected


@pytest.mark.parametrize("heard, expected", [
    ("I am saying close vs code", "close vs code"),
    ("ok again open the vs code", "open the vs code"),
    ("I told you open chrome", "open chrome"),
])
def test_lead_in_phrases_are_stripped(heard, expected):
    assert Agent().normalize(heard) == expected


def test_close_it_with_a_reason_goes_to_agent():
    assert needs_agent("close it so i can have my ram")


def test_long_plain_timer_stays_local():
    assert not needs_agent("set a timer of for 30 minutes 30 seconds")
    assert needs_agent("remind me in 5 minutes to call mom and dad")


def test_pronoun_followed_by_words_means_last_app(apps):
    agent = Agent({"AGENT_TRACE": False}, confirm=lambda q: True)  # no Gemini: stays local
    agent.handle("open notepad")
    agent.handle("close it so i can have my ram")
    assert apps["closed"] == ["notepad"]


@pytest.mark.parametrize("text", ["close yourself", "turn yourself off", "close max", "shut yourself down"])
def test_close_yourself_exits(text):
    assert Agent({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}).handle(text).stop is True


@pytest.mark.parametrize("text, arg", [
    ("how much cloth zooming my ram", "cloth"),
    ("how much cloud using my ram", "cloud"),
    ("how much ram is chrome using", "chrome"),
    ("how much memory does vs code use", "vs code"),
])
def test_program_memory_command(text, arg):
    command, matched = Agent().match(text)
    assert command.name == "program memory" and matched == arg


def test_total_ram_question_still_means_total():
    assert Agent().match("how much ram am i using")[0].name == "ram"


def test_cpu_and_ram_together_gives_status():
    assert Agent().match("tell me whats my ram cpu utilisation")[0].name == "cpu and ram"


def test_program_memory_finds_misheard_claude(monkeypatch):
    monkeypatch.setattr(performance_monitor, "_program_totals",
                        lambda: {"claude.exe": 1500.0, "wpscloudsvr.exe": 6.0, "chrome.exe": 6000.0})
    assert performance_monitor.program_memory("cloud").message == "claude is using 1.5 gigabytes of RAM."
    assert performance_monitor.program_memory("chrome.exe").message == "chrome is using 5.9 gigabytes of RAM."
    assert performance_monitor.program_memory("discord").message == "discord isn't running."
    assert not performance_monitor.program_memory("flux capacitor").success


def test_simple_command_stays_local_with_gemini(apps):
    agent, session, _ = make_agent([])
    assert agent.handle("open chrome").message == "Opening chrome"
    assert session.sent == []


def test_complex_request_goes_to_agent(apps):
    agent, session, _ = make_agent([
        calls(("open_app", {"name": "chrome"}), ("open_app", {"name": "youtube"})),
        Reply(text="Opened both."),
    ])
    assert agent.handle("open chrome and youtube").message == "Opened both."
    assert apps["opened"] == ["chrome", "youtube"]


def test_complex_request_without_gemini_still_tries_locally(apps):
    agent = Agent({"AGENT_TRACE": False})
    agent.handle("open chrome and youtube")
    assert apps["opened"] == ["chrome and youtube"]


def test_local_commands_are_shared_as_context(apps):
    agent, session, _ = make_agent([Reply(text="Sure.")])
    agent.handle("open discord")
    agent.handle("actually close it instead")
    assert "'open discord' -> Opening discord" in session.sent[0]
    assert session.sent[0].endswith("actually close it instead")


def test_pronoun_refers_to_last_app(apps):
    agent = Agent({"AGENT_TRACE": False}, confirm=lambda q: True)
    agent.handle("open discord")
    agent.handle("close it")
    assert apps["closed"] == ["discord"]


def test_news_uses_web_search():
    provider = FakeProvider(search_answer="Headline one. Headline two.")
    agent = Agent({"AGENT_TRACE": False}, provider=provider)
    assert agent.handle("news").message == "Headline one. Headline two."
    assert "pk" in provider.searched[0]


# ---------- undo ----------

def test_undo_open_closes_the_app(apps):
    agent = Agent({"AGENT_TRACE": False})
    agent.handle("open notepad")
    result = agent.handle("undo")
    assert apps["closed"] == ["notepad"]
    assert result.message.startswith("Undone.")


def test_undo_close_reopens(apps):
    agent = Agent({"AGENT_TRACE": False}, confirm=lambda q: True)
    agent.handle("close discord")
    agent.handle("undo that")
    assert apps["opened"] == ["discord"]


def test_undo_volume(monkeypatch):
    pressed = []
    monkeypatch.setattr(desktop_agent.media_keys, "press", lambda key, times=1: pressed.append((key, times)))
    agent = Agent({"AGENT_TRACE": False})
    agent.handle("volume up")
    agent.handle("undo")
    assert pressed == [("volume_up", 5), ("volume_down", 5)]


def test_nothing_to_undo():
    assert Agent().handle("undo").message == "There's nothing I can undo."
