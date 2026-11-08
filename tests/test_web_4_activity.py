"""The new dashboard, step 4: the Activity feed and the approval dialog. The
dialog round-trip is driven with a waiting thread answering through api — the
same shape the controller's round-trip makes on the worker, never a real
window.
"""
import threading
import time
from types import SimpleNamespace

from modules.result import Result
from ui.bridge import Bridge
from ui.controller import Controller
from ui import web_app

from tests.test_web_1_skeleton import FakeAgent, FakeWebViewWindow, js_values


def make_dashboard():
    window = FakeWebViewWindow()
    controller = Controller(FakeAgent(), Bridge())
    dashboard = web_app.WebDashboard(
        controller, controller.agent, create_window=lambda **kw: window,
        stats_factory=lambda push: SimpleNamespace(start=lambda: None, stop=lambda: None))
    dashboard._pump_stop.set()
    window.events.fire("loaded")
    dashboard._flush_pending()   # the boot event rides out early
    return dashboard, window, controller


def wait_for_prompt(window, seconds=2.0):
    """Until the page has one dialog open; returns its payload."""
    end = time.time() + seconds
    while time.time() < end:
        for payload in js_values(window):
            if payload["t"] == "prompt":
                return payload
        time.sleep(0.01)
    raise AssertionError("no dialog was opened on the page")


