"""Day 4 step 2: looking at the screen. Fake windows and fake pictures: no real screenshot is taken
and nothing is sent anywhere."""
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from brain import gemini
from brain.base import Reply, ToolCall
from brain.budget import Budget
from brain.relay import RelayProvider
from desktop_agent import Agent
from modules import screen, system_control, windows
from tests.conftest import FakeProvider

REAL_GRAB_WINDOW = screen._grab_window  # conftest replaces it in every test
CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}
LEFT, RIGHT = (0, 0, 1920, 1080), (1920, 0, 3840, 1080)


class LookProvider(FakeProvider):
    can_look = True

    def __init__(self, replies=(), answers=("It says the file was not found.",)):
        super().__init__(replies)
        self.answers = list(answers)
        self.looks = []  # (image, question, context)

    def look(self, image, question, context=""):
        self.looks.append((image, question, context))
        return self.answers.pop(0) if self.answers else "Another answer."


class Desk:
    """Fake windows (front first) and fake screenshots."""

    def __init__(self):
        self.win = []      # (hwnd, process, title, rect, minimised)
        self.grabs = []    # what was grabbed: ("window", hwnd) or ("screen", box)

    def add(self, hwnd, process, title, rect=(100, 100, 800, 600), minimised=False):
        self.win.append((hwnd, process, title, rect, minimised))

    def visible(self):
        return [(h, p, t) for h, p, t, _, _ in self.win]

    def info(self, hwnd):
        return next(w for w in self.win if w[0] == hwnd)

    def grab_window(self, hwnd, words=()):
        self.grabs.append(("window", hwnd))
        return Image.new("RGB", (2560, 1440), "white")

    def grab_screen(self, box=None):
        self.grabs.append(("screen", box))
        return Image.new("RGB", (1920, 1080), "white")


@pytest.fixture
def desk(monkeypatch):
    fake = Desk()
    monkeypatch.setattr(windows, "visible_windows", fake.visible)
    monkeypatch.setattr(windows, "_is_minimised", lambda h: fake.info(h)[4])
    monkeypatch.setattr(windows, "_get_rect", lambda h: fake.info(h)[3])
    monkeypatch.setattr(windows, "_monitor_rect", lambda h: RIGHT if h and fake.info(h)[3][0] >= 1920 else LEFT)
    monkeypatch.setattr(system_control, "app_windows",
                        lambda procs: [(h, t) for h, p, t, _, _ in fake.win if p in {x.lower() for x in procs}])
    monkeypatch.setattr(screen, "_grab_window", fake.grab_window)
    monkeypatch.setattr(screen, "_grab_screen", fake.grab_screen)
    return fake


def max_agent(provider, confirm=None, config=None):
    return Agent({**CONFIG, **(config or {})}, confirm=confirm, provider=provider)


# ---------- the picture ----------

def test_shrink_makes_a_small_jpeg_in_memory():
    data = screen.shrink(Image.new("RGB", (2560, 1440), "white"))
    assert data[:2] == b"\xff\xd8"  # JPEG
    import io
    assert Image.open(io.BytesIO(data)).size == (1280, 720)
    assert Image.open(io.BytesIO(screen.shrink(Image.new("RGB", (800, 600))))).size == (800, 600)


@pytest.mark.parametrize("title, word", [
    ("Meezan Bank - Google Chrome", "meezan"),
    ("HBL Mobile - Login", "hbl"),
    ("Bitwarden", "bitwarden"),
    ("New Incognito tab - Google Chrome", "incognito"),
    ("Allied Bank Limited", "allied bank"),
    ("Statement - Bank Alfalah", "alfalah"),
])
def test_private_windows(title, word):
    assert screen.blocked_word([title], screen.DEFAULT_BLOCKLIST) == word


@pytest.mark.parametrize("title", ["Bankai - Bleach wiki", "desktop_agent.py - Visual Studio Code", "Subscribe"])
def test_ordinary_windows_are_not_private(title):
    assert screen.blocked_word([title], screen.DEFAULT_BLOCKLIST) is None


