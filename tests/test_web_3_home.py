"""The new dashboard, step 3: Home and chat. The boot payload, the brain
footer and the four real quick actions are Python-side, so they are tested
here without a window; the page's own markup is checked against what the
Python side sends.
"""
from types import SimpleNamespace

from ui import web_app
from ui.web_app import QUICK_ACTIONS, boot_event, brain_footer


def test_brain_footer_names_the_real_brain():
    assert brain_footer(SimpleNamespace(provider=None)) == "Local commands only"
    assert brain_footer(SimpleNamespace(provider=SimpleNamespace(name="gemini"))) == "Gemini"
    assert brain_footer(SimpleNamespace(provider=SimpleNamespace(name="server"))) == "KOVA server"
    assert brain_footer(SimpleNamespace(provider=SimpleNamespace(name="relay"))) == "Relay, for testing"
    assert brain_footer(SimpleNamespace(provider=SimpleNamespace(name=None))) == "Local commands only"


def test_brain_footer_tells_when_gemini_has_handed_over():
    resting = SimpleNamespace(name="gemini+server", primary=object(), primary_ok=lambda: False)
    assert brain_footer(SimpleNamespace(provider=resting)) == "Brain: KOVA server (Gemini resting)"
    healthy = SimpleNamespace(name="gemini+server", primary=object(), primary_ok=lambda: True)
    assert brain_footer(SimpleNamespace(provider=healthy)) == "Brain: Gemini, with the KOVA server as backup"


def test_all_quick_actions_are_real_commands():
    assert QUICK_ACTIONS == (
        ("System status", "system status"),
        ("My memory", "what do you know about me"),
        ("My reminders", "what reminders do I have"),
        ("Front window", "which window is in front"),
    )


class FakeAgent:
    name = "Max"
    title = "Commander"

    def __init__(self):
        self.provider = SimpleNamespace(name="gemini")
        self.config = {"ASSISTANT_NAME": "Max", "USER_TITLE": "Commander", "WAKE_WORDS": ["max", "mac"]}


def test_boot_event_carries_everything_the_page_needs_and_nothing_of_the_config():
    agent = FakeAgent()
    event = boot_event(agent, "Gemini")
    assert event == {
        "t": "boot",
        "assistant": "Max",
        "title": "Commander",
        "brain": "Gemini",
        "wake": "max",
        "persona": "max",
        "tint": "none",
        "quick": [{"label": label, "send": command} for label, command in QUICK_ACTIONS],
    }
    assert set(event) == {"t", "assistant", "title", "brain", "wake", "persona", "tint", "quick"}
    flattened = str(event)
    for secret in ("API_KEY", "BRAIN_PROVIDER", "SERVER_IP"):
        assert secret not in flattened


def test_the_dashboard_sends_boot_first_when_the_page_loads():
    from tests.test_web_1_skeleton import FakeWebViewWindow, js_values
    from ui.bridge import Bridge
    from ui.controller import Controller
    from ui import web_app as module

    window = FakeWebViewWindow()
    agent = FakeAgent()
    agent.config = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}
    controller = Controller(agent, Bridge())
    dash = module.WebDashboard(controller, agent, create_window=lambda **kw: window,
                               stats_factory=lambda push: SimpleNamespace(start=lambda: None, stop=lambda: None))
    dash._pump_stop.set()
    try:
        window.events.fire("loaded")
        dash._tick()
        boot = js_values(window)[0]
        assert boot["t"] == "boot" and boot["assistant"] == "Max"
        assert boot["brain"] == "Gemini"
        assert [q["send"] for q in boot["quick"]][0] == "system status"
    finally:
        dash.destroy()


def test_the_ask_bar_and_quick_strip_exist_on_the_page():
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "ui" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")

    for element in ("ask-input", "ask-mic", "ask-send", "quick", "chat-log", "greeting", "home-date"):
        assert f'id="{element}"' in html, element
    assert "api.send(text)" in js and "api.talk()" in js   # typed and the mic button
    assert "textContent = text" in js                     # chat is never built from raw text