def wait_done(predicate, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def wait_for_new_prompt(window, previous_token, seconds=2.0):
    """The next dialog after the one already answered."""
    end = time.time() + seconds
    while time.time() < end:
        for payload in reversed(js_values(window)):
            if payload["t"] == "prompt" and payload["token"] != previous_token:
                return payload
        time.sleep(0.01)
    raise AssertionError("the second dialog was never opened")


def test_no_dialog_works_when_the_page_never_loaded():
    window = FakeWebViewWindow()
    controller = Controller(FakeAgent(), Bridge())
    dash = web_app.WebDashboard(controller, controller.agent,
                                create_window=lambda **kw: window,
                                stats_factory=lambda push: SimpleNamespace(start=lambda: None, stop=lambda: None))
    dash._pump_stop.set()   # loaded never fired
    try:
        assert dash.prompt("confirm", "Should I do it?") is False
        assert dash.prompt("ask", "Name?") is None
        assert window.scripts == []
    finally:
        dash.destroy()


def test_a_confirm_dialog_answers_true_only_when_approved():
    dashboard, window, controller = make_dashboard()
    try:
        came = {}

        def the_agent_waits():
            came["answer"] = dashboard.prompt("confirm", "Should I close the calculator?")

        waiter = threading.Thread(target=the_agent_waits, daemon=True)
        waiter.start()
        assert wait_done(lambda: any(p["t"] == "prompt" for p in js_values(window)))
        payload = [p for p in js_values(window) if p["t"] == "prompt"][-1]
        assert payload["kind"] == "confirm"
        assert payload["question"] == "Should I close the calculator?"
        dashboard.api.respond(payload["token"], True)
        waiter.join(2)
        assert not waiter.is_alive()
        assert came.get("answer") is True
        # the answered token is stale now: nothing changes the result twice
        dashboard.api.respond(payload["token"], False)
        assert came["answer"] is True
    finally:
        dashboard.destroy()


def test_a_confirm_dialogclosing_is_a_no():
    dashboard, window, controller = make_dashboard()
    try:
        came = {}

        def the_agent_waits():
            came["answer"] = dashboard.prompt("confirm", "Should I close the calculator?")

        waiter = threading.Thread(target=the_agent_waits, daemon=True)
        waiter.start()
        payload = wait_for_prompt(window)
        dashboard.api.respond(payload["token"], False)   # what closing the dialog sends
        waiter.join(2)
        assert came.get("answer") is False
    finally:
        dashboard.destroy()


def test_an_ask_dialog_answers_with_the_typed_text_or_none():
    dashboard, window, controller = make_dashboard()
    try:
        came = {}

        def the_agent_waits():
            came["answer"] = dashboard.prompt("ask", "What should the note say?")

        waiter = threading.Thread(target=the_agent_waits, daemon=True)
        waiter.start()
        payload = wait_for_prompt(window)
        dashboard.api.respond(payload["token"], "buy milk")
        waiter.join(2)
        assert came.get("answer") == "buy milk"

        came.clear()
        waiter = threading.Thread(target=the_agent_waits, daemon=True)
        waiter.start()
        payload = wait_for_new_prompt(window, payload["token"])
        dashboard.api.respond(payload["token"], None)   # Cancel / closing the dialog
        waiter.join(2)
        assert came.get("answer") is None
    finally:
        dashboard.destroy()


def test_a_window_going_away_answering_no_instead_of_waiting():
    dashboard, window, controller = make_dashboard()
    try:
        came = {}

        def the_agent_waits():
            came["answer"] = dashboard.prompt("confirm", "Should I close it?")

        waiter = threading.Thread(target=the_agent_waits, daemon=True)
        waiter.start()
        wait_for_prompt(window)
        controller.bridge.close()   # the dashboard is being torn down
        waiter.join(3)
        assert not waiter.is_alive()
        assert came.get("answer") is False
    finally:
        dashboard.destroy()


def test_controller_roundtrip_wires_confirm_through_the_dialog():
    dashboard, window, controller = make_dashboard()
    pump_stop = threading.Event()

    def pump():
        while not pump_stop.is_set():
            controller.bridge.next_call(timeout=0.05)

    pumper = threading.Thread(target=pump, daemon=True)
    pumper.start()
    try:
        controller.attach()
        controller.prompt = dashboard.prompt
        came = {}

        waiter = threading.Thread(target=lambda: came.update(
            answer=controller.confirm("Should I delete memory 3?")), daemon=True)
        waiter.start()
        payload = wait_for_prompt(window)
        dashboard.api.respond(payload["token"], True)
        waiter.join(2)
        assert came.get("answer") is True

        came.clear()
        waiter = threading.Thread(target=lambda: came.update(
            answer=controller.confirm("Should I delete memory 3?")), daemon=True)
        waiter.start()
        payload = wait_for_new_prompt(window, payload["token"])
        dashboard.api.respond(payload["token"], False)
        waiter.join(2)
        assert came.get("answer") is False
    finally:
        pump_stop.set()
        controller.stop()   # any waiter still open sees the bridge close and lets go
        dashboard.destroy()


def test_the_ask_dialog_via_the_controller_roundtrip():
    dashboard, window, controller = make_dashboard()
    pump_stop = threading.Event()

    def pump():
        while not pump_stop.is_set():
            controller.bridge.next_call(timeout=0.05)

    pumper = threading.Thread(target=pump, daemon=True)
    pumper.start()
    try:
        controller.attach()
        controller.prompt = dashboard.prompt
        came = {}
        waiter = threading.Thread(target=lambda: came.update(
            answer=controller.ask("What should the note say?")), daemon=True)
        waiter.start()
        payload = wait_for_prompt(window)
        dashboard.api.respond(payload["token"], "buy milk")
        waiter.join(2)
        assert came.get("answer") == "buy milk"
    finally:
        pump_stop.set()
        controller.stop()
        dashboard.destroy()


def test_trace_events_land_in_order():
    dashboard, window, controller = make_dashboard()
    try:
        for i in range(30):
            controller.bridge.post(dashboard.show_trace, f"→ tool_{i}()")
        for _ in range(30):
            controller.bridge.next_call(timeout=0)
        dashboard._flush_pending()
        lines = [p["line"] for p in js_values(window) if p["t"] == "trace"]
        assert lines == [f"→ tool_{i}()" for i in range(30)]
    finally:
        dashboard.destroy()


def test_the_page_side_buffer_never_grows_without_bound():
    dashboard, window, controller = make_dashboard()
    try:
        with dashboard._lock:      # the page is loading again: nothing may evaluate yet
            dashboard._ready = False
        for i in range(web_app.BUFFER_KEEP + 5):
            dashboard._push({"t": "trace", "line": str(i)})
        with dashboard._lock:
            dashboard._ready = True
        dashboard._flush_pending()
        lines = [p["line"] for p in js_values(window) if p["t"] == "trace"]
        assert len(lines) == web_app.BUFFER_KEEP
        assert lines[0] == "5"   # the five oldest dropped, newest kept
    finally:
        dashboard.destroy()


def test_a_stopped_result_reaches_on_exit_from_the_bridge():
    dashboard, window, controller = make_dashboard()
    try:
        came = []
        dashboard.on_exit = came.append
        controller.bridge.post(dashboard.show_result, Result(True, "done", stop=True), "text")
        controller.bridge.next_call(timeout=0)
        assert len(came) == 1 and came[0].stop
    finally:
        dashboard.destroy()

