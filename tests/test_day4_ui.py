"""Day 4 steps 4 and 5: UI Automation and multi-step plans that stop safely. A fake window with fake
controls: nothing is read, clicked or typed on the real desktop."""
import pytest

from brain.base import Reply, ToolCall
from desktop_agent import Agent
from modules import system_control, ui_automation as ui, windows
from modules.ui_automation import Control
from tests.conftest import FakeProvider

CONFIG = {"ASSISTANT_NAME": "Max", "WAKE_WORDS": ["max"]}


class FakeApp:
    """One window in front with controls; records what Max did to it."""

    def __init__(self):
        self.title = "Untitled - Notepad"
        self.titles = []          # titles to report next, one per check (then self.title)
        self.controls = [Control("File", "menu"), Control("Edit", "menu"), Control("Save", "button"),
                         Control("Text Editor", "text area")]
        self.did = []
        self.admin = False
        self.hung = False
        self.empty = True         # the field is empty, so its value can be set
        self.focus_stays = True
        self.send_ok = True
        self.popup = None         # (hwnd, title) of a dialog Notepad has open, like Save As

    def window_title(self, hwnd):
        if self.popup and hwnd == self.popup[0]:
            return self.popup[1]
        return self.titles.pop(0) if self.titles else self.title


@pytest.fixture
def app(monkeypatch):
    fake = FakeApp()
    monkeypatch.setattr(windows, "visible_windows", lambda: [(7, "notepad.exe", fake.title)])
    monkeypatch.setattr(windows, "_is_minimised", lambda h: False)
    monkeypatch.setattr(windows, "_active_popup", lambda h: fake.popup[0] if fake.popup else None)
    monkeypatch.setattr(windows, "_runs_as_admin", lambda h: fake.admin)
    monkeypatch.setattr(system_control, "_window_title", fake.window_title)
    monkeypatch.setattr(system_control, "_bring_to_front", lambda h: True)
    monkeypatch.setattr(ui, "_is_hung", lambda h: fake.hung)
    monkeypatch.setattr(ui, "_is_terminal", lambda h: fake.terminal)
    fake.terminal = False
    monkeypatch.setattr(ui, "_read_tree", lambda h, deadline, depth: list(fake.controls))
    monkeypatch.setattr(ui, "_invoke", lambda c: fake.did.append(("click", c.name)) or True)

    def set_value(c, text):
        if fake.empty:
            fake.did.append(("set", c.name, text))
        return fake.empty
    monkeypatch.setattr(ui, "_set_empty_value", set_value)
    monkeypatch.setattr(ui, "_replace_value", lambda c, text: fake.did.append(("replace", c.name, text)) or True)
    # Windows' own dialogs have the class #32770
    monkeypatch.setattr(ui, "_window_class", lambda h: "#32770" if fake.popup and h == fake.popup[0] else "Notepad")
    monkeypatch.setattr(ui, "_focus", lambda h, c: True)
    monkeypatch.setattr(ui, "_still_focused", lambda c: fake.focus_stays)
    monkeypatch.setattr(ui, "_type_keys", lambda text: fake.did.append(("type", text)) or fake.send_ok)
    monkeypatch.setattr(ui, "_press", lambda keys: fake.did.append(("press", keys)) or fake.send_ok)
    return fake