# ---------- looking ----------

def test_whats_this_error_looks_at_the_window_in_front_not_maxs_terminal(desk, monkeypatch, capsys):
    desk.add(1, "windowsterminal.exe", "Max")
    desk.add(2, "chrome.exe", "404 Not Found - Google Chrome")
    monkeypatch.setattr(windows, "_own_windows", lambda: {1})
    provider = LookProvider()
    result = max_agent(provider).handle("what's this error?")
    assert result.success and result.message == "It says the file was not found."
    assert desk.grabs == [("window", 2)]
    image, question, context = provider.looks[0]
    assert image[:2] == b"\xff\xd8" and question == "what's this error?" and context == ""
    assert "📸 Looking at the chrome window, 404 Not Found" in capsys.readouterr().out


def test_without_a_brain_that_can_look(desk):
    desk.add(2, "chrome.exe", "Page")
    assert max_agent(None).handle("what's on my screen").message == screen.NO_BRAIN
    assert max_agent(FakeProvider()).handle("what's on my screen").message == screen.NO_BRAIN
    assert desk.grabs == []


def test_a_private_window_needs_look_anyway(desk):
    desk.add(2, "chrome.exe", "Meezan Bank - Google Chrome")
    provider = LookProvider()
    agent = max_agent(provider)
    result = agent.handle("summarise this page")
    assert not result.success and "looks private (meezan)" in result.message
    assert desk.grabs == [] and provider.looks == []
    assert agent.handle("look anyway").success
    assert desk.grabs == [("window", 2)]
    # Only once: the next look is checked again
    assert "private" in agent.handle("summarise this page").message


def test_a_chat_app_is_private_by_its_program_name(desk):
    desk.add(2, "whatsapp.exe", "(2) Family")
    assert "private (whatsapp)" in max_agent(LookProvider()).handle("what does this say").message


def test_look_anyway_with_nothing_waiting():
    assert max_agent(LookProvider()).handle("look anyway").message == "There's nothing waiting for me to look at."


def test_your_own_blocklist_in_config(desk):
    desk.add(2, "code.exe", "secretproject - Visual Studio Code")
    agent = max_agent(LookProvider(), config={"SCREEN_BLOCKLIST": ["secretproject"]})
    assert "private (secretproject)" in agent.handle("explain this code").message


def test_a_follow_up_reuses_the_same_picture(desk):
    desk.add(2, "chrome.exe", "Error - Google Chrome")
    provider = LookProvider(answers=["A missing file.", "Reinstall it."])
    agent = max_agent(provider)
    agent.handle("what's this error")
    result = agent.handle("and how do I fix it?")
    assert result.message == "Reinstall it."
    assert len(desk.grabs) == 1                      # no second screenshot
    assert provider.looks[1][0] is provider.looks[0][0]
    assert "Q: what's this error\nA: A missing file." in provider.looks[1][2]


def test_a_follow_up_after_a_minute_is_an_ordinary_request(desk):
    desk.add(2, "chrome.exe", "Error - Google Chrome")
    provider = LookProvider(replies=[Reply(text="From the brain.")])
    agent = max_agent(provider)
    agent.handle("what's this error")
    screen._last["time"] = time.time() - screen.KEEP_SECONDS - 1
    assert agent.handle("how do I fix it").message == "From the brain."
    assert len(desk.grabs) == 1


def test_same_picture_when_it_is_gone_takes_a_new_one(desk):
    desk.add(2, "chrome.exe", "Error - Google Chrome")
    provider = LookProvider()
    screen.look("and now?", same_picture=True, provider=provider)
    assert desk.grabs == [("window", 2)]


