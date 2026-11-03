import main
from desktop_agent import Agent
from modules import voice_handler


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class FakeVoice:
    """Plays back a script of what the mic 'heard'. None means silence."""

    def __init__(self, heard, clock):
        self.heard = list(heard)
        self.clock = clock
        self.spoken = []

    def listen_text(self, wait_seconds=5, max_seconds=10):
        self.clock.now += wait_seconds
        # When the script runs out, end the loop
        return self.heard.pop(0) if self.heard else "max goodbye"

    def speak(self, text):
        self.spoken.append(text)


def run(monkeypatch, heard, timeout=60):
    clock = FakeClock()
    voice = FakeVoice(heard, clock)
    monkeypatch.setattr(voice_handler, "listen_text", voice.listen_text)
    monkeypatch.setattr(voice_handler, "speak", voice.speak)
    monkeypatch.setattr(main.time, "time", clock.time)

    agent = Agent({"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"], "AWAKE_TIMEOUT": timeout})
    handled = []
    original = agent.handle
    agent.handle = lambda text: handled.append(agent.normalize(text)) or original(text)

    main.run_voice(agent)
    return handled, voice.spoken


def test_stays_awake_without_wake_word(monkeypatch):
    handled, _ = run(monkeypatch, ["max", "what time is it", "whats the date", "goodbye"])
    assert handled == ["what time is it", "whats the date", "goodbye"]


def test_ignores_speech_while_asleep(monkeypatch):
    handled, _ = run(monkeypatch, ["what time is it", "max what time is it", "goodbye"])
    assert handled == ["what time is it", "goodbye"]


def test_go_to_sleep_command(monkeypatch):
    handled, _ = run(monkeypatch, ["max", "go to sleep", "what time is it", "max whats the date", "goodbye"])
    assert handled == ["go to sleep", "whats the date", "goodbye"]


def test_falls_asleep_after_timeout(monkeypatch):
    silence = [None] * 20  # 20 x 5 s = 100 s of nothing
    handled, spoken = run(monkeypatch, ["max"] + silence + ["what time is it"], timeout=60)
    assert "Going to sleep." in spoken
    assert handled == ["goodbye"]


def test_confirmation_asks_again_when_nothing_heard(monkeypatch):
    from modules import system_control
    from modules.result import Result
    monkeypatch.setattr(system_control, "check_app", lambda name, must_be_running=False: (name, None))
    closed = []
    monkeypatch.setattr(system_control, "close_app", lambda name: closed.append(name) or Result(True, "Closed"))

    _, spoken = run(monkeypatch, ["max close notepad", None, "yes", "goodbye"])
    assert "Sorry, I didn't hear you. Yes or no?" in spoken
    assert closed == ["notepad"]


def test_single_word_noise_not_sent_to_gemini(monkeypatch):
    import desktop_agent
    asked = []
    monkeypatch.setattr(desktop_agent.ai_brain, "ask", lambda text, config: asked.append(text) or "answer")
    _, spoken = run(monkeypatch, ["max", "goa", "goodbye"])
    assert asked == []
    assert "Sorry, I didn't catch that." in spoken


def test_timeout_zero_never_sleeps(monkeypatch):
    silence = [None] * 20
    handled, spoken = run(monkeypatch, ["max"] + silence + ["what time is it", "goodbye"], timeout=0)
    assert "Going to sleep." not in spoken
    assert handled == ["what time is it", "goodbye"]


def test_is_yes():
    assert main.is_yes("Yes")
    assert main.is_yes("yeah close it")
    assert main.is_yes("Okay.")
    assert not main.is_yes("no")
    assert not main.is_yes(None)
    assert not main.is_yes("yesterday")
