from ui.web_app import WebDashboard


def test_refresh_boot_updates_dashboard_window_title():
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard.agent = type("Agent", (), {"name": "Mira"})()
    dashboard._window = type("Window", (), {"set_title": lambda self, title: setattr(self, "last_title", title)})()
    dashboard._push = lambda event: None
    dashboard.pill = type("Surface", (), {"push": lambda self, event: None})()
    dashboard.pet = type("Surface", (), {"push": lambda self, event: None})()
    dashboard._shown_state = "idle"
    dashboard._my_boot_event = lambda: {}
    dashboard.refresh_boot()
    assert dashboard._title == "KOVA — Mira"
    assert dashboard._window.last_title == "KOVA — Mira"
    assert dashboard._shown_state is None
