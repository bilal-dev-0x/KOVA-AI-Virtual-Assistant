"""Day 6 dashboard startup wiring, tested without opening a real window."""
import sys
import time
from types import SimpleNamespace

from modules.result import Result
from ui.run import run_ui


class FakeAgent:
    def __init__(self):
        self.config = {"ASSISTANT_NAME": "Max", "USER_TITLE": "Commander", "PTT_HOTKEY": "F9"}
        self.name = "Max"
        self.title = "Commander"
        self.handled = []
        self.confirm = lambda question: False
        self.ask = lambda question: None
        self.trace = lambda line: None

    def handle(self, text):
        self.handled.append(text)
        return Result(True, f"handled {text}")


class FakeDashboard:
    def __init__(self, controller, agent, **callbacks):
        self.controller = controller
        self.agent = agent
        self.callbacks = callbacks
        self.on_exit = None
        self.bound = []
        self.destroyed = False
        controller.on_user = lambda text: None
        controller.on_result = lambda result, source: None
        controller.on_trace = lambda line: None
        controller.prompt = self.prompt

    def bind(self, event, callback, add=None):
        self.bound.append(event)

    def prompt(self, kind, question):
        return True

    def withdraw(self):
        pass

    def show(self):
        pass

    def _toggle_mute(self):
        self.controller.muted = not self.controller.muted

    def state(self):
        return "normal"

    def show_notice(self, message):
        pass

    def destroy(self):
        self.destroyed = True

    def mainloop(self):
        assert self.controller.agent is self.agent
        assert self.agent.confirm == self.controller.confirm
        assert self.agent.ask == self.controller.ask
        assert self.agent.trace == self.controller.trace
        assert self.bound == ["<Map>", "<Unmap>"]
        self.controller.submit("hello")
        end = time.time() + 2
        while self.controller.busy and time.time() < end:
            self.controller.bridge.drain()
            time.sleep(0.002)
        self.controller.bridge.drain()
        self.on_exit(Result(True, "restart", restart=True))


class FakeHotkey:
    def __init__(self, callback):
        self.callback = callback
        self.started = []
        self.stopped = False

    def start(self, spec):
        self.started.append(spec)
        return True

    def stop(self):
        self.stopped = True


class FakeTray:
    last = None

    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.stopped = False
        FakeTray.last = self

    def start(self):
        return True

    def stop(self):
        self.stopped = True


def test_run_ui_wires_one_agent_and_tears_down(monkeypatch):
    from modules import alerts, reminders, voice_handler

    agent = FakeAgent()
    monkeypatch.setattr(voice_handler, "configure", lambda config: None)
    previous_alert = alerts.on_alert = lambda message: None
    previous_reminder = reminders.on_fire = lambda message: None
    starts, stops = [], []
    monkeypatch.setattr(alerts, "start", lambda config: starts.append(config))
    monkeypatch.setattr(alerts, "stop", lambda: stops.append(True))
    hotkeys = []

    def hotkey_factory(callback):
        hotkey = FakeHotkey(callback)
        hotkeys.append(hotkey)
        return hotkey

    result = run_ui(agent, dashboard_factory=FakeDashboard, tray_factory=FakeTray,
                    hotkey_factory=hotkey_factory, listen=lambda: "mic phrase", speak=lambda text: None)

    assert result.restart
    assert agent.handled == ["hello"]
    assert hotkeys[0].started == ["F9"] and hotkeys[0].stopped
    assert FakeTray.last.stopped
    assert len(stops) == 1
    assert alerts.on_alert is previous_alert
    assert reminders.on_fire is previous_reminder
    assert agent.min_ai_words == 3


def test_run_ui_wires_the_own_window_hook_and_clears_it(monkeypatch):
    """run.py owns windows.on_own_window for exactly as long as the dashboard
    lives: the agent's 'maximise your window' with the dashboard hidden goes
    through it, and voice/text mode afterwards must never see a dead window."""
    from modules import voice_handler, windows

    monkeypatch.setattr(voice_handler, "configure", lambda config: None)
    seen = []

    class HookDashboard(FakeDashboard):
        def focus_window(self):
            seen.append("front")

        def show_maximised(self):
            seen.append("maximise")

        def mainloop(self):
            assert windows.on_own_window is not None      # wired while the UI lives
            answer = windows.on_own_window("maximise")    # the agent's tool path
            assert answer.success and "back up" in answer.message
            end = time.time() + 2
            while not seen and time.time() < end:
                self.controller.bridge.drain()
                time.sleep(0.002)
            self.controller.bridge.drain()
            self.on_exit(Result(True, "restart", restart=True))

    result = run_ui(FakeAgent(), dashboard_factory=HookDashboard, tray_factory=FakeTray,
                    hotkey_factory=lambda callback: FakeHotkey(callback),
                    listen=lambda: None, speak=lambda text: None)

    assert result.restart
    assert seen == ["maximise"]                           # the pump ran the window work
    assert windows.on_own_window is None                  # cleared once the UI is gone


def test_run_ui_returns_stop_result_when_the_dashboard_quits(monkeypatch):
    from modules import alerts, voice_handler

    monkeypatch.setattr(voice_handler, "configure", lambda config: None)

    class QuitDashboard(FakeDashboard):
        def mainloop(self):
            self.callbacks["on_quit"]()

    result = run_ui(FakeAgent(), dashboard_factory=QuitDashboard,
                    tray_factory=FakeTray, hotkey_factory=FakeHotkey)
    assert result.stop and not result.restart


def test_main_dispatches_ui_mode_without_creating_another_agent(monkeypatch):
    import main

    agent = FakeAgent()
    calls = []
    monkeypatch.setattr(sys, "argv", ["main.py", "--ui"])
    monkeypatch.setattr(main, "load_config", lambda: {})
    monkeypatch.setattr(main, "Agent", lambda config: agent)
    monkeypatch.setattr(main, "single_instance", lambda: True)
    monkeypatch.setattr(main.windows if hasattr(main, "windows") else __import__("modules.windows", fromlist=["windows"]),
                        "dpi_aware", lambda: None, raising=False)

    import ui.web_app
    monkeypatch.setattr(ui.web_app, "launch", lambda shared, **kw: calls.append(shared) or Result(True, "done", stop=True))
    main.main()
    assert calls == [agent]
