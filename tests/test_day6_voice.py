"""Day 6 (Bilal's ask): voice mode runs all the time inside the dashboard,
beside typing. No microphone: listen() is scripted, the bridge is drained by hand."""
import threading
import time

from modules.result import Result
from ui.messages import percent
from ui.bridge import Bridge
from ui.controller import Controller
from ui.voice import VoiceLoop


class FakeAgent:
    def __init__(self, answer=None):
        self.config = {"AWAKE_TIMEOUT": 60}
        self.name = "Max"
        self.title = "Commander"
        self.wake_words = ["max"]
        self.confirm = lambda question: False
        self.ask = lambda question: None
        self.trace = lambda line: None
        self.handled = []
        self.answer = answer or (lambda text: Result(True, f"did {text}"))

    def find_wake_word(self, heard):
        heard = heard.lower()
        return heard.split("max", 1)[1].strip(" ,") if "max" in heard else None

    def normalize(self, text):
        return text.strip()

    def handle(self, text):
        self.handled.append(text)
        return self.answer(text)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(heard, answer=None):
    agent = FakeAgent(answer)
    bridge = Bridge()
    ctl = Controller(agent, bridge)
    ctl.attach()
    spoken = []
    ctl.speak = spoken.append
    script = list(heard)
    clock = Clock()

    def listen(wait_seconds=5, max_seconds=10):
        return script.pop(0) if script else None

    voice = VoiceLoop(agent, ctl, listen, spoken.append, clock=clock)
    ctl.on_sleep = voice.went_to_sleep
    return agent, ctl, voice, spoken, clock


def test_wake_word_with_a_command_runs_it_and_speaks_the_answer():
    agent, ctl, voice, spoken, _ = make(["max what time is it"])
    assert voice.step() == "what time is it"
    assert agent.handled == ["what time is it"]
    assert spoken == ["did what time is it"]
    assert voice.awake and ctl.voice_state == "awake"


def test_speech_without_the_wake_word_is_ignored_while_asleep():
    agent, ctl, voice, spoken, _ = make(["what time is it"])
    assert voice.step() is None
    assert agent.handled == [] and not voice.awake


def test_awake_max_needs_no_wake_word_and_sleeps_after_the_timeout():
    agent, ctl, voice, spoken, clock = make(["max", "open notepad"])
    voice.step()
    assert spoken == ["Yes Commander? I'm listening."]
    assert voice.step() == "open notepad"
    clock.now += 61
    voice.step()
    assert "Going to sleep." in spoken and not voice.awake
    assert ctl.voice_state == "asleep"


def test_go_to_sleep_answer_puts_the_loop_back_to_sleep():
    agent, ctl, voice, spoken, _ = make(["max go to sleep"],
                                        answer=lambda text: Result(True, "Sleeping.", sleep=True))
    voice.step()
    assert not voice.awake


def test_muted_voice_never_touches_the_microphone():
    heard = []
    agent, ctl, voice, spoken, _ = make([])
    voice.listen = lambda **kw: heard.append(kw)
    ctl.muted = True
    assert voice.step() is None and heard == []


def test_hotkey_wakes_max_without_the_wake_word():
    agent, ctl, voice, spoken, _ = make(["open notepad"])
    voice.wake()
    assert voice.step() == "open notepad"


def test_unsure_speech_is_asked_again_not_acted_on():
    agent, ctl, voice, spoken, _ = make(["max delete everything"])
    voice.unsure = lambda: True
    assert voice.step() is None
    assert agent.handled == [] and "didn't catch that" in spoken[0]


def test_spoken_request_is_approved_by_voice_typed_one_by_pop_up():
    asked = []

    def answer(text):
        return Result(True, "yes" if asked_agent.confirm("Should I close it?") else "no")

    asked_agent, ctl, voice, spoken, _ = make(["max close it", "yes do it"], answer=answer)
    ctl.voice_prompt = lambda kind, question: voice.prompt(kind, question, lambda a, q: a == "yes do it")
    ctl.prompt = lambda kind, question: asked.append(question) or False
    voice.step()
    assert spoken[-1] == "yes" and "Should I close it?" in spoken and asked == []

    done = threading.Event()
    ctl.submit("close it", done=done)
    end = time.time() + 5
    while not done.is_set() and time.time() < end:
        ctl.bridge.drain()
        time.sleep(0.002)
    assert asked == ["Should I close it?"]


def test_typed_answers_are_not_spoken():
    agent, ctl, voice, spoken, _ = make([])
    done = threading.Event()
    ctl.submit("hello", done=done)
    assert done.wait(5)
    assert spoken == []


def test_status_shows_asleep_and_awake():
    agent, ctl, voice, spoken, _ = make([])
    ctl.voice_state = "asleep"
    assert ctl.state == "asleep"
    ctl.muted = True
    assert ctl.state == "muted"


def test_percent_reads_meter_values():
    assert percent("71%") == 0.71
    assert percent("100% charging") == 1.0
    assert percent(None) is None
