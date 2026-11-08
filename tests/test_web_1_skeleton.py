"""The new PyWebView dashboard, step 1: window skeleton and the Python-JS
bridge. No real window, no real pywebview — a fake window records every
evaluate_js, and the bridge is driven by hand exactly like test_day6_*.
"""
import json
import sys
import types

from modules.result import Result
from ui.bridge import Bridge
from ui.controller import Controller
from ui import web_app


def js_values(window):
    """Everything the page received, as dicts: 'window.kova.receive({...})'."""
    marks = "window.kova.receive("
    return [json.loads(script[len(marks):-1]) for script in window.scripts]


class FakeEvent:
    """One pywebview event: handlers arrive with +=."""

    def __init__(self, rules, name):
        self._rules = rules
        self._name = name

    def __iadd__(self, fn):
        self._rules.setdefault(self._name, []).append(fn)
        return self


class FakeEvents:
    """pywebview's events container: `events.loaded += handler`, fired by name."""

    def __init__(self):
        self.rules = {}

    def __getattr__(self, name):
        return FakeEvent(self.rules, name)

    def fire(self, name):
        for fn in self.rules.get(name, []):
            fn()


class FakeWebViewWindow:
    def __init__(self):
        self.kwargs = None
        self.scripts = []
        self.shown = 0
        self.hidden = 0
        self.destroyed = False
        self.events = FakeEvents()

    def register_kwargs(self, kwargs):
        self.kwargs = kwargs
        return self

    def evaluate_js(self, script):
        self.scripts.append(script)
        return None

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def destroy(self):
        self.destroyed = True


class FakeAgent:
    def __init__(self):
        self.config = {"ASSISTANT_NAME": "Max"}
        self.name = "Max"
        self.title = "Commander"
        self.provider = None
        self.confirm = lambda question: False
        self.ask = lambda question: None
        self.trace = lambda line: None

    def handle(self, text):
        return Result(True, f"handled {text}")


class FakeStats:
    """Counts observations of the model, without touching the system."""

    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def make_dashboard():
    """A dashboard with a fake window, a fake sampler and no pump thread
    racing the test.
    """
    window = FakeWebViewWindow()
    sampler = FakeStats()
    controller = Controller(FakeAgent(), Bridge())
    dashboard = web_app.WebDashboard(
        controller, controller.agent, stats_factory=lambda push: sampler,
        create_window=lambda **kw: window.register_kwargs(kw))
    dashboard._pump_stop.set()   # the test drives pump ticks by hand
    return dashboard, window, controller, sampler


def test_bridge_next_call_runs_posted_work():
    bridge = Bridge()
    calls = []
    bridge.post(calls.append, "one")
    assert bridge.next_call(timeout=0) is True
    assert calls == ["one"]
    assert bridge.next_call(timeout=0.01) is False   # nothing pending


def test_bridge_next_call_swallows_a_broken_callback():
    bridge = Bridge()
    seen = []

    def broken():
        raise ValueError("boom")

    bridge.post(broken)
    bridge.post(seen.append, "after")
    assert bridge.next_call(timeout=0) is True
    assert bridge.next_call(timeout=0) is True
    assert seen == ["after"]   # one broken callback cannot stop the queue


def test_no_pending_work_runs_after_close():
    bridge = Bridge()
    seen = []
    bridge.post(seen.append, "late")
    bridge.close()
    assert bridge.next_call(timeout=0.01) is False
    assert bridge.post(seen.append, "later") is False
    assert seen == []


def test_js_events_are_safe_and_round_trip():
    weird = 'He said "hi" \\ a\nline\tand é'
    script = web_app.js_receive({"t": "chat", "text": weird})
    assert script.startswith("window.kova.receive(") and script.endswith(")")
    assert json.loads(script[len("window.kova.receive("):-1]) == {"t": "chat", "text": weird}


def test_state_events_carry_a_label_and_tone():
    assert web_app.state_event("working") == {
        "t": "state", "state": "working", "text": "Thinking...", "tone": "working"}
    assert web_app.state_parts("asleep") == ("Asleep — say the wake word", "asleep")
    assert web_app.state_parts("mystery") == ("mystery", "idle")


