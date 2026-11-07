"""Day 6 step 1: the Bridge between Max's worker thread and the Tk thread.
Nothing here creates a window: the queue is tested on its own."""
import pytest

from ui.bridge import Bridge


def test_work_runs_in_the_order_it_was_posted():
    bridge = Bridge()
    seen = []
    assert bridge.post(seen.append, 1) is True
    assert bridge.post(seen.append, 2) is True
    bridge.drain()
    assert seen == [1, 2]
    assert bridge.pending() == 0


def test_drain_survives_a_broken_callback():
    """One bad callback must not swallow the ones behind it or kill the mainloop."""
    bridge = Bridge()
    seen = []

    def boom():
        raise ValueError("widget gone")

    bridge.post(boom)
    bridge.post(seen.append, "after")
    bridge.drain()  # must not raise
    assert seen == ["after"]


def test_closed_bridge_takes_no_new_work():
    bridge = Bridge()
    bridge.close()
    assert bridge.closed
    assert bridge.post(print, "never") is False
    bridge.drain()


def test_close_throws_away_pending_work():
    """The window is going away: a queued callback touches a destroyed widget,
    so it is dropped rather than run."""
    bridge = Bridge()
    ran = []
    bridge.post(ran.append, "dropped")
    bridge.close()
    bridge.drain()
    assert ran == []


def test_drain_on_an_empty_bridge_is_quiet():
    bridge = Bridge()
    bridge.drain()
    assert bridge.pending() == 0