def test_the_picture_is_forgotten_by_a_timer(desk, monkeypatch):
    desk.add(2, "chrome.exe", "Error - Google Chrome")
    monkeypatch.setattr(screen, "KEEP_SECONDS", 0.05)
    screen.look("what is it", provider=LookProvider())
    assert screen._last["image"] is not None
    time.sleep(0.3)
    assert screen._last["image"] is None


def test_my_screen_is_the_monitor_of_the_window_in_front(desk):
    desk.add(2, "chrome.exe", "Docs - Google Chrome", rect=(2000, 0, 1200, 900))
    desk.add(3, "whatsapp.exe", "WhatsApp", rect=(10, 10, 600, 800))  # on the other monitor
    assert max_agent(LookProvider()).handle("what's on my screen").success
    assert desk.grabs == [("screen", RIGHT)]


def test_a_private_window_on_that_monitor_blocks_it(desk):
    desk.add(2, "chrome.exe", "Docs - Google Chrome")
    desk.add(3, "telegram.exe", "Telegram", rect=(1500, 700, 300, 300))
    assert "private (telegram)" in max_agent(LookProvider()).handle("look at my screen").message


def test_both_screens(desk):
    desk.add(2, "chrome.exe", "Docs - Google Chrome")
    assert max_agent(LookProvider()).handle("what's on both screens").success
    assert desk.grabs == [("screen", None)]


def test_look_at_a_named_app(desk):
    desk.add(2, "chrome.exe", "Docs - Google Chrome")
    desk.add(3, "notepad.exe", "todo.txt - Notepad")
    assert max_agent(LookProvider()).handle("look at notepad").success
    assert desk.grabs == [("window", 3)]


def test_a_minimised_window_is_not_looked_at(desk):
    desk.add(3, "notepad.exe", "todo.txt - Notepad", minimised=True)
    assert "minimised" in max_agent(LookProvider()).handle("look at notepad").message


@pytest.mark.parametrize("said, looks", [
    ("what's this error", True), ("what's on my screen", True), ("what am i looking at", True),
    ("summarise this page", True), ("explain this code", True), ("read this email for me", True),
    ("look at chrome", True), ("what does this say", True),
    ("read the news", False), ("explain that", False), ("what's my project folder", False),
])
def test_look_phrases(said, looks):
    agent = max_agent(None)
    command, _ = agent.match(agent.normalize(said))
    assert (command is not None and command.name == "look at screen") == looks


def test_the_agent_asks_before_looking(desk):
    desk.add(2, "chrome.exe", "Docs - Google Chrome")
    asked = []
    provider = LookProvider(replies=[Reply(calls=[ToolCall("look_at_screen", {"question": "what is open"})])])
    agent = max_agent(provider, confirm=lambda q: asked.append(q) and False)
    agent.handle("can you tell me what i have open and then close it")
    assert asked == ["Should I take a screenshot of the window in front and send it to the AI?"]
    assert desk.grabs == []


def test_an_unclear_answer_to_an_approval_is_a_no(desk):
    # confirm() returning None (neither yes nor no) once let a controlled step run
    desk.add(2, "chrome.exe", "Docs - Google Chrome")
    provider = LookProvider(replies=[Reply(calls=[ToolCall("look_at_screen", {"question": "what is open"})])])
    max_agent(provider, confirm=lambda q: None).handle("can you tell me what i have open and then close it")
    assert desk.grabs == []


# ---------- the fallback when PrintWindow draws nothing ----------

def test_fallback_refuses_when_a_private_window_lies_over_the_shot(desk, monkeypatch):
    desk.add(2, "chrome.exe", "Docs - Google Chrome", rect=(0, 0, 1000, 800))
    desk.add(9, "telegram.exe", "Telegram mini", rect=(900, 700, 300, 300))  # always on top, overlapping
    monkeypatch.setattr(screen, "_print_window", lambda h: Image.new("RGB", (10, 10), "black"))
    monkeypatch.setattr(screen, "_grab_window", REAL_GRAB_WINDOW)
    monkeypatch.setattr(system_control, "_bring_to_front", lambda h: True)
    result = screen.look("what is this", provider=LookProvider())
    assert "private (telegram)" in result.message and desk.grabs == []


