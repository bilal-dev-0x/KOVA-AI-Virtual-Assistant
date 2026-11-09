import json
import os
import threading
import time

from desktop_agent import wake_words_from
from modules import personas, startup, voice_handler
from modules.logger import get_logger
from ui import memory as memory_tab
from ui import history as history_tab
from ui import messages as messages_tab
from ui import settings as settings_tab
from ui import stats as stats_tab
from ui import data as data_tab
from ui.pill import Pet, Pill

# The new dashboard window: PyWebView (Edge WebView2) showing ui/web/. Only the
# window layer of the classic CustomTkinter app is replaced here — the same
# Controller, Bridge, settings, memory and stats are reused, and the window
# keeps the public shape run_ui() drives (bind, prompt, show, withdraw,
# mainloop...).
#
# Python pushes events to the page with evaluate_js; the page calls back
# through JsApi. Everything from the agent travels through the Bridge and is
# drained by one pump thread, so evaluate_js is only ever called from one
# place. Events raised before the page has loaded wait in a buffer instead of
# being lost, and stopping the sampler while the window is hidden works the
# same way, so an idle dashboard costs nothing.

log = get_logger()

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
BUFFER_KEEP = 500
ERROR_SHOWN_SECONDS = 6   # how long "Request failed" stays before the real state shows again

# Max's states, the ones the classic window showed, now as a short key the page
# turns into a colour.
STATES = {"idle": ("Ready", "idle"),
          "asleep": ("Asleep — say the wake word", "asleep"),
          "awake": ("Listening", "listening"),
          "listening": ("Listening", "listening"),
          "thinking": ("Thinking", "thinking"),
          "speaking": ("Speaking", "speaking"),
          "working": ("Working", "working"),
          "approval": ("Waiting for approval", "approval"),
          "error": ("Request failed", "error"),
          "muted": ("Voice off", "muted")}

# What a dialog answers when nobody is left to answer it: closing a confirm is
# No, closing an ask is empty.
DEFAULT_ANSWERS = {"confirm": False, "ask": None}


def disk_percent():
    """How full the main drive is, 0..1, so the page's ring can show it.
    None when nothing readable is there."""
    try:
        from modules import performance_monitor as perf
        disks = perf.disk_info()
        chosen = next((d for d in disks if d["drive"].upper().startswith("C")), disks[0])
        return max(0.0, min(1.0, chosen["percent"] / 100.0))
    except Exception as e:
        log.debug("Disk ring reading failed: %s", e)
        return None


def ring_percentages(reading, disk):
    """The three rings' values, 0..1 each. A figure this PC cannot read stays
    None, and the ring never gets guessed."""
    return {"cpu": messages_tab.percent(reading.get("cpu")),
            "ram": messages_tab.percent(reading.get("ram_percent")),
            "disk": disk}


def state_parts(state):
    """(label, tone) for the sidebar status. Unknown states still get a label."""
    return STATES.get(state, (state, "idle"))


def state_event(state):
    text, tone = state_parts(state)
    return {"t": "state", "state": state, "text": text, "tone": tone}


def js_receive(payload):
    """The one JS entry point Python pushes events through. json.dumps keeps
    every string JS-safe: the page is never assembled out of raw text."""
    return "window.kova.receive(%s)" % json.dumps(payload)


def webview_available():
    """True when the new window can open: pywebview imports and the Edge
    WebView2 runtime is registered. main.py falls back to the classic window."""
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return webview_runtime_found()


