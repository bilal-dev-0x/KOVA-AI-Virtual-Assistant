"""Day 6 step 1: the Controller — one Agent, one worker thread, prompts that
cross to the UI thread. No window, no Tk: the Bridge is drained by the test
exactly like the Tk timer drains it in the real dashboard."""
import time

from modules.result import Result
from ui.bridge import Bridge
from ui.controller import Controller


class FakeAgent:
    """Stands in for Agent: records requests, reports if two ever overlapped."""

    def __init__(self, script=None):
        self.confirm = lambda question: False
        self.ask = lambda question: None
        self.trace = lambda line: None
        self.script = script or (lambda text: Result(True, f"echo {text}"))
        self.handled = []
        self.running = 0
        self.overlapped = False

    def handle(self, text):
        self.running += 1
        if self.running > 1:
            self.overlapped = True
        try:
            self.handled.append(text)
            return self.script(text)
        finally:
            self.running -= 1


def make(agent=None, listen=None, prompt=None):
    agent = agent or FakeAgent()
    bridge = Bridge()
    shown = {"user": [], "results": [], "trace": []}
    ctl = Controller(agent, bridge,
                     on_user=shown["user"].append,
                     on_result=lambda result, source: shown["results"].append((result, source)),
                     on_trace=shown["trace"].append,
                     listen=listen, prompt=prompt)
    return agent, bridge, ctl, shown


def pump_until(bridge, condition, timeout=5):
    """Drain the bridge like the Tk timer does until the test's condition holds."""
    end = time.time() + timeout
    while not condition():
        bridge.drain()
        if time.time() > end:
            raise AssertionError("timed out waiting for the worker")
        time.sleep(0.002)
    bridge.drain()


def wait_for_pending(bridge, timeout=5):
    end = time.time() + timeout
    while bridge.pending() == 0:
        if time.time() > end:
            raise AssertionError("the worker never posted anything")
        time.sleep(0.002)


# ---------- requests ----------

def test_only_one_request_runs_at_a_time_and_in_order():
    agent, bridge, ctl, shown = make()
    assert ctl.submit("first")
    assert ctl.submit("second")
    assert ctl.submit("third")
    pump_until(bridge, lambda: len(shown["results"]) == 3)
    assert agent.handled == ["first", "second", "third"]
    assert not agent.overlapped
    assert shown["user"] == ["first", "second", "third"]
    assert [source for _, source in shown["results"]] == ["text", "text", "text"]
    assert ctl.state == "idle"


def test_empty_or_refused_text_is_not_queued():
    agent, bridge, ctl, shown = make()
    assert ctl.submit("") is False
    assert ctl.submit("   ") is False
    ctl.stop()
    assert ctl.submit("after closing") is False
    assert agent.handled == []


def test_the_worker_survives_a_broken_request():
    def boom(text):
        raise ValueError("bad reply")

    agent = FakeAgent(script=boom)
    bridge = Bridge()
    ctl = Controller(agent, bridge, on_result=lambda r, s: None)
    ctl.submit("anything")
    pump_until(bridge, lambda: ctl.state == "idle")  # the thread went back to sleep
    assert agent.handled == ["anything"]


# ---------- approvals ----------

def test_confirm_waits_for_the_pop_up_and_only_yes_is_yes():
    asked = []

    def prompt(kind, question):
        asked.append((kind, question))
        return True

    agent = FakeAgent()
    agent.script = lambda text: Result(agent.confirm("Close discord?"), "closing")
    _, bridge, ctl, shown = make(agent=agent, prompt=prompt)
    ctl.attach()
    ctl.submit("close discord")
    pump_until(bridge, lambda: shown["results"])
    assert asked == [("confirm", "Close discord?")]
    assert shown["results"][0][0].message == "closing"
    assert agent.confirm == ctl.confirm  # the one agent asks the UI now


def test_closing_the_pop_up_counts_as_no():
    def prompt(kind, question):
        return None  # the user clicked the window's X

    agent = FakeAgent()

    def script(text):
        approved = agent.confirm("Delete it?")
        return Result(approved, "ran" if approved else "no")

    agent.script = script
    _, bridge, ctl, shown = make(agent=agent, prompt=prompt)
    ctl.attach()
    ctl.submit("delete it")
    pump_until(bridge, lambda: shown["results"])
    assert shown["results"][0][0].message == "no"


def test_ask_returns_what_was_typed_and_none_when_closed():
    answers = iter(["calculator", None])

    def prompt(kind, question):
        return next(answers)

    agent = FakeAgent()
    agent.script = lambda text: Result(True, str(agent.ask("Which app?")))
    _, bridge, ctl, shown = make(agent=agent, prompt=prompt)
    ctl.attach()
    ctl.submit("open")
    ctl.submit("open again")
    pump_until(bridge, lambda: len(shown["results"]) == 2)
    assert shown["results"][0][0].message == "calculator"
    assert shown["results"][1][0].message == "None"


def test_stopping_the_window_frees_a_worker_stuck_on_a_pop_up():
    """Quit while a dialog is up: the prompt never gets answered, so the bridge
    closes under it and the worker lets go instead of hanging forever."""
    agent = FakeAgent()

    def script(text):
        approved = agent.confirm("Close everything?")
        return Result(approved, "yes" if approved else "no")

    agent.script = script
    bridge = Bridge()
    ctl = Controller(agent, bridge, on_result=lambda r, s: None, prompt=lambda k, q: None)
    ctl.attach()
    ctl.submit("close everything")
    wait_for_pending(bridge)  # the dialog is waiting for the user, nothing drains it
    ctl.stop()
    pump_until(bridge, lambda: ctl.state == "idle")
    assert agent.handled == ["close everything"]
    assert ctl.busy is False
    assert bridge.closed


# ---------- trace and the mic ----------

def test_trace_lines_reach_the_panel_without_blocking_the_worker():
    def script(text):
        agent.trace("  → close_app(name='discord')")
        return Result(True, "Closed Discord.")

    agent = FakeAgent(script=script)
    _, bridge, ctl, shown = make(agent=agent)
    ctl.attach()
    ctl.submit("close discord")
    pump_until(bridge, lambda: shown["results"])
    assert shown["trace"] == ["  → close_app(name='discord')"]


def test_the_mic_records_one_phrase_and_hands_it_to_the_same_agent():
    agent, bridge, ctl, shown = make(listen=lambda: "open notepad")
    ctl.attach()
    assert ctl.push_to_talk()
    pump_until(bridge, lambda: shown["results"])
    assert agent.handled == ["open notepad"]
    assert shown["user"] == ["open notepad"]
    assert shown["results"][0][1] == "mic"
    assert ctl.state == "idle"


def test_hearing_nothing_says_so_instead_of_running_anything():
    agent, bridge, ctl, shown = make(listen=lambda: None)
    ctl.push_to_talk()
    pump_until(bridge, lambda: shown["results"])
    result, source = shown["results"][0]
    assert not result.success and "catch" in result.message
    assert source == "mic"
    assert agent.handled == []


def test_a_muted_microphone_does_nothing():
    calls = []

    def listen():
        calls.append(1)
        return "open notepad"

    agent, bridge, ctl, shown = make(listen=listen)
    ctl.muted = True
    assert ctl.push_to_talk() is False
    assert ctl.state == "muted"
    assert calls == []
    assert agent.handled == []


def test_a_ui_action_runs_on_the_worker_and_reports_back():
    _, bridge, ctl, shown = make()
    assert ctl.run(lambda: Result(True, "forgotten"))
    pump_until(bridge, lambda: shown["results"])
    assert shown["results"][0] == (Result(True, "forgotten"), "ui")
