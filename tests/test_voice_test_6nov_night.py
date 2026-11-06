"""Fixes from Bilal's voice test of 6 Nov night (21:52-23:00), with both KOVA server models and the
screen read by OCR. Gemini was out for the day, so every AI answer came from the server."""
from pathlib import Path

import pytest

from brain import hybrid, server
from brain.base import Reply
from desktop_agent import Agent, fix_misheard, side_by_side_args, spelled_words
from modules import agent_loop, dev, files, system_control, voice_handler, windows
from modules.result import Result
from tests.conftest import FakeProvider
from tests.test_server_brain import call, ollama  # noqa: F401 (ollama is a fixture)

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"], "USER_TITLE": "Commander"}


def agent(provider=None, confirm=None):
    return Agent(dict(CONFIG), provider=provider, confirm=confirm)


# ---------- phrases that are local commands now ----------

@pytest.mark.parametrize("said, command", [
    ("kaise ho tum", "greet"),
    ("hey how are you doing", "greet"),
    ("what is your model name", "brain"),
    ("which brain are you using", "brain"),
    ("which window is in front", "front window"),
    ("are you in the valorant window", "front window"),
    ("tell me what is showing in the", "look at screen"),
    ("read the text which is shown on the screen", "look at screen"),
    ("what is consuming my ram", "top processes"),
    ("which is using my ram", "top processes"),
    ("which app is most using my laptop", "top processes"),
    ("meri raim jyada kaun istemal kar raha hai", "top processes"),
    ("govind gaming mode", "gaming mode"),
    ("turn off gaming mode", "done gaming"),
    ("open d whatsapp tab in existing chrome window", "switch tab"),
    ("switch to the gmail tab", "switch tab"),
    ("open it in vs code", "open project"),
    ("open first code asm with vs code", "open in vs code"),
    ("what about my rank", "ram"),
    ("now whats my rain usage", "ram"),
])
def test_phrases_from_the_voice_test(said, command):
    max_agent = agent()
    assert max_agent.match(fix_misheard(max_agent.normalize(said)))[0].name == command


def test_aligned_to_the_chrome_window():
    assert side_by_side_args("aligned to the chrome window to the left and cloud computing to the right") == \
        ("chrome", "cloud computing")


def test_spelled_names():
    assert spelled_words("I am saying minimise the lord window c l a u d e") == ["claude"]


# ---------- handled the way it was meant ----------

@pytest.fixture
def moved(monkeypatch):
    calls = []
    monkeypatch.setattr(windows, "move", lambda name, position, title="": calls.append((name, position))
                        or Result(True, "ok"))
    return calls


def test_no_in_front_of_a_command(moved):
    agent().handle("no minimise the cloud window")
    assert moved == [("cloud", "minimise")]


def test_the_spelled_name_is_the_name(moved):
    agent().handle("I am saying minimise the lord window c l a u d e")
    assert moved == [("claude", "minimise")]


def test_close_the_windows_means_the_window_in_front(monkeypatch):
    closed = []
    monkeypatch.setattr(windows, "target_window", lambda: (7, "file explorer", "Downloads"))
    monkeypatch.setattr(windows, "close_hwnd", lambda hwnd, app, title: closed.append(hwnd) or Result(True, "Closed."))
    asked = []
    result = agent(confirm=lambda q: asked.append(q) or True).handle("close the windows")
    assert result.success and closed == [7] and asked == ["Close the file explorer window Downloads?"]


def test_close_youtube_desktop_finds_its_window(monkeypatch):
    # A web app: chrome.exe shows it in a window titled "YouTube"; Max couldn't see it running
    max_agent = agent()
    monkeypatch.setattr(system_control, "check_app", lambda name, must_be_running=False: (
        None, Result(False, "I can't find YouTube Desktop running, so I can't close it.")))
    monkeypatch.setattr(system_control, "window_titled", lambda name: None)
    monkeypatch.setattr(windows, "find", lambda name, title="": (5, "youtube", "YouTube") if name == "youtube"
                        else (None, None, Result(False, "no")))
    monkeypatch.setattr(windows, "process_of", lambda hwnd: "chrome.exe")
    assert max_agent._prepare_close("youtube desktop") == ("chrome", None)
    assert max_agent.closing_title == ("chrome", "YouTube")  # Ctrl+W on it, never the whole browser


