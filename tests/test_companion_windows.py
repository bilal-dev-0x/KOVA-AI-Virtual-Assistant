"""Issue 2 from Bilal's live testing of 10 Nov: the rectangular floating
Open/Talk pill is gone from the desktop — the Mini Reaper persona is the only
companion, next to the tray.

Contract: the dashboard never builds a pill, hiding the dashboard opens no
extra window, teardown destroys the pet and nothing else, and the Pill class
survives only as the pet's base (show/hide/push/destroy and PillApi are
shared). The dashboard coming back from hidden, maximised, is Issue 1's UI
side and lives in test_own_window_moves.py; this file covers show_maximised
because it is the same window work.
"""
from types import SimpleNamespace

from tests.test_web_1_skeleton import FakeAgent, FakeWebViewWindow
from tests.test_web_4_activity import make_dashboard


def test_the_dashboard_has_no_pill_any_more():
    dashboard, window, controller = make_dashboard()
    try:
        assert not hasattr(dashboard, "pill")          # creation removed for good
        assert dashboard.pet is not None               # the Reaper stays
    finally:
        dashboard.destroy()


def test_hiding_the_dashboard_opens_no_companion_window():
    dashboard, window, controller = make_dashboard()
    try:
        dashboard._visible = True
        pet_calls = []
        dashboard.pet.show = lambda: pet_calls.append("show")
        dashboard.pet.hide = lambda: pet_calls.append("hide")

        dashboard.withdraw()

        assert window.hidden == 1                      # only the dashboard went away
        assert pet_calls == []                         # nothing opened beside the Reaper
        assert not hasattr(dashboard, "pill")          # and no pill exists to open
    finally:
        dashboard.destroy()


def test_show_maximised_puts_the_dashboard_back_up():
    """'maximise your window' with the dashboard hidden: show, restore,
    maximize — all on the pump thread, visible again, Map fired for the tray."""
    dashboard, window, controller = make_dashboard()
    try:
        unmapped_mapped = []
        dashboard.bind("<Map>", lambda event: unmapped_mapped.append("map"), add="+")
        dashboard._visible = False

        dashboard.show_maximised()

        assert window.shown == 1 and window.restored == 1 and window.maximized == 1
        assert dashboard._visible is True
        assert unmapped_mapped == ["map"]              # the tray's is_visible goes True
    finally:
        dashboard.destroy()


def test_destroy_closes_the_pet_and_nothing_else():
    dashboard, window, controller = make_dashboard()
    pet_calls = []
    dashboard.pet.destroy = lambda: pet_calls.append("pet")
    dashboard.destroy()
    assert pet_calls == ["pet"]                        # the Reaper is closed properly
    assert window.destroyed is True                    # no orphaned WebView left behind


def test_the_pill_survives_only_as_the_pets_base():
    """Pet is built on Pill — removing the class would break the pet's window
    lifecycle, so the base stays even though nothing shows a pill any more."""
    from ui.pill import Pet, Pill
    assert issubclass(Pet, Pill)
    assert Pet.show is Pill.show
