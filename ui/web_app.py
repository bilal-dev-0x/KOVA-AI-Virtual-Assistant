import json
import os
import threading

from desktop_agent import wake_words_from
from modules.logger import get_logger
from ui import memory as memory_tab
from ui import messages as messages_tab
from ui import settings as settings_tab
from ui import stats as stats_tab

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
BUFFER_KEEP = 500     # events held until the page has loaded, oldest first

# Max's states, the ones the classic window showed, now as a short key the page
# turns into a colour.
STATES = {"idle": ("Ready", "idle"),
          "asleep": ("Asleep — say the wake word", "asleep"),
          "awake": ("Listening", "listening"),
          "listening": ("Listening", "listening"),
          "working": ("Thinking...", "working"),
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


def brain_footer(agent):
    """The real brain answering right now, said plainly for the footer."""
    provider = getattr(agent, "provider", None)
    if provider is None:
        return "Local commands only"
    name = getattr(provider, "name", "none")
    if name == "gemini+server":
        primary = getattr(provider, "primary", None)
        if primary is not None and not provider.primary_ok():
            return "Brain: KOVA server (Gemini resting)"
        return "Brain: Gemini, with the KOVA server as backup"
    return {"gemini": "Gemini", "server": "KOVA server", "relay": "Relay, for testing"}.get(name) \
        or (name or "Local commands only")


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

    def forget(self, fact_id):
        """Forget one fact the safe way, through the worker: the approval
        dialog may come up, and the fresh list follows whatever the tool does."""
        self._dashboard.controller.run(self._dashboard.make_forget_job(fact_id))

    def settings(self):
        """The four keys the Settings page may read, nothing else. The config
        holds API keys, and none of them ever travel to the page."""
        agent = self._dashboard.agent
        return {"VOICE": agent.config.get("VOICE", settings_tab.DEFAULTS["VOICE"]),
                "ALERTS_ON": bool(agent.config.get("ALERTS_ON", False)),
                "WAKE_WORDS": ", ".join(wake_words_from(agent.config)),
                "PTT_HOTKEY": agent.config.get("PTT_HOTKEY", settings_tab.DEFAULTS["PTT_HOTKEY"])}

    def save(self, form):
        """Save through ui.settings: only changed keys are written. A hotkey
        that refused registration is reported back, the old key keeps working."""
        refused = []

        def take_hotkey(spec):
            if not self._dashboard.on_hotkey(spec):
                refused.append(spec)

        changed = settings_tab.save_and_apply(self._dashboard.agent, form, on_hotkey=take_hotkey)
        return {"changed": list(changed), "refused": refused}


class WebDashboard:
    """The PyWebView window, shaped so ui/run.py can drive it like the classic
    one. Heavy webview work stays in the window; the pieces the tests exercise
    are the event payloads and the bridge flow."""

    def __init__(self, controller, agent, speak=None, on_hide=None, on_quit=None,
                 on_hotkey=None, create_window=None, stats_factory=None):
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
        if self._visible:
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
        state = self.controller.state
        if state != self._shown_state:
            self._shown_state = state
            self._push(state_event(state))

    # ---------- events from the window ----------

    def _page_loaded(self):
        """The page can receive events. The boot event goes in first, ahead of
        anything that was buffered while it loaded."""
        with self._lock:
            self._buffer.insert(0, self._my_boot_event())
        self._ready = True    # the pump flushes the buffer on its next tick

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

    def withdraw(self):
        """To the tray: the window goes, Max keeps working."""
        self._window.hide()
        self._set_visible(False)
        self._shown_state = None   # re-showing puts the status up again

    def show_notice(self, message):
        """An alert or reminder, shown without interrupting speech."""
        self._push({"t": "notice", "text": message})

    def show_user(self, text):
        self._push({"t": "chat", "who": "user", "text": text})

    def show_result(self, result, source):
        lines = messages_tab.answer_lines(result)
        if lines:
            self._push({"t": "chat", "who": "max", "text": "\n".join(lines),
                        "note": None if result.success else "couldn't do that"})
        if (result.stop or result.restart) and self.on_exit:
            self.on_exit(result)

    def show_trace(self, line):
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
                return DEFAULT_ANSWERS.get(kind)
        with self._lock:
            answer = self._prompt_answer
            self._drop_prompt_locked()
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