def test_greetings():
    assert agent().handle("kaise ho tum").message.startswith("Main theek hoon Commander")
    assert agent().handle("how are you doing").message == "I'm doing well, Commander. What can I do for you?"
    assert agent().handle("hello").message == "Hi Commander, how are you?"


def test_which_brain():
    class Gemini(FakeProvider):
        name = "gemini"
    provider = hybrid.HybridProvider(Gemini(), server.ServerProvider({}))
    assert agent(provider).handle("which brain are you using").message == (
        "Gemini answers right now, and the KOVA server (kova-chat for talk, kova-cmd for commands) is the backup.")
    provider._primary_back_at = float("inf")
    assert "Gemini is resting for now" in agent(provider).handle("what is your model name").message


def test_front_window(monkeypatch):
    monkeypatch.setattr(windows, "target_window", lambda: (3, "riot client", "Riot Client"))
    assert agent().handle("are you in the valorant window").message == "No, riot client, Riot Client is in front."
    assert agent().handle("which window is in front").message == "riot client, Riot Client is in front."


# ---------- the claim guard ----------

@pytest.mark.parametrize("answer, succeeded, verb", [
    ("Chrome and Edge have been closed. I'll force close Edge now.", {"close_app"}, None),
    ("I'll force close Edge now.", set(), "close"),
    ("The YouTube desktop window is open and showing a search for film.", set(), "see"),
    ("I see the Valorant window with the Play button.", set(), "see"),
    ("Chrome is open and showing a Google search.", {"ui_read"}, None),
    ("I see. Let me know what to do next.", set(), None),
])
def test_claims(answer, succeeded, verb):
    assert agent_loop.unbacked_claim(answer, succeeded) == verb


def test_describing_the_screen_without_looking_is_not_spoken():
    provider = FakeProvider([Reply(text="The YouTube window is open and showing a search for film.")])
    result = agent(provider).handle("its opened and showing in a task manager ok google")
    assert not result.success and "without looking at it" in result.message


# ---------- the KOVA server ----------

def test_kova_cmd_is_asked_again_after_saying_it_cant(ollama):
    ollama.replies = [{"role": "assistant", "content": "I can't press buttons or navigate within apps."},
                      {"role": "assistant", "content": "", "tool_calls": [call("ui_click", name="Play")]}]
    session = server.ServerProvider({"SERVER_CHAT_MODEL": ""}).session("You are KOVA.", [])
    session.messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "I can't do that."}]
    reply = session.send("press the play button in riot client")
    assert reply.calls[0].name == "ui_click"
    retried = ollama.requests[1][1]["messages"]
    assert [m["role"] for m in retried] == ["system", "user"]  # its old refusals are gone
    assert retried[1]["content"].endswith(server.RETRY_NOTE)


def test_kova_chat_never_sees_the_local_commands_note(ollama):
    ollama.replies = [{"role": "assistant", "content": "A venv keeps a project's packages apart."}]
    session = server.ServerProvider({}).session("You are KOVA.", [])
    session.send("(Commands the user ran without you since your last reply: 'minimise lord window claude' -> "
                 "I can't see it)\ntell me about python venv")
    asked = ollama.requests[0][1]["messages"][-1]["content"]
    assert asked == "tell me about python venv\n(Reply in English.)"


def test_kova_chat_is_told_the_language_of_the_latest_message(ollama):
    session = server.ServerProvider({}).session("You are KOVA.", [])
    session.send("yaar laptop garam hota hai kya karun")
    session.send("tell me a difference between i5 and i7")
    first, second = (body["messages"][-1]["content"] for _, body in ollama.requests)
    assert first.endswith("(Reply in Roman Urdu (Urdu words, not Hindi).)")
    assert second.endswith("(Reply in English.)")


@pytest.mark.parametrize("chat_says", [
    "The text you're referring to is on the Wikipedia page that's open in the Chrome window.",
    "It's shown on your screen in the top corner.",
])
def test_kova_chat_talking_about_the_screen_hands_over(ollama, chat_says):
    ollama.replies = [{"role": "assistant", "content": chat_says}, {"role": "assistant", "content": "From kova-cmd."}]
    session = server.ServerProvider({}).session("You are KOVA.", [])
    assert session.send("what does it say there").text == "From kova-cmd."


