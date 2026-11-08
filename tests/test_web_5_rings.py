"""The new dashboard, step 5: the CPU/RAM/disk rings on Home. The numbers come
from the sampler, the disk is read on the sampler thread, and everything is
packaged through the bridge — tested with fakes, no psutil call of its own.
"""
from types import SimpleNamespace

from ui import web_app

from tests.test_web_1_skeleton import FakeAgent, FakeWebViewWindow, js_values


def make_dashboard():
    window = FakeWebViewWindow()
    from ui.bridge import Bridge
    from ui.controller import Controller

    fresh = Controller(FakeAgent(), Bridge())
    dash = web_app.WebDashboard(
        fresh, fresh.agent, create_window=lambda **kw: window,
        stats_factory=lambda push: SimpleNamespace(start=lambda: None, stop=lambda: None))
    dash._pump_stop.set()
    window.events.fire("loaded")     # the page is up; the boot event rides out now
    dash._flush_pending()
    return dash, window, fresh


def test_ring_percentages_come_from_the_reading_or_stay_unknown():
    assert web_app.ring_percentages({"cpu": "71%", "ram_percent": "39%"}, disk=0.42) == \
        {"cpu": 0.71, "ram": 0.39, "disk": 0.42}
    assert web_app.ring_percentages({"battery": "85% on battery"}, disk=None) == \
        {"cpu": None, "ram": None, "disk": None}


def test_disk_percent_prefers_the_c_drive(monkeypatch):
    from modules import performance_monitor as perf

    monkeypatch.setattr(perf, "disk_info",
                        lambda: [{"drive": "D:", "percent": 12}, {"drive": "C:", "percent": 76}])
    assert web_app.disk_percent() == 0.76
    monkeypatch.setattr(perf, "disk_info",
                        lambda: [{"drive": "f:\\", "percent": 55}])
    assert web_app.disk_percent() == 0.55   # only drive that exists
    monkeypatch.setattr(perf, "disk_info", lambda: [])
    assert web_app.disk_percent() is None
    monkeypatch.setattr(perf, "disk_info", lambda: [{"drive": "C:", "percent": None}])
    assert web_app.disk_percent() is None


def test_disk_percent_never_guesses(monkeypatch):
    from modules import performance_monitor as perf

    monkeypatch.setattr(perf, "disk_info", lambda: None)
    assert web_app.disk_percent() is None
    monkeypatch.setattr(perf, "disk_info", lambda: {"drive": "C:"})   # no percent key
    assert web_app.disk_percent() is None


def test_one_stats_reading_rides_the_bridge_with_its_rings():
    dash, window, controller = make_dashboard()
    dash._disk_reader = lambda: 0.3
    dash._on_stats({"cpu": "12%", "ram_percent": "39%", "battery": None, "gpu": None})
    controller.bridge.next_call(timeout=0)
    payload = [p for p in js_values(window) if p["t"] == "stats"][-1]
    assert payload == {"t": "stats", "rings": {"cpu": 0.12, "ram": 0.39, "disk": 0.3}}


def test_the_sampler_thread_s_own_push_is_the_event_packer():
    dash, window, controller = make_dashboard()
    holder = {}

    def stats_factory(push):
        holder["push"] = push
        return SimpleNamespace(start=lambda: None, stop=lambda: None)

    from ui.bridge import Bridge
    from ui.controller import Controller

    fresh = Controller(FakeAgent(), Bridge())
    dash2 = web_app.WebDashboard(fresh, fresh.agent, create_window=lambda **kw: window,
                                 stats_factory=stats_factory)
    dash2._pump_stop.set()
    try:
        window.events.fire("loaded")   # both dashboards' loaders now say the page is up
        dash2._flush_pending()         # and the boot facts ride out first
        assert getattr(holder["push"], "__name__", "") == "_on_stats"
        # with the page loaded, the sampler's own push drives the page at once:
        dash2._disk_reader = lambda: 0.66
        holder["push"]({"cpu": "7%", "ram_percent": "55%", "battery": None, "gpu": None})
        fresh.bridge.next_call(timeout=0)   # the posted show_stats runs on the pump
        payload = [p for p in js_values(window) if p["t"] == "stats"][-1]
        assert payload["rings"] == {"cpu": 0.07, "ram": 0.55, "disk": 0.66}
    finally:
        dash2.destroy()


def test_home_has_three_real_meters():
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "ui" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")

    for key in ("cpu", "ram", "disk"):
        assert f'id="meter-{key}"' in html
        assert f'id="meter-{key}-value"' in html
    assert "style.width" in js                 # the bar's width is the reading
    assert "known ? Math.round(value * 100)" in js   # an unknown figure is never shown as a number