def webview_runtime_found():
    """Windows: look the WebView2 runtime up in the registry, the way the
    official detection does. On other systems there is nothing to check."""
    if os.name != "nt":
        return True
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for path in (r"Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                         r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"):
                try:
                    winreg.OpenKey(hive, path)
                    return True
                except OSError:
                    continue
    except Exception as e:
        log.debug("WebView2 runtime registry check failed: %s", e)
    return os.path.isdir(r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application")


BRAIN_NAMES = {"gemini": "Gemini", "openrouter": "OpenRouter", "server": "KOVA server", "relay": "Relay"}


def brain_footer(agent):
    """The real brain answering right now, said plainly for the footer."""
    provider = getattr(agent, "provider", None)
    if provider is None:
        return "Local commands only"
    name = getattr(provider, "name", "none")
    if name and name != "gemini+server" and "+" in name:
        # A longer chain: Gemini, then OpenRouter, then the KOVA server
        names = [BRAIN_NAMES.get(part, part) for part in name.split("+")]
        primary = getattr(provider, "primary", None)
        if primary is not None and not provider.primary_ok():
            return f"Brain: {names[1]} ({names[0]} resting)"
        if names[-1] == "KOVA server" and len(names) > 1:
            return "Brain: " + ", then ".join(names[:-1]) + ", with the KOVA server as backup"
        return "Brain: " + ", then ".join(names)
    if name == "gemini+server":
        primary = getattr(provider, "primary", None)
        if primary is not None and not provider.primary_ok():
            return "Brain: KOVA server (Gemini resting)"
        return "Brain: Gemini, with the KOVA server as backup"
    return {"gemini": "Gemini", "openrouter": "OpenRouter", "server": "KOVA server",
            "relay": "Relay, for testing"}.get(name) \
        or (name or "Local commands only")


def gemini_key_set(config):
    from brain import is_available
    return is_available(config)


def openrouter_key_set(config):
    from brain import openrouter
    return openrouter.is_available(config)


def looks_like_openrouter_key(key):
    """OpenRouter keys start with sk-or- and are one word."""
    import re
    return bool(re.fullmatch(r"sk-or-[A-Za-z0-9_\-]{20,120}", key or ""))


def looks_like_gemini_key(key):
    """Google API keys are one word of letters, digits, - and _ (usually starting AIza)."""
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{30,60}", key or ""))


def brain_status(agent):
    """What Settings may show about the brain: names and yes/no, never a key."""
    config = agent.config
    return {"footer": brain_footer(agent),
            "gemini_key": gemini_key_set(config),
            "openrouter_key": openrouter_key_set(config),
            "provider": config.get("BRAIN_PROVIDER", "gemini"),
            "server": config.get("SERVER_BRAIN", True) is not False}


def use_new_brain(agent):
    """After a new key: the next request goes to the new brain, in a fresh session."""
    import brain
    from modules import tools
    agent.provider = brain.get_provider(agent.config)
    tools.setup(agent.config, agent.provider)
    agent.session = None


# Four quick actions on Home, every one a real command the agent already knows.
QUICK_ACTIONS = (
    ("System status", "system status"),
    ("My memory", "what do you know about me"),
    ("My reminders", "what reminders do I have"),
    ("Front window", "which window is in front"),
)


def boot_event(agent, brain):
    """What the page needs once, when it loads: its own state as text. The API
    keys never travel here and nothing else of config.json does."""
    return {"t": "boot",
            "assistant": agent.name,
            "title": agent.title,
            "brain": brain,
            "wake": (wake_words_from(agent.config) or ["Max"])[0],
            "persona": (personas.current(agent.config) or {}).get("id", "max"),
            "tint": (personas.current(agent.config) or {}).get("tint", "none"),
            "quick": [{"label": label, "send": command} for label, command in QUICK_ACTIONS]}


class JsApi:
    """The page's way back into Python. pywebview runs each call on its own
    thread, so these only hand work over (or return small values) and never
    touch the agent directly."""

    def __init__(self, dashboard):
        self._dashboard = dashboard

    def send(self, text):
        """One typed request; returns False when it was not taken."""
        return self._dashboard.controller.submit(text)

    def talk(self):
        """One push-to-talk recording."""
        return self._dashboard.controller.push_to_talk()

    def respond(self, token, value):
        """The page's answer to the dialog the agent waits at. Stale tokens
        are ignored: there is one dialog at a time."""
        self._dashboard.hold_prompt_answer(token, value)

    def memory(self):
        """Every fact, as (id, text) rows, straight back to the page."""
        return [[row_id, text] for row_id, text in memory_tab.rows()]

    def history(self):
        """The local conversation, only what the Assistant screen needs."""
        return history_tab.all_messages()

    def apps(self, query=""):
        """Installed Start-menu apps with a live running flag."""
        return data_tab.applications(query)

    def files(self, folder="", query=""):
        """Safe folder listing or bounded search below one allowed folder."""
        return data_tab.files_view(folder, query)

    def system(self):
        """One real system snapshot; the page calls it only while System is open."""
        return data_tab.system_status(self._dashboard._last_reading)

    def activity(self, limit=100):
        """Recent action records, with no raw arguments exposed to the page."""
        return data_tab.activity(self._dashboard.agent, limit)

    def launch_app(self, name):
        """Queue an installed app launch through the Agent's ordinary command path."""
        from modules import system_control
        available = system_control.start_menu_apps()
        exact = next((display for display, _ in available.values()
                      if display.casefold() == str(name or "").strip().casefold()), None)
        if exact is None:
            return {"queued": False, "message": "That app is not in the Start menu list."}
        queued = self._dashboard.controller.submit(f"open {exact}", source="ui")
        return {"queued": bool(queued), "message": "Request sent to Max." if queued else "Max is closing."}

    def file_action(self, action, path):
        """Queue open/show/delete through existing Agent commands and checks."""
        from modules import files as file_tools
        action = str(action or "").lower()
        if action not in {"open", "show", "delete"}:
            return {"queued": False, "message": "Unknown file action."}
        if not isinstance(path, str) or not path:
            return {"queued": False, "message": "Choose a file or folder first."}
        target = os.path.realpath(path)
        if file_tools._hidden_path(target):
            return {"queued": False, "message": "Hidden and system files are not available here."}
        problem = file_tools.check_place(target, change=(action == "delete"))
        if problem:
            return {"queued": False, "message": problem.message}
        if not any(file_tools._inside(target, root) for root in file_tools.allowed_roots()):
            return {"queued": False, "message": "That path is outside KOVA's allowed folders."}
        if action == "show":
            command = f"open folder {os.path.dirname(target)}"
        elif action == "delete":
            command = f"delete {target}"
        elif os.path.isdir(target):
            command = f"open folder {target}"
        else:
            command = f"open file {target}"
        queued = self._dashboard.controller.submit(command, source="ui")
        return {"queued": bool(queued), "message": "Request sent to Max." if queued else "Max is closing."}

    def undo(self):
        """Ask the existing Agent to undo its latest reversible action."""
        queued = self._dashboard.controller.submit("undo", source="ui")
        return {"queued": bool(queued), "message": "Undo request sent to Max." if queued else "Max is closing."}

    def forget(self, fact_id):
        """Forget one fact the safe way, through the worker: the approval
        dialog may come up, and the fresh list follows whatever the tool does."""
        self._dashboard.controller.run(self._dashboard.make_forget_job(fact_id))

    def settings(self):
        """The four keys the Settings page may read, nothing else. The config
        holds API keys, and none of them ever travel to the page."""
        agent = self._dashboard.agent
        return {"PERSONA": (personas.current(agent.config) or {}).get("id", "max"),
                "PERSONAS": [{"id": pid, "name": personas.load(pid)["name"]} for pid in personas.all_ids()],
                "VOICE": agent.config.get("VOICE", settings_tab.DEFAULTS["VOICE"]),
                "ALERTS_ON": bool(agent.config.get("ALERTS_ON", False)),
                "PET_ON": bool(agent.config.get("PET_ON", False)),
                "WAKE_WORDS": ", ".join(wake_words_from(agent.config)),
                "PTT_HOTKEY": agent.config.get("PTT_HOTKEY", settings_tab.DEFAULTS["PTT_HOTKEY"]),
                "START_WITH_WINDOWS": startup.is_enabled(),
                "BRAIN": brain_status(agent)}

    def first_run(self):
        """True on the very first start without a Gemini key: the page then shows
        the welcome sheet once. Nothing else of the config is read out."""
        config = self._dashboard.agent.config
        return {"show": not config.get("SETUP_DONE") and not gemini_key_set(config)
                and not openrouter_key_set(config),
                "PERSONAS": [{"id": pid, "name": personas.load(pid)["name"]} for pid in personas.all_ids()]}

    def finish_setup(self, persona="", key=""):
        """The welcome sheet's answer: a persona, maybe a Gemini key. The key is
        written to config.json and never sent back to the page."""
        agent = self._dashboard.agent
        notes = []
        if persona:
            self.save({"PERSONA": persona})
        if key:
            answer = self.set_gemini_key(key)
            notes.append(answer["message"])
            if not answer["ok"]:
                return {"ok": False, "message": answer["message"]}
        settings_tab.save({"SETUP_DONE": True})
        agent.config["SETUP_DONE"] = True
        return {"ok": True, "message": " ".join(notes) or "All set."}

    def set_gemini_key(self, key):
        """Save a new Gemini key and switch the brain over now. Returns only ok and
        a sentence; the key itself never travels back."""
        return self._set_key("GEMINI_API_KEY", key, looks_like_gemini_key, "Gemini")

    def set_openrouter_key(self, key):
        """The same for OpenRouter, the brain between Gemini and the KOVA server."""
        return self._set_key("OPENROUTER_API_KEY", key, looks_like_openrouter_key, "OpenRouter")

    def _set_key(self, setting, key, check, name):
        key = str(key or "").strip()
        if not check(key):
            return {"ok": False, "message": f"That doesn't look like a {name} API key."}
        dashboard = self._dashboard
        settings_tab.save({setting: key})
        dashboard.agent.config[setting] = key
        use_new_brain(dashboard.agent)
        dashboard.refresh_boot()
        log.info("%s key saved from the dashboard", name)
        return {"ok": True, "message": "Key saved. " + brain_footer(dashboard.agent) + "."}

    def save(self, form):
        """Save through ui.settings: only changed keys are written. A hotkey
        that refused registration is reported back, the old key keeps working."""
        refused = []

        def take_hotkey(spec):
            if not self._dashboard.on_hotkey(spec):
                refused.append(spec)

        changed = settings_tab.save_and_apply(self._dashboard.agent, form, on_hotkey=take_hotkey)
        changed = dict(changed)
        notes = []
        wanted = form.get("START_WITH_WINDOWS")
        if wanted is not None and bool(wanted) != startup.is_enabled():
            ok, message = startup.enable() if wanted else startup.disable()
            notes.append(message)
            if ok:
                changed["START_WITH_WINDOWS"] = bool(wanted)
        if "PET_ON" in changed:
            self._dashboard.set_pet(changed["PET_ON"])
        if "PERSONA" in changed:
            self._dashboard.refresh_boot()   # the new name and tint reach the page now
        return {"changed": list(changed), "refused": refused, "notes": notes}


class WebDashboard:
    """The PyWebView window, shaped so ui/run.py can drive it like the classic
    one. Heavy webview work stays in the window; the pieces the tests exercise
    are the event payloads and the bridge flow."""

    def __init__(self, controller, agent, speak=None, on_hide=None, on_quit=None,
                 on_hotkey=None, create_window=None, stats_factory=None, pill_window=None):
        self.controller = controller
        self.agent = agent
        self.speak = speak
        self.on_hide = on_hide or self.withdraw
        self.on_quit = on_quit or self.destroy
        self.on_hotkey = on_hotkey or (lambda spec: False)   # re-register PTT_HOTKEY
        self.on_exit = None                   # called with the Result when Max should stop
        self.api = JsApi(self)
        self._alive = True
        self._visible = False
        self._ready = False                   # the page can receive events
        self._buffer = []                     # events waiting for the page to load
        self._lock = threading.Lock()         # buffer and ready
        self._shown_state = None
        self._status_override = None
        self._error_until = 0.0
        self._last_reading = None
        self._pet_started = False
        self._pending_user = None
        self._bound = {}                      # <Map> / <Unmap> hooks for run_ui
        self._prompt_token = 0                # one dialog at a time, known by token
        self._open_token = None
        self._prompt_answer = None
        self._prompt_answered = None

        # The agent's callbacks land here, exactly like on the classic window
        controller.on_user = self.show_user
        controller.on_result = self.show_result
        controller.on_trace = self.show_trace

        url = "file:///" + WEB_DIR.replace("\\", "/") + "/index.html"
        create_window = create_window or self._create_webview_window
        self._window = create_window(
            title=f"KOVA — {agent.name}", url=url, js_api=self.api,
            width=1180, height=760, min_size=(900, 600), resizable=True,
            background_color="#0b0d12", text_select=True)
        self._bind_window_events()
        # The floating companion, made only the first time the dashboard goes to the tray
        self.pill = Pill(on_open=lambda: self.show(), on_talk=lambda: self.controller.push_to_talk(),
                         create_window=pill_window)
        self.pet = Pet(on_open=lambda: self.show(), on_talk=lambda: self.controller.push_to_talk(),
                       create_window=pill_window)

        if stats_factory is None:
            stats_factory = lambda push: stats_tab.StatsCollector(push=push)
        self._disk_reader = disk_percent
        self._stats = stats_factory(self._on_stats)
        self._pump_stop = threading.Event()
        self._pump_thread = threading.Thread(target=self._pump_loop, name="kova-ui-pump", daemon=True)
        self._pump_thread.start()

    # ---------- window ----------

    def _create_webview_window(self, **kwargs):
        import webview
        kwargs["confirm_close"] = False   # nothing closes without a look at the dashboard first
        return webview.create_window(**kwargs)

    def _bind_window_events(self):
        events = getattr(self._window, "events", None)
        if events is None:                # tests may use a bare fake window
            return
        events.loaded += self._page_loaded
        events.shown += self._window_shown
        events.minimized += self._window_minimized
        events.restored += self._window_restored

    # ---------- one pump thread ----------

    def _pump_loop(self):
        """The drain the classic window did with a Tk timer: run posted
        callbacks on the page and keep the status in step. While the window is
        hidden only posted work runs; the cheap state check waits for it."""
        while self._alive and not self._pump_stop.is_set():
            self._tick()

    def _tick(self):
        """One pump step, exact enough to steer from tests. Buffered events go
        out first, so their order keeps how they happened."""
        self._flush_pending()
        ran = self.controller.bridge.next_call(timeout=0.1)
        if ran:
            return   # what that work pushed goes out on the next tick
        if self._visible or self.pet.window is not None or self.pill.window is not None:
            # The desktop Reaper and the pill follow Max while the dashboard is hidden too
            self._mirror_state()

    def _flush_pending(self):
        """Everything raised before the page loaded goes out now. True when
        something was sent."""
        with self._lock:
            if not self._ready:
                return False
            pending = self._buffer
            if not pending:
                return False
            self._buffer = []
        for event in pending:
            self._eval(js_receive(event))
        return True

    def _push(self, event):
        """Send one event to the page, or buffer it while the page is still
        loading. Runs on the pump thread."""
        with self._lock:
            if not self._ready:
                if len(self._buffer) >= BUFFER_KEEP:
                    self._buffer.pop(0)
                self._buffer.append(event)
                return
        self._eval(js_receive(event))

    def _mirror_state(self):
        if self._status_override == "error" and time.monotonic() > self._error_until:
            # A failed request is news for a few seconds, not a state Max stays in
            self._status_override = None
        state = self._status_override or self.controller.state
        if voice_handler.is_speaking() and state in ("idle", "thinking", "awake", "asleep", "working"):
            state = "speaking"   # the real flag from the voice layer, never guessed
        if state != self._shown_state:
            self._shown_state = state
            self._push(state_event(state))
            self.pill.push(state_event(state))
            self.pet.push(state_event(state))

    # ---------- events from the window ----------

    def _page_loaded(self):
        """The page can receive events. The boot event goes in first, ahead of
        anything that was buffered while it loaded."""
        with self._lock:
            self._buffer.insert(0, self._my_boot_event())
        self._ready = True    # the pump flushes the buffer on its next tick
        if self.agent.config.get("PET_ON") and not self._pet_started:
            self._pet_started = True
            self.set_pet(True)

    def set_pet(self, on):
        """The Reaper on the desktop, on or off, right now."""
        if on:
            self.pet.show()
            self.pet.push(self._my_boot_event())
        else:
            self.pet.hide()

    def refresh_boot(self):
        self._push(self._my_boot_event())
        self.pill.push(self._my_boot_event())
        self.pet.push(self._my_boot_event())
        self._shown_state = None

    def _my_boot_event(self):
        return boot_event(self.agent, brain_footer(self.agent))

    def _window_shown(self):
        self._set_visible(True)

    def _window_minimized(self):
        self._set_visible(False)

    def _window_restored(self):
        self._set_visible(True)

    # ---------- visibility ----------

    def bind(self, event, callback, add=None):
        """run_ui's <Map> / <Unmap> hooks, kept under the same names."""
        self._bound.setdefault(event, []).append(callback)

    def _set_visible(self, visible):
        if not self._alive:
            return
        changed = self._visible != visible
        self._visible = visible
        if visible:
            if changed:
                self._stats.start()
            for callback in self._bound.get("<Map>", []):
                callback(None)
        else:
            if changed:
                self._stats.stop()
            for callback in self._bound.get("<Unmap>", []):
                callback(None)

    # ---------- the dashboard run_ui drives ----------

    def show(self):
        self._window.show()
        self._set_visible(True)
        self.pill.hide()

    def withdraw(self):
        """To the tray: the window goes, Max keeps working."""
        self._window.hide()
        self._set_visible(False)
        self.pill.show()
        self._shown_state = None   # re-showing puts the status up again

    def show_notice(self, message):
        """An alert or reminder, shown without interrupting speech."""
        self._push({"t": "notice", "text": message})

    def show_user(self, text):
        self._status_override = None
        self._shown_state = None
        # Saved with its answer: a misheard voice fragment that Max didn't catch is
        # shown now but never kept (live check 9 Nov: "camera", "IMAX" filled the history)
        self._pending_user = text
        self._push({"t": "chat", "who": "user", "text": text, "id": None})

    def show_result(self, result, source):
        lines = messages_tab.answer_lines(result)
        pending, self._pending_user = self._pending_user, None
        noise = source in ("mic", "voice") and not result.success and \
            (result.message or "").startswith("Sorry, I didn't catch that")
        if pending and not noise:
            history_tab.append("user", pending)
        if lines:
            text = "\n".join(lines)
            saved = None if noise else history_tab.append("max", text)
            self._push({"t": "chat", "who": "max", "text": text,
                        "note": None if result.success else "couldn't do that",
                        "id": saved.get("id") if saved else None})
            if source == "ui":
                self._push({"t": "screen-result", "text": text,
                            "success": bool(result.success)})
            self.pet.push({"t": "done", "success": bool(result.success)})
        self._status_override = None if result.success else "error"
        self._error_until = time.monotonic() + ERROR_SHOWN_SECONDS
        self._shown_state = None
        if self._status_override:
            self._mirror_state()
        if (result.stop or result.restart) and self.on_exit:
            self.on_exit(result)

    def show_trace(self, line):
        stripped = (line or "").lstrip()
        if stripped.startswith("→"):
            self._status_override = "working"
            self._shown_state = None
        elif stripped.startswith("←"):
            self._status_override = "thinking"
            self._shown_state = None
        self._push({"t": "trace", "line": line})

    def show_memory(self):
        """The fresh fact list, after a forget job settles."""
        self._push({"t": "memory", "rows": memory_tab.rows()})

    def make_forget_job(self, fact_id):
        """The worker's job behind api.forget: delete the safe way, then the
        page gets the fresh list whatever happened."""
        def job():
            result = None
            try:
                result = memory_tab.delete(self.agent, fact_id)
            finally:
                self.controller.bridge.post(self.show_memory)
            return result
        return job

    def _on_stats(self, reading):
        """One reading, on the sampler thread: the main-drive read belongs
        here, never on the pump. The packed event goes through the bridge."""
        self._last_reading = dict(reading)
        event = {"t": "stats", "rings": ring_percentages(reading, self._disk_reader())}
        self.controller.bridge.post(self.show_stats, event)

    def show_stats(self, event):
        self._push(event)

    def _toggle_mute(self):
        self.controller.muted = not self.controller.muted
        self._shown_state = None   # the status bar shows Voice off at once

    def prompt(self, kind, question):
        """The dialog the agent waits at, posted here by the controller's
        round-trip: shown with one event, then this thread waits for the page
        to hold its answer through api.respond(token, value). Closing the
        dialog is always No (confirm) or empty (ask); a window that goes away
        answers the same, so the worker can never hang on a dead page."""
        if not self._alive or not self._ready:
            return DEFAULT_ANSWERS.get(kind)
        self._status_override = "approval"
        self._shown_state = None
        self._mirror_state()
        with self._lock:
            self._prompt_token += 1
            token = self._prompt_token
            self._open_token = token
            self._prompt_answer = None
            answered = self._prompt_answered = threading.Event()
        self._push({"t": "prompt", "token": token, "kind": kind, "question": question})
        while not answered.wait(0.1):
            if not self._alive or self.controller.bridge.closed:
                self._drop_prompt()
                self._status_override = None
                self._shown_state = None
                return DEFAULT_ANSWERS.get(kind)
        with self._lock:
            answer = self._prompt_answer
            self._drop_prompt_locked()
        self._status_override = "thinking"
        self._shown_state = None
        return answer

    def _drop_prompt_locked(self):
        self._open_token = None
        self._prompt_answer = None
        self._prompt_answered = None

    def _drop_prompt(self):
        with self._lock:
            self._drop_prompt_locked()

    def hold_prompt_answer(self, token, value):
        """api.respond: the page has an answer for its open dialog."""
        event = None
        with self._lock:
            if self._open_token == token:
                self._prompt_answer = value
                event = self._prompt_answered
        if event is not None:
            event.set()

    def mainloop(self):
        """Block until the window closes: pywebview's event loop.

        http_server=True serves the page folder over a loopback HTTP server,
        because Chromium refuses webfonts (CORS-requested) from the opaque
        file:// origin; the fonts are still stored in the project.
        """
        import webview
        webview.start(http_server=True)

    def destroy(self):
        """Max is quitting for good: closes the page and lets the pump go."""
        self._alive = False
        self._pump_stop.set()
        stats = getattr(self, "_stats", None)
        if stats is not None:
            stats.stop()
        self._ready = False   # no more sends into a dead window
        self.pill.destroy()
        self.pet.destroy()
        try:
            self._window.destroy()
        except Exception:
            pass

    # ---------- plumbing ----------

    def _eval(self, script):
        try:
            self._window.evaluate_js(script)
        except Exception as e:
            log.debug("evaluate_js skipped: %s", e)   # a closing window cannot receive


def launch(agent, is_yes=None):
    """What `python main.py --ui` runs. Without WebView2 there is no window: say so
    and stop, so text and voice mode (`python main.py`) are the way in."""
    from modules.result import Result
    from ui.run import run_ui
    if not webview_available():
        log.warning("WebView2 not available: the dashboard can't open")
        print("The dashboard needs Microsoft Edge WebView2. Run `python main.py` for voice mode.")
        return Result(False, "The dashboard needs WebView2.", stop=True)
    return run_ui(agent, is_yes=is_yes, dashboard_factory=WebDashboard)