class Answers:
    def __init__(self, *answers):
        self.answers, self.questions = list(answers), []

    def __call__(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False


def max_agent(*answers, provider=None):
    asked = Answers(*answers)
    return Agent(dict(CONFIG), confirm=asked, provider=provider), asked


# ---------- reading ----------

def test_what_can_i_click_here(app):
    result = max_agent()[0].handle("what can I click here?")
    assert result.message == "In notepad, Untitled, I can see menus: File, Edit; buttons: Save; text areas: Text Editor."


def test_hung_and_admin_windows(app):
    app.hung = True
    assert ui.describe().message == "notepad isn't responding, so I can't read it."
    app.hung, app.admin = False, True
    assert "running as admin" in ui.click("Save").message and app.did == []


# ---------- clicking ----------

def test_click_by_name_asks_first(app):
    agent, asked = max_agent(True)
    assert agent.handle("click Save").message == "Clicked Save."
    assert asked.questions == ["Click save in the window in front?"]
    assert app.did == [("click", "Save")]


def test_one_match_only(app):
    app.controls += [Control("Save as", "menu"), Control("Save", "menu")]
    result = ui.click("save")
    assert result.halt and "There are 2 called save" in result.message and app.did == []
    assert ui.click("print").message == "I can't find a control called print in notepad."


def test_the_window_changed_before_the_click(app):
    app.titles = ["Save As"]  # the title checked right before the click
    result = ui.click("Save")
    assert result.halt and result.message == "The window changed to Save As, so I stopped."
    assert app.did == []


def test_the_window_the_plan_expected(app):
    result = ui.click("Save", window="chrome")
    assert result.halt and "is in front, not chrome" in result.message


@pytest.mark.parametrize("name, word", [("Delete", "delete"), ("Send message", "send"), ("Sign out", "sign out"),
                                        ("Pay now", "pay"), ("Sender", None), ("Payload", None)])
def test_risky_names(name, word):
    assert ui.risky(name) == word


def test_a_risky_click_asks_again_right_before(app):
    app.controls.append(Control("Delete", "button"))
    agent, asked = max_agent(True, False)
    result = agent.handle("click delete")
    assert asked.questions == ["Click delete in the window in front?", "Click Delete in notepad? It can't be undone."]
    assert result.message == "Okay, I didn't click Delete." and app.did == []


def test_the_agents_risky_click_asks_again_too(app):
    app.controls.append(Control("Send", "button"))
    provider = FakeProvider([Reply(calls=[ToolCall("ui_click", {"name": "Send"})])])
    agent, asked = max_agent(True, True, provider=provider)
    agent.handle("send the message please and then tell me")
    assert asked.questions == ["Should I click Send?", "Click Send in notepad? It can't be undone."]
    assert app.did == [("click", "Send")]


# ---------- typing ----------

def test_type_into_an_empty_field_sets_its_value(app):
    assert max_agent()[0].handle("type Hello World into the text area").success
    assert app.did == [("set", "Text Editor", "Hello World")]


def test_text_already_there_is_kept(app):
    app.empty = False
    assert ui.type_text("more", "text editor").success
    assert app.did == [("type", "more")]


def test_password_fields_are_never_typed_into(app):
    app.controls = [Control("Password", "field", password=True)]
    result = ui.type_text("1234", "password")
    assert result.halt and "password field" in result.message and app.did == []


def test_a_one_line_box_gets_no_new_line(app):
    app.controls = [Control("Message", "field")]
    result = ui.type_text("hi\nthere", "message")
    assert not result.success and "one line" in result.message and app.did == []
    app.controls = [Control("Text Editor", "text area")]
    app.empty = False
    assert ui.type_text("hi\nthere").success and app.did == [("type", "hi\nthere")]


def test_too_long(app):
    assert "too long" in ui.type_text("x" * (ui.MAX_TEXT + 1)).message


def test_focus_moved_before_typing(app):
    app.empty, app.focus_stays = False, False
    result = ui.type_text("hello")
    assert result.halt and "cursor left" in result.message and app.did == []


def test_windows_blocking_the_typing_is_reported(app):
    app.empty, app.send_ok = False, False
    result = ui.type_text("hello")
    assert result.halt and "blocked the typing" in result.message


def test_several_text_fields_need_a_name(app):
    app.controls = [Control("To", "field"), Control("Subject", "field")]
    assert "has 2 text fields: To, Subject" in ui.type_text("hello").message


def test_text_events():
    events = ui.text_events("a\n😀")
    assert events[2:4] == [(0x0D, 0, 0), (0x0D, 0, 2)]   # the new line is Enter
    assert len(events) == 2 + 2 + 4                     # the emoji is two UTF-16 units


# ---------- keys ----------

@pytest.mark.parametrize("said, combo", [("ctrl s", "ctrl+s"), ("Control+S", "ctrl+s"), ("enter", "enter"),
                                         ("esc", "esc"), ("alt f4", None), ("win r", None)])
def test_key_combo(said, combo):
    assert ui.key_combo(said) == combo


def test_press_keys(app):
    assert max_agent()[0].handle("press ctrl s").message == "Pressed ctrl+s in notepad."
    assert app.did == [("press", ["ctrl", "s"])]
    assert "only press everyday keys" in ui.press_keys("alt f4").message


@pytest.mark.parametrize("said, command", [
    ("what can i click here", "ui read"), ("press enter", "press keys"), ("press ctrl s", "press keys"),
    ("click save", "click"), ("type hello into the search box", "type"),
    ("press the start menu", "start menu"),     # still the start menu
])
def test_ui_phrases(said, command):
    assert max_agent()[0].match(said)[0].name == command


def test_write_a_poem_is_not_typed_anywhere():
    command, _ = max_agent()[0].match("write a poem about rain")
    assert command is None


# ---------- step 5: a plan stops safely ----------

def test_a_plan_halts_when_the_window_changes(app):
    app.empty = False
    provider = FakeProvider([
        Reply(calls=[ToolCall("ui_press_keys", {"keys": "ctrl+a"})]),
        Reply(calls=[ToolCall("ui_type", {"text": "shopping list"}), ToolCall("ui_click", {"name": "Save"})]),
    ])
    agent, asked = max_agent(True, True, provider=provider)
    app.titles = ["Untitled - Notepad", "Untitled - Notepad", "Save changes?"]  # a popup appears mid-plan
    result = agent.handle("select everything in notepad, then type shopping list and save it")
    assert not result.success
    assert result.message == ("The window changed to Save changes?, so I stopped. "
                              "I already did: press ctrl+a, type 'shopping list' into the text field.")
    assert ("click", "Save") not in app.did              # the step after the halt never ran
    assert provider.fake_session.resets == 1             # no half-answered plan left in the conversation


def test_a_halt_in_the_first_step_says_nothing_ran(app):
    provider = FakeProvider([Reply(calls=[ToolCall("ui_click", {"name": "Save", "window": "chrome"})])])
    agent, _ = max_agent(True, provider=provider)
    result = agent.handle("click save in chrome and then close it")
    assert result.message == "Untitled is in front, not chrome, so I stopped. Nothing else ran."


def test_an_open_dialog_is_the_window_in_front(app):
    app.popup = (8, "Save As")
    assert windows.target_window() == (8, "notepad", "Save As")
    assert ui.click("Save", window="save as").success


def test_a_dialog_popping_up_before_the_click_halts(app, monkeypatch):
    real_one = ui._one

    def read_then_popup(controls, name, app_name, editable):
        found = real_one(controls, name, app_name, editable)
        app.popup = (9, "Do you want to save changes?")   # appears between the read and the click
        return found
    monkeypatch.setattr(ui, "_one", read_then_popup)
    result = ui.click("Save")
    assert result.halt and result.message == "Do you want to save changes? popped up in front, so I stopped."
    assert app.did == []


def test_the_demo_save_to_desktop_plan(app, monkeypatch):
    """"open notepad, type shopping list, then save it to my desktop" as the brain would plan it."""
    def press(keys):
        app.did.append(("press", keys))
        app.popup = (8, "Save As")                         # ctrl+s opens the Save As dialog
        app.controls = [Control("File name:", "field"), Control("Save", "button"), Control("Cancel", "button")]
        return True
    monkeypatch.setattr(ui, "_press", press)
    path = r"C:\Users\Bilal\Desktop\shopping list.txt"
    provider = FakeProvider([
        Reply(calls=[ToolCall("ui_type", {"text": "shopping list", "field": "text area", "window": "notepad"})]),
        Reply(calls=[ToolCall("ui_press_keys", {"keys": "ctrl+s", "window": "notepad"})]),
        Reply(calls=[ToolCall("ui_type", {"text": path, "field": "file name", "window": "save as"})]),
        Reply(calls=[ToolCall("ui_click", {"name": "Save", "window": "save as"})]),
        Reply(text="Saved it to your desktop."),
    ])
    agent, asked = max_agent(True, True, True, True, provider=provider)
    result = agent.handle("type shopping list in notepad, then save it to my desktop")
    assert result.success and result.message == "Saved it to your desktop."
    assert app.did == [("set", "Text Editor", "shopping list"), ("press", ["ctrl", "s"]),
                       ("replace", "File name:", path), ("click", "Save")]
    assert len(asked.questions) == 4                     # one approval per round, nothing risky


def test_the_save_dialogs_file_name_is_replaced_but_other_text_is_kept(app):
    app.empty = False
    app.popup = (8, "Save As")
    app.controls = [Control("File name:", "field"), Control("Search", "field")]
    assert ui.type_text("notes.txt", "file name").success
    assert ui.type_text("cats", "search").success
    assert app.did == [("replace", "File name:", "notes.txt"), ("type", "cats")]
    # The same name outside Windows' own dialog is ordinary text: typed at the cursor
    app.popup, app.did = None, []
    app.controls = [Control("File name:", "field")]
    assert ui.type_text("x", "file name").success and app.did == [("type", "x")]


def test_never_types_clicks_or_presses_in_a_terminal(app):
    # Found in the live test: behind Max's own console was Claude Code's terminal, and the plan typed
    # into it and pressed Enter. Typing + Enter in a terminal runs a command.
    app.terminal = True
    app.empty = False
    for result in (ui.type_text("dir"), ui.press_keys("enter"), ui.click("Save")):
        assert result.halt and "never type, click or press keys in a terminal" in result.message
    assert app.did == []
    assert ui.describe().success  # reading it is harmless


def test_backspace_clears_after_select_all(app):
    assert ui.key_combo("backspace") == "backspace"
    assert ui.press_keys("backspace").success and app.did == [("press", ["backspace"])]


def test_ui_tool_levels():
    from modules import tool_registry
    get = tool_registry.get
    assert get("ui_read").level == "safe"
    assert get("ui_click").level == get("ui_type").level == get("ui_press_keys").level == "controlled"


def test_click_the_chat_by_its_name(app):
    # Voice test: "click the chat Rehan" found nothing; the list item is just "Rehan"
    app.controls = [Control("Rehan", "list item"), Control("Ahmed", "list item")]
    agent, _ = max_agent(True)
    assert agent.handle("click the chat rehan").message == "Clicked Rehan."


def test_open_something_inside_the_app_goes_to_the_agent(app):
    # Voice test: "open Mr Ahmed chat" -> "I don't know an app or website called mr ahmed chat"
    provider = FakeProvider([Reply(calls=[ToolCall("ui_click", {"name": "Mr Ahmed", "window": "notepad"})])])
    app.controls.append(Control("Mr Ahmed", "list item"))
    agent, _ = max_agent(True, provider=provider)
    agent.handle("open mr ahmed chat")
    assert ("click", "Mr Ahmed") in app.did


def test_a_long_roman_urdu_question_with_gpu_goes_to_the_brain():
    # Voice test: "yah meri gpu per sahi chal jaenge na" got a GPU report instead of an answer
    provider = FakeProvider([Reply(text="Haan, chal jayenge.")])
    agent, _ = max_agent(provider=provider)
    assert agent.handle("yah meri gpu per sahi chal jaenge na").message == "Haan, chal jayenge."
    assert agent.match("what is my gpu usage right now")[0].name == "gpu"   # a plain question stays local


def test_search_in_steam():
    from desktop_agent import search_args
    assert search_args("titanfall 2 in steam") == {"query": "titanfall 2", "site": "steam"}
    assert search_args("steam for stardew valley") == {"query": "stardew valley", "site": "steam"}