def test_webview_availability(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    monkeypatch.setattr(web_app, "webview_runtime_found", lambda: True)
    assert web_app.webview_available() is True
    monkeypatch.setattr(web_app, "webview_runtime_found", lambda: False)
    assert web_app.webview_available() is False
    monkeypatch.setitem(sys.modules, "webview", None)   # pywebview not installed
    monkeypatch.setattr(web_app, "webview_runtime_found", lambda: True)
    assert web_app.webview_available() is False


def test_dashboard_wires_the_window_and_the_agent_callbacks():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        assert window.kwargs["title"] == "KOVA — Max"
        assert window.kwargs["url"].startswith("file:///") and window.kwargs["url"].endswith("ui/web/index.html")
        assert window.kwargs["js_api"] is dashboard.api
        assert controller.on_user == dashboard.show_user
        assert controller.on_result == dashboard.show_result
        assert controller.on_trace == dashboard.show_trace
    finally:
        dashboard.destroy()


def test_events_before_the_page_loads_are_buffered_in_order():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        controller.bridge.post(dashboard.show_user, "who greeted first")
        controller.bridge.post(dashboard.show_trace, "trace line")
        dashboard._tick()
        assert window.scripts == []   # nothing before the page has loaded

        window.events.fire("loaded")   # pywebview page ready, pump flushes next tick
        dashboard._tick()
        payloads = js_values(window)
        assert payloads[0]["t"] == "boot"   # the boot facts ride in first
        assert payloads[1:] == [{"t": "chat", "who": "user", "text": "who greeted first"},
                                {"t": "trace", "line": "trace line"}]
    finally:
        dashboard.destroy()


def test_answer_and_trace_events_keep_their_shape():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        window.events.fire("loaded")
        answer = Result(True, "First line", "Second line\nThird")
        controller.bridge.post(dashboard.show_result, answer, "text")
        controller.bridge.post(dashboard.show_trace, "→ flip_light_switch({})")
        controller.bridge.next_call(timeout=0)
        controller.bridge.next_call(timeout=0)
        payloads = js_values(window)
        assert payloads[0] == {"t": "chat", "who": "max",
                               "text": "First line\nSecond line\nThird", "note": None}
        assert payloads[1] == {"t": "trace", "line": "→ flip_light_switch({})"}
    finally:
        dashboard.destroy()


def test_map_and_unmap_hooks_start_and_stop_the_sampler():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        window.events.fire("shown")
        assert sampler.started == 1 and sampler.stopped == 0
        window.events.fire("minimized")
        assert sampler.stopped == 1
        window.events.fire("restored")
        assert sampler.started == 2

        seen = []
        dashboard.bind("<Map>", lambda event: seen.append("map"))
        dashboard.bind("<Unmap>", lambda event: seen.append("unmap"))
        dashboard.withdraw()   # to the tray
        dashboard.show()
        assert seen == ["unmap", "map"]
        assert window.hidden == 1 and window.shown == 1
    finally:
        dashboard.destroy()


def test_state_changes_arrive_only_when_they_change_while_visible():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        window.events.fire("loaded")
        controller._state = "working"
        dashboard._set_visible(True)
        dashboard._tick()
        dashboard._tick()   # nothing changed: nothing new is sent
        states = [p for p in js_values(window) if p["t"] == "state"]
        assert states == [{"t": "state", "state": "working",
                           "text": "Thinking...", "tone": "working"}]
        controller._state = "idle"
        dashboard._tick()
        states = [p for p in js_values(window) if p["t"] == "state"]
        assert states[-1] == {"t": "state", "state": "idle", "text": "Ready", "tone": "idle"}
    finally:
        dashboard.destroy()


def test_a_result_that_stops_reaches_on_exit():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        came_back = []
        window.events.fire("loaded")
        dashboard.on_exit = came_back.append
        controller.bridge.post(dashboard.show_result, Result(True, "done", stop=True), "text")
        controller.bridge.next_call(timeout=0)
        assert len(came_back) == 1 and came_back[0].stop
    finally:
        dashboard.destroy()


def test_prompt_placeholder_answers_the_safe_way():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        assert dashboard.prompt("confirm", "Should I close it?") is False
        assert dashboard.prompt("ask", "Your name?") is None
    finally:
        dashboard.destroy()


def test_notice_runs_without_being_a_chat_line():
    dashboard, window, controller, sampler = make_dashboard()
    try:
        window.events.fire("loaded")
        dashboard.show_notice("Battery is down to 15%.")
        assert js_values(window) == [{"t": "notice", "text": "Battery is down to 15%."}]
    finally:
        dashboard.destroy()


def test_destroy_stops_the_pump_and_the_sampler():
    dashboard, window, controller, sampler = make_dashboard()
    thread = dashboard._pump_thread
    dashboard.destroy()
    assert window.destroyed
    thread.join(2)
    assert not thread.is_alive()


def test_launch_uses_the_web_window_and_falls_back_to_the_classic_one(monkeypatch):
    from types import SimpleNamespace

    from ui import run as ui_run

    choices = []

    def fake_run_ui(agent, is_yes=None, dashboard_factory=None, **kw):
        choices.append(SimpleNamespace(agent=agent, factory=dashboard_factory))
        return Result(True, "done", stop=True)

    monkeypatch.setattr(ui_run, "run_ui", fake_run_ui)
    agent = FakeAgent()

    monkeypatch.setattr(web_app, "webview_available", lambda: True)
    result = web_app.launch(agent, is_yes=lambda q: True)
    assert result.stop and choices[-1].agent is agent
    assert choices[-1].factory is web_app.WebDashboard

    monkeypatch.setattr(web_app, "webview_available", lambda: False)
    count = len(choices)
    assert web_app.launch(agent).stop and len(choices) == count   # no window, no classic fallback
