import json

from modules import agent_loop, personas, voice_handler
from ui import settings as settings_tab
from ui.web_app import state_event


def test_both_persona_files_load_and_differ():
    max_, mira = personas.load("max"), personas.load("mira")
    assert (max_["name"], max_["voice"]) == ("Max", "male")
    assert (mira["name"], mira["voice"]) == ("Mira", "female")
    assert "mira" in mira["wake_words"] and "max" in max_["wake_words"]
    assert personas.all_ids() == ["max", "mira"]


def test_unknown_or_unsafe_persona_ids_are_refused():
    for bad in ("nobody", "../config", "", None, "a/b"):
        assert personas.load(bad) is None
    assert personas.settings_for("nobody") is None


def test_persona_falls_back_to_max_and_sets_the_prompt_manner():
    assert personas.current({})["id"] == "max"
    assert personas.current({"PERSONA": "mira"})["id"] == "mira"
    prompt = agent_loop.system_prompt({"ASSISTANT_NAME": "Mira", "PERSONA": "mira"})
    assert "You are Mira" in prompt and "warm" in prompt


def test_persona_never_changes_the_safety_lines():
    for pid in ("max", "mira"):
        prompt = agent_loop.system_prompt({"PERSONA": pid})
        assert "Only say you did something if its tool result is ok" in prompt


def test_choosing_a_persona_sets_name_voice_and_wake_words_together():
    current = {"PERSONA": "max", "ASSISTANT_NAME": "Max", "VOICE": "male", "WAKE_WORDS": ["max"]}
    wanted = settings_tab.changes(current, {"PERSONA": "mira", "VOICE": "male", "ALERTS_ON": True})
    assert wanted["PERSONA"] == "mira" and wanted["ASSISTANT_NAME"] == "Mira"
    assert wanted["VOICE"] == "female" and "mira" in wanted["WAKE_WORDS"]
    assert wanted["ALERTS_ON"] is True


def test_same_persona_changes_nothing():
    current = {"PERSONA": "mira"}
    assert settings_tab.changes(current, {"PERSONA": "mira"}) == {}


def test_applying_a_persona_renames_the_agent_and_resets_its_session(monkeypatch):
    class A:
        config = {}
        name = "Max"
        session = object()
        wake_words = []

        def _build_commands(self):
            return []

    monkeypatch.setattr(voice_handler, "set_voice", lambda gender: None)
    agent = A()
    settings_tab.apply(agent, personas.settings_for("mira"))
    assert agent.name == "Mira" and agent.session is None


def test_speaking_flag_is_set_only_while_a_line_is_said(monkeypatch):
    seen = []
    monkeypatch.setattr(voice_handler, "_say", lambda text: seen.append(voice_handler.is_speaking()))
    voice_handler.speak("hello")
    assert seen == [True] and voice_handler.is_speaking() is False


def test_speaking_flag_clears_even_when_the_voice_fails(monkeypatch):
    def boom(text):
        raise RuntimeError("no audio")
    monkeypatch.setattr(voice_handler, "_say", boom)
    try:
        voice_handler.speak("hello")
    except RuntimeError:
        pass
    assert voice_handler.is_speaking() is False


def test_speaking_has_its_own_status():
    event = state_event("speaking")
    assert event["text"] == "Speaking" and event["tone"] == "speaking"


def test_the_page_has_persona_tint_speaking_bars_and_a_happy_flash():
    html = open("ui/web/index.html", encoding="utf-8").read()
    css = open("ui/web/style.css", encoding="utf-8").read()
    js = open("ui/web/app.js", encoding="utf-8").read()
    assert 'class="level"' in html
    assert 'body[data-tint="warm"]' in css and "box-shadow:" not in css
    assert "dataset.tint" in js and "is-happy" in js and "playbackRate" in js


def test_pill_sits_in_the_bottom_right_corner_above_the_taskbar():
    from ui.pill import corner, WIDTH, HEIGHT
    x, y = corner((1920, 1080))
    assert x + WIDTH < 1920 and y + HEIGHT < 1080
    assert corner((10, 10)) == (0, 0)


class _Window:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def evaluate_js(self, script):
        self.calls.append(script)

    def destroy(self):
        self.calls.append("destroy")


def test_pill_is_made_on_first_show_then_reused_and_keeps_the_latest_state(monkeypatch):
    import ui.pill as pill_module
    monkeypatch.undo()
    made = []

    def create(**kwargs):
        made.append(kwargs)
        return _Window()

    pill = pill_module.Pill(on_open=lambda: None, on_talk=lambda: None, create_window=create,
                            size_of_screen=lambda: (1920, 1080))
    pill.push({"t": "state", "text": "Speaking", "tone": "speaking"})   # before any window exists
    assert made == []
    assert pill.show() is True
    assert made[0]["frameless"] and made[0]["on_top"] and made[0]["focus"] is False
    assert "Speaking" in pill.window.calls[-1]
    pill.hide()
    assert pill.show() is True and len(made) == 1
    assert pill.window.calls[-3:-1] == ["hide", "show"]
    pill.destroy()
    assert pill.window is None


def test_pill_failure_means_no_pill_and_no_retry():
    import ui.pill as pill_module

    def broken(**kwargs):
        raise RuntimeError("no second window")

    pill = pill_module.Pill(on_open=lambda: None, on_talk=lambda: None, create_window=broken)
    assert pill.show() is False and pill.show() is False


def test_the_desktop_reaper_is_round_draggable_and_off_by_default():
    from ui.pill import Pet
    from ui import settings as s
    assert Pet.ROUND and Pet.PAGE == "pet.html" and Pet.TITLE != "KOVA"
    assert s.DEFAULTS["PET_ON"] is False
    assert s.changes({}, {"PET_ON": True}) == {"PET_ON": True}
    assert s.changes({"PET_ON": True}, {"PET_ON": True}) == {}
    html = open("ui/web/pet.html", encoding="utf-8").read()
    assert "dblclick" in html and "kovaPill" in html and "border-radius: 50%" in html


def test_turning_the_desktop_reaper_on_and_off_goes_through_the_window(monkeypatch):
    from tests.test_web_6_memory_settings import make_loaded_dashboard
    dashboard, window, controller = make_loaded_dashboard()
    try:
        calls = []
        monkeypatch.setattr(dashboard.pet, "show", lambda: calls.append("show") or True)
        monkeypatch.setattr(dashboard.pet, "hide", lambda: calls.append("hide"))
        dashboard.set_pet(True)
        dashboard.set_pet(False)
        assert calls == ["show", "hide"]
    finally:
        dashboard.destroy()


def test_the_desktop_reaper_follows_max_while_the_dashboard_is_hidden():
    from tests.test_web_6_memory_settings import make_loaded_dashboard
    from modules.result import Result
    dashboard, window, controller = make_loaded_dashboard()
    try:
        fake = _Window()
        dashboard.pet._window = fake        # the Reaper is on the desktop
        dashboard._visible = False          # the dashboard is minimised or in the tray
        controller._state = "thinking"
        dashboard._tick()
        assert any('"thinking"' in call for call in fake.calls)
        dashboard.show_result(Result(True, "Done."), "text")
        assert any('"done"' in call and '"success": true' in call for call in fake.calls)
    finally:
        dashboard.pet._window = None
        dashboard.destroy()


def test_the_pet_page_moves_only_with_real_states():
    html = open("ui/web/pet.html", encoding="utf-8").read()
    for state in ("is-listening", "is-thinking", "is-working", "is-speaking", "is-asleep"):
        assert state in html
    assert 'event.t === "done"' in html and "prefers-reduced-motion" in html