@pytest.mark.parametrize("said, talk", [
    ("read the text which is signed on which", False),
    ("are you in the valorant window", False),
    ("what is consuming virus", False),
    ("yaar mera laptop gaming karte waqt garam hota hai, kya karun?", True),
    ("tell me a difference between i5 and i7", True),
])
def test_screen_words_go_to_kova_cmd(said, talk):
    assert server.is_talk(said) is talk


def test_kova_chat_answers_in_the_latest_language():
    prompt = server.chat_prompt("You are Max. Address the user as 'Commander'.")
    assert "language of the user's latest message" in prompt and "never Hindi words" in prompt
    assert "you can't see the screen" in prompt


def test_the_server_gets_the_missing_tools():
    for name in ("gaming_mode", "done_gaming", "gpu_usage", "open_in_vscode", "switch_tab"):
        assert name in server.DEFAULT_TOOLS


# ---------- voice ----------

def test_a_cut_off_sentence_waits_for_the_rest():
    heard = []

    def listen(wait_seconds, max_seconds):
        heard.append(wait_seconds)
        return "i5 and i7"
    assert voice_handler.finish_cut_off("tell me the difference between", listen) == \
        "tell me the difference between i5 and i7"
    assert voice_handler.finish_cut_off("open chrome", listen) == "open chrome" and heard == [3]
    assert voice_handler.finish_cut_off("I want you", lambda **kw: None) == "I want you"


def test_unsure_speech_is_not_acted_on(monkeypatch):
    voice_handler.pick_language({"en-US": ("search and ud", 0.21), "en-IN": ("search and you'd", 0.18)}, "en-US")
    assert voice_handler.last_confidence == 0.21 and voice_handler.unsure()
    voice_handler.pick_language({"en-US": ("open chrome", 0.93)}, "en-US")
    assert not voice_handler.unsure()


# ---------- tabs and VS Code ----------

def test_switch_tab_steps_through_the_tabs(monkeypatch):
    titles = ["Gmail - Google Chrome", "YouTube - Google Chrome", "(3) WhatsApp - Google Chrome"]
    shown = {"i": 0}
    monkeypatch.setattr(system_control, "app_windows", lambda procs: [(9, titles[shown["i"]])])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda hwnd: True)
    monkeypatch.setattr(system_control, "_tab_count", lambda hwnd: 3)
    monkeypatch.setattr(system_control, "_window_title", lambda hwnd: titles[shown["i"]])
    monkeypatch.setattr(system_control, "_press_ctrl_tab", lambda: shown.update(i=(shown["i"] + 1) % 3))
    result = system_control.switch_tab("d whatsapp")
    assert result.success and shown["i"] == 2 and "WhatsApp" in result.message


def test_switch_tab_that_isnt_open(monkeypatch):
    monkeypatch.setattr(system_control, "app_windows", lambda procs: [(9, "Gmail - Google Chrome")])
    monkeypatch.setattr(system_control, "_bring_to_front", lambda hwnd: True)
    monkeypatch.setattr(system_control, "_tab_count", lambda hwnd: 1)
    monkeypatch.setattr(system_control, "_window_title", lambda hwnd: "Gmail - Google Chrome")
    monkeypatch.setattr(system_control, "_press_ctrl_tab", lambda: None)
    assert system_control.switch_tab("whatsapp").message == "I can't find a whatsapp tab in your browser."


def test_open_the_last_file_in_vs_code(monkeypatch, tmp_path):
    asm = Path(files.known_folder("documents")) / "Learning Assembly" / "first_code.asm"
    asm.parent.mkdir(parents=True, exist_ok=True)
    asm.write_text("mov ax, 1", encoding="utf-8")
    opened = []
    monkeypatch.setattr(dev, "_vscode", lambda: "Code.exe")
    monkeypatch.setattr(dev, "_popen", lambda args: opened.append(args))
    monkeypatch.setitem(files._last, "path", str(asm))
    assert dev.open_path_in_vscode("it").success and opened == [["Code.exe", str(asm)]]
    monkeypatch.setitem(files._last, "path", None)
    assert "which file you mean" in dev.open_path_in_vscode("it").message
