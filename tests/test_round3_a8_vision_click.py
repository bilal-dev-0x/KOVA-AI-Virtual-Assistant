import json

from modules import screen, tools, ui_automation, windows
from modules.result import Result


class SeeingProvider:
    can_look = True


def _miss(monkeypatch, name="Library"):
    monkeypatch.setattr(ui_automation, "click",
                        lambda *a, **k: Result(False, f"I can't find a control called {name} in Epic."))
    monkeypatch.setattr(ui_automation, "_target", lambda window="": (123, "Epic", "Epic Games Launcher"))
    monkeypatch.setattr(ui_automation, "risky", lambda _name: None)
    monkeypatch.setattr(ui_automation, "_same_window", lambda *_: None)
    monkeypatch.setattr(windows, "_get_rect", lambda _hwnd: (100, 50, 1000, 800))


def test_uia_miss_uses_confident_vision_coordinate_and_same_window(monkeypatch):
    _miss(monkeypatch)
    monkeypatch.setitem(tools._context, "provider", SeeingProvider())
    monkeypatch.setitem(tools._context, "config", {})
    seen = {}
    monkeypatch.setattr(screen, "look", lambda question, **kwargs: (
        seen.update(question=question, kwargs=kwargs) or Result(True, '{"x":500,"y":500,"confidence":0.91}')))
    monkeypatch.setattr(ui_automation, "click_at",
                        lambda hwnd, title, x, y: seen.update(click=(hwnd, title, x, y)) or True)
    result = tools.ui_click("Library")
    assert result.success
    assert "untrusted" in seen["question"]
    assert seen["kwargs"]["what"] == "window"
    assert seen["click"] == (123, "Epic Games Launcher", 600, 450)


def test_vision_click_stays_off_without_a_seeing_provider(monkeypatch):
    _miss(monkeypatch)
    monkeypatch.setitem(tools._context, "provider", None)
    monkeypatch.setitem(tools._context, "config", {})
    called = []
    monkeypatch.setattr(screen, "look", lambda *a, **k: called.append(True))
    result = tools.ui_click("Library")
    assert not result.success and "can't find" in result.message
    assert called == []


def test_risky_control_never_uses_the_vision_fallback(monkeypatch):
    _miss(monkeypatch, "Delete")
    monkeypatch.setattr(ui_automation, "risky", lambda _name: "delete")
    monkeypatch.setitem(tools._context, "provider", SeeingProvider())
    monkeypatch.setitem(tools._context, "config", {})
    called = []
    monkeypatch.setattr(screen, "look", lambda *a, **k: called.append(True))
    result = tools.ui_click("Delete")
    assert not result.success
    assert called == []


def test_low_confidence_or_invalid_coordinates_are_never_clicked(monkeypatch):
    _miss(monkeypatch)
    monkeypatch.setitem(tools._context, "provider", SeeingProvider())
    monkeypatch.setitem(tools._context, "config", {})
    monkeypatch.setattr(screen, "look", lambda *a, **k: Result(
        True, json.dumps({"x": 1001, "y": 500, "confidence": 0.99})))
    clicks = []
    monkeypatch.setattr(ui_automation, "click_at", lambda *a: clicks.append(a) or True)
    result = tools.ui_click("Library")
    assert not result.success
    assert "safely identify" in result.message
    assert clicks == []