def test_fallback_grabs_the_window_area_when_nothing_private_is_over_it(desk, monkeypatch):
    desk.add(2, "chrome.exe", "Docs - Google Chrome", rect=(0, 0, 1000, 800))
    desk.add(9, "telegram.exe", "Telegram", rect=(1500, 0, 300, 300))  # elsewhere
    monkeypatch.setattr(screen, "_print_window", lambda h: None)
    monkeypatch.setattr(screen, "_grab_window", REAL_GRAB_WINDOW)
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    monkeypatch.setattr(system_control, "_bring_to_front", lambda h: True)
    assert screen.look("what is this", provider=LookProvider()).success
    assert desk.grabs == [("screen", (0, 0, 1000, 800))]


def test_fallback_for_a_window_off_every_screen_sends_nothing(desk, monkeypatch):
    desk.add(2, "chrome.exe", "Docs - Google Chrome", rect=(-32000, -32000, 160, 28))
    monkeypatch.setattr(screen, "_print_window", lambda h: None)
    monkeypatch.setattr(screen, "_grab_window", REAL_GRAB_WINDOW)
    provider = LookProvider()
    result = screen.look("what is this", provider=provider)
    assert result.message == "I can't see that window on screen. Bring it into view and ask me again."
    assert desk.grabs == [] and provider.looks == []


def test_fallback_with_a_blank_picture_sends_nothing(desk, monkeypatch):
    desk.add(2, "chrome.exe", "Docs - Google Chrome", rect=(0, 0, 1000, 800))
    monkeypatch.setattr(screen, "_print_window", lambda h: None)
    monkeypatch.setattr(screen, "_grab_window", REAL_GRAB_WINDOW)
    monkeypatch.setattr(screen, "_grab_screen", lambda box=None: Image.new("RGB", (10, 10), "black"))
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    monkeypatch.setattr(system_control, "_bring_to_front", lambda h: True)
    provider = LookProvider()
    assert "can't see that window" in screen.look("what is this", provider=provider).message
    assert provider.looks == []


# ---------- brains ----------

def test_relay_look_writes_the_picture_then_deletes_it(tmp_path, monkeypatch):
    relay = RelayProvider({}, folder=tmp_path)
    seen = {}

    def ask(request):
        seen["request"] = request
        seen["exists"] = (tmp_path / "screen.jpg").exists()
        return {"text": "A login page."}
    monkeypatch.setattr(relay, "ask", ask)
    assert relay.look(b"\xff\xd8jpeg", "what is this") == "A login page."
    assert seen["exists"] and seen["request"]["look"]["question"] == "what is this"
    assert not (tmp_path / "screen.jpg").exists()


def test_relay_removes_a_screenshot_left_behind(tmp_path):
    (tmp_path / "screen.jpg").write_bytes(b"old")
    RelayProvider({}, folder=tmp_path)
    assert not (tmp_path / "screen.jpg").exists()


def test_gemini_look_sends_the_picture_and_the_question(tmp_path):
    provider = gemini.GeminiProvider({"GEMINI_API_KEY": "test", "GEMINI_MODELS": ["m"]})
    provider.budget = Budget(limit=100, path=str(tmp_path / "usage.json"))
    sent = {}

    def generate_content(model, contents, config):
        sent.update(model=model, contents=contents, config=config)
        part = SimpleNamespace(text="It's a 404 page.", thought=False)
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])
    provider._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    assert provider.look(b"\xff\xd8jpeg", "what is this", context="Q: a\nA: b") == "It's a 404 page."
    image_part, text = sent["contents"]
    assert image_part.inline_data.mime_type == "image/jpeg" and image_part.inline_data.data == b"\xff\xd8jpeg"
    assert text.endswith("Now: what is this") and "Q: a" in text
    assert provider.budget.used() == 1
