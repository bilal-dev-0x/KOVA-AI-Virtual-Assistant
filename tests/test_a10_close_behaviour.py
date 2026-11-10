"""A10: what the X on the dashboard does — regression tests for the real
failure Bilal hit on 10 Nov.

Root cause: the closing handler runs on pywebview's GUI thread. The old
version did the hide right there, so the first X ran pill.show() on the main
thread; pywebview refuses to create native windows from the main thread while
its loop runs, and the wrapped show() then waited up to 20 seconds for an
event only the GUI thread itself could set — "Program Not Responding". A quit
posted while the pump was wedged behind that freeze never ran, so the second
X closed only the dashboard and the pet plus tray survived.

Contract now: the handler only decides and posts; the pump does every bit of
window work, exactly like the tray's hide and Quit always did. Nothing on the
GUI thread, ever — not on the first X, not on the tenth.
"""
import threading
import time

from tests.test_web_4_activity import make_dashboard


def closing_answer(dashboard):
    """Press X once: the handler's return value cancels (False) or allows
    (anything else) the native close, just like pywebview reads it."""
    handler = dashboard._window.events.rules["closing"][-1]
    return handler()


def wait_done(predicate, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_default_switch_is_off():
    dashboard, window, controller = make_dashboard()
    try:
        assert dashboard.api.settings()["CLOSE_ON_X"] is False
    finally:
        dashboard.destroy()


def test_saved_value_sits_on_top_of_the_false_default():
    from ui import settings as settings_tab
    assert settings_tab.DEFAULTS["CLOSE_ON_X"] is False
    # saving true writes true; saving the same value writes nothing
    assert settings_tab.changes({"CLOSE_ON_X": False}, {"CLOSE_ON_X": True}) == {"CLOSE_ON_X": True}
    assert settings_tab.changes({"CLOSE_ON_X": False}, {"CLOSE_ON_X": False}) == {}
    # a dashboard started with true reports true without a restart
    dashboard, window, controller = make_dashboard()
    try:
        dashboard.agent.config["CLOSE_ON_X"] = True
        assert dashboard.api.settings()["CLOSE_ON_X"] is True
    finally:
        dashboard.destroy()


def test_x_touches_no_window_on_the_gui_thread():
    """The freeze regression: X must not run the hide inline. The window is
    still exactly as it was the instant the handler returns; the work sits on
    the bridge for the pump thread."""
    dashboard, window, controller = make_dashboard()
    try:
        dashboard._visible = True
        assert closing_answer(dashboard) is False       # native close cancelled
        assert window.hidden == 0                        # nothing happened here
        assert window.destroyed is False
        assert dashboard._visible is True                # still on screen
        assert controller.bridge.pending() == 1          # hide queued for the pump
    finally:
        dashboard.destroy()


def test_pump_delivers_the_hide_like_the_tray_does():
    dashboard, window, controller = make_dashboard()
    try:
        dashboard._visible = True
        pets, unmapped = [], []
        dashboard.pet.hide = lambda: pets.append("hide")
        dashboard.pet.destroy = lambda: pets.append("destroy")
        dashboard.bind("<Unmap>", lambda event: unmapped.append(event), add="+")

        assert closing_answer(dashboard) is False
        dashboard._tick()                                 # the pump runs the queued hide
        assert window.hidden == 1 and window.destroyed is False
        assert dashboard._alive is True and dashboard._visible is False
        assert unmapped == [None]                         # the tray's is_visible goes False
        assert pets == []                                 # the pet was not touched at all
    finally:
        dashboard.destroy()


def test_x_without_a_tray_quits_through_the_same_off_thread_road():
    """run_ui points on_hide at a quit when no tray could start — X follows
    that path, still posted, never inline."""
    dashboard, window, controller = make_dashboard()
    try:
        taken = []
        dashboard.on_hide = lambda: taken.append("on-hide")
        assert closing_answer(dashboard) is False
        assert taken == [] and controller.bridge.pending() == 1
        dashboard._tick()
        assert taken == ["on-hide"]
    finally:
        dashboard.destroy()


def test_repeated_x_in_hide_mode_stays_safe():
    dashboard, window, controller = make_dashboard()
    try:
        dashboard._visible = True
        closing_answer(dashboard)
        closing_answer(dashboard)                         # pump may not have run yet
        assert controller.bridge.pending() == 2           # two hides queued, no work done twice inline
        dashboard._tick()
        dashboard._tick()
        assert window.hidden == 2                         # both delivered, no error, no freeze
        assert window.destroyed is False
    finally:
        dashboard.destroy()


def test_hidden_dashboard_opens_again_through_pet_or_tray():
    dashboard, window, controller = make_dashboard()
    try:
        dashboard._visible = True
        closing_answer(dashboard)
        dashboard._tick()
        assert dashboard._visible is False
        dashboard.show()   # what the pet's double-click and the tray both call
        assert window.shown == 1 and dashboard._visible is True
    finally:
        dashboard.destroy()


def test_x_with_the_switch_on_posts_the_quit():
    dashboard, window, controller = make_dashboard()
    dashboard.agent.config["CLOSE_ON_X"] = True
    gone = []
    dashboard.pet.destroy = lambda: gone.append("pet")
    dashboard.on_exit = lambda value=None: dashboard.destroy()

    assert closing_answer(dashboard) is False            # native close cancelled…
    assert controller.bridge.pending() == 1              # …the quit went to the pump
    assert gone == [] and window.destroyed is False      # nothing torn down on the GUI thread
    dashboard._tick()                                    # the pump runs the quit
    assert gone == ["pet"]                               # dashboard and pet down
    assert window.destroyed is True
    assert controller.bridge.pending() == 0


def test_x_with_the_switch_on_but_no_exit_wiring_still_quits():
    """A dashboard used without run_ui (on_exit never set) must not hang on X:
    the quit falls back to destroy() itself, still on the pump."""
    dashboard, window, controller = make_dashboard()
    dashboard.agent.config["CLOSE_ON_X"] = True
    assert closing_answer(dashboard) is False
    assert controller.bridge.pending() == 1
    dashboard._tick()
    assert window.destroyed is True


def test_a_second_x_while_the_quit_is_queued_is_harmless():
    dashboard, window, controller = make_dashboard()
    dashboard.agent.config["CLOSE_ON_X"] = True
    dashboard.on_exit = lambda value=None: dashboard.destroy()
    assert closing_answer(dashboard) is False
    assert closing_answer(dashboard) is None   # the quit is queued: no second post
    assert controller.bridge.pending() == 1    # only one quit was posted
    dashboard._tick()
    assert window.destroyed is True


def test_destroy_closing_the_window_lets_the_native_close_pass():
    """The shutdown's own Close() raises closing a second time on pywebview —
    cancelling it from there would leave KOVA half alive, window gone but the
    process running."""
    dashboard, window, controller = make_dashboard()
    dashboard.destroy()
    assert closing_answer(dashboard) is None   # no quit reposted, no hide, no cancel


def test_x_still_works_when_the_bridge_is_already_closed():
    dashboard, window, controller = make_dashboard()
    try:
        controller.bridge.close()
        dashboard._visible = True
        assert closing_answer(dashboard) is False
        assert wait_done(lambda: window.hidden == 1)   # the one-shot thread hid it
    finally:
        dashboard.destroy()


def test_pump_survives_a_broken_step_and_still_delivers_the_quit(monkeypatch):
    """A single raising pump step must not kill the drain: the quit that X
    posts depends on this loop being alive until the very end."""
    dashboard, window, controller = make_dashboard()
    dashboard.agent.config["CLOSE_ON_X"] = True
    dashboard.on_exit = lambda value=None: dashboard.destroy()
    dashboard._pump_stop.clear()

    real_tick = dashboard._tick
    steps = {"n": 0}

    def flaky_tick():
        steps["n"] += 1
        if steps["n"] == 1:
            raise RuntimeError("one bad step")
        real_tick()

    monkeypatch.setattr(dashboard, "_tick", flaky_tick)
    closing_answer(dashboard)                          # posts the quit
    worker = threading.Thread(target=dashboard._pump_loop, daemon=True)
    worker.start()
    assert wait_done(lambda: window.destroyed)         # the quit still lands
    worker.join(timeout=1)
    assert not worker.is_alive()
