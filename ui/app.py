import customtkinter as ctk

from desktop_agent import wake_words_from
from ui import memory as memory_tab
from ui import settings as settings_tab
from ui import stats as stats_tab
from ui.hotkey import DEFAULT_HOTKEY

# The Max dashboard window, in CustomTkinter. Widgets only: what each button
# does lives in ui.controller, ui.settings, ui.memory and ui.stats, which the
# tests exercise without ever opening a window. This module is imported only
# when Max is started with --ui, so text and voice mode never load Tk.
#
# Everything arriving from the agent's worker thread comes through the Bridge
# and is drained by a short timer (_pump): Tkinter is only ever touched here,
# on the thread that created the window.

PUMP_MS = 50          # how often the Tk thread drains what the worker posted
CHAT_KEEP = 200       # chat bubbles kept before the oldest are dropped
TRACE_KEEP = 500      # trace lines kept

# One dark palette: the window is meant to sit beside games and code editors
BG = "#0f1115"
PANEL = "#171a21"
CARD = "#1e222b"
LINE = "#2a2f3a"
TEXT = "#e6e8ee"
MUTED = "#8a91a0"
ACCENT = "#4f9cff"
USER_BUBBLE = "#2b5cd6"
MAX_BUBBLE = "#232834"
DANGER = "#e5484d"

STATES = {"idle": ("Ready", MUTED),
          "asleep": ("Asleep — say the wake word", "#7c6cf2"),
          "awake": ("Listening", "#3ddc84"),
          "listening": ("Listening", "#3ddc84"),
          "working": ("Thinking...", ACCENT),
          "muted": ("Voice off", "#ff9f43")}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def answer_lines(result):
    """What the chat shows for one answer: the message, then any detail."""
    lines = [result.message] if result.message else []
    lines += [line for line in (result.detail or "").splitlines() if line.strip()]
    return lines


def percent(text):
    """'71%' or '100% charging' -> 0.71 / 1.0 for a progress bar; None when unknown."""
    try:
        return max(0.0, min(1.0, float(str(text).split("%")[0]) / 100))
    except (TypeError, ValueError):
        return None


class Dashboard(ctk.CTk):
    def __init__(self, controller, agent, speak=None, on_hide=None, on_quit=None, on_hotkey=None):
        super().__init__(fg_color=BG)
        self.controller = controller
        self.agent = agent
        self.speak = speak
        self.on_hide = on_hide or self.hide   # run_ui decides: hide to tray, or quit with no tray
        self.on_quit = on_quit or self.destroy
        self.on_hotkey = on_hotkey or (lambda spec: False)   # re-register PTT_HOTKEY
        self.on_exit = None                   # called with the Result when Max should stop
        self._alive = True
        self._shown_state = None
        self._bubbles = []

        self.title(f"KOVA — {agent.name}")
        self.geometry("1040x680")
        self.minsize(820, 520)
        self.protocol("WM_DELETE_WINDOW", self.on_hide)
        self.bind("<Map>", self._visibility)
        self.bind("<Unmap>", self._visibility)

        # The agent's callbacks land here
        controller.on_user = self.show_user
        controller.on_result = self.show_result
        controller.on_trace = self.show_trace
        controller.prompt = self.prompt

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_pages()
        self.stats = stats_tab.StatsCollector(
            push=lambda reading: controller.bridge.post(self._show_stats, reading))
        self.after(PUMP_MS, self._pump)

    # ---------- sidebar ----------

    def _build_sidebar(self):
        side = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=PANEL)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)

        ctk.CTkLabel(side, text="KOVA", font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=22, pady=(22, 0))
        ctk.CTkLabel(side, text=f"{self.agent.name} · your desktop assistant",
                     text_color=MUTED).pack(anchor="w", padx=22)

        # The orb: one glance says whether Max is asleep, listening or thinking
        status = ctk.CTkFrame(side, fg_color=CARD, corner_radius=14)
        status.pack(fill="x", padx=16, pady=(20, 14))
        self.orb = ctk.CTkCanvas(status, width=46, height=46, bg=CARD, highlightthickness=0)
        self.orb.pack(side="left", padx=(12, 8), pady=12)
        self._orb_ring = self.orb.create_oval(3, 3, 43, 43, outline=MUTED, width=2)
        self._orb_dot = self.orb.create_oval(13, 13, 33, 33, fill=MUTED, outline="")
        self.state_label = ctk.CTkLabel(status, text="Ready", text_color=TEXT, anchor="w",
                                        justify="left", wraplength=130,
                                        font=ctk.CTkFont(size=14, weight="bold"))
        self.state_label.pack(side="left", fill="x", expand=True, pady=12)

        self.nav = {}
        for name in ("Chat", "Activity", "Memory", "Settings"):
            button = ctk.CTkButton(side, text=name, anchor="w", height=38, corner_radius=10,
                                   fg_color="transparent", hover_color=CARD, text_color=TEXT,
                                   font=ctk.CTkFont(size=14), command=lambda n=name: self.show_page(n))
            button.pack(fill="x", padx=12, pady=2)
            self.nav[name] = button

        self.mic_button = ctk.CTkButton(side, text="🎙  Voice on", height=40, corner_radius=12,
                                        command=self._toggle_mute)
        self.mic_button.pack(side="bottom", fill="x", padx=16, pady=(8, 18))
        self.mute_button = self.mic_button   # one button: voice on/off

        meters = ctk.CTkFrame(side, fg_color=CARD, corner_radius=14)
        meters.pack(side="bottom", fill="x", padx=16, pady=8)
        self.meters = {}
        for key, label in (("cpu", "CPU"), ("ram_percent", "RAM"), ("gpu", "GPU"), ("battery", "Battery")):
            row = ctk.CTkFrame(meters, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(8, 0))
            ctk.CTkLabel(row, text=label, text_color=MUTED, width=60, anchor="w").pack(side="left")
            value = ctk.CTkLabel(row, text="–", text_color=TEXT, anchor="e")
            value.pack(side="right")
            bar = ctk.CTkProgressBar(meters, height=6, progress_color=ACCENT, fg_color=LINE)
            bar.set(0)
            bar.pack(fill="x", padx=12, pady=(2, 4))
            self.meters[key] = (value, bar)
        ctk.CTkFrame(meters, height=6, fg_color="transparent").pack()
        self.stats_label = ctk.CTkLabel(meters, text="", text_color=MUTED)  # the plain line, for the tray tooltip

    # ---------- pages ----------

    def _build_pages(self):
        self.pages = {}
        host = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        host.grid(row=0, column=1, sticky="nsew")
        host.grid_columnconfigure(0, weight=1)
        host.grid_rowconfigure(0, weight=1)
        for name, build in (("Chat", self._build_chat), ("Activity", self._build_trace),
                            ("Memory", self._build_memory), ("Settings", self._build_settings)):
            page = ctk.CTkFrame(host, fg_color=BG, corner_radius=0)
            page.grid(row=0, column=0, sticky="nsew")
            build(page)
            self.pages[name] = page
        self.show_page("Chat")

    def show_page(self, name):
        self.pages[name].tkraise()
        for other, button in self.nav.items():
            button.configure(fg_color=CARD if other == name else "transparent")
        if name == "Chat":
            self.entry.focus_set()

    def _heading(self, page, title, note=""):
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=26, pady=(22, 10))
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=TEXT).pack(side="left")
        if note:
            ctk.CTkLabel(head, text=note, text_color=MUTED).pack(side="right")
        return head

    # ---------- chat ----------

    def _build_chat(self, page):
        words = wake_words_from(self.agent.config)
        hint = f"Say “{words[0].title()}” to talk, or type below" if words else "Type below"
        self._heading(page, "Chat", hint)

        self.chat = ctk.CTkScrollableFrame(page, fg_color=PANEL, corner_radius=16)
        self.chat.pack(fill="both", expand=True, padx=22, pady=(0, 12))

        form = ctk.CTkFrame(page, fg_color=PANEL, corner_radius=16)
        form.pack(fill="x", padx=22, pady=(0, 20))
        self.entry = ctk.CTkEntry(form, placeholder_text=f"Message {self.agent.name}...", height=42,
                                  border_width=0, fg_color=CARD, corner_radius=12,
                                  font=ctk.CTkFont(size=14))
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 8), pady=10)
        self.entry.bind("<Return>", self._send)
        ctk.CTkButton(form, text="Send", width=86, height=42, corner_radius=12,
                      command=self._send).pack(side="left", padx=(0, 10), pady=10)

    def _send(self, _event=None):
        text = self.entry.get()
        self.entry.delete(0, "end")
        self.controller.submit(text)

    def _bubble(self, text, mine, note=None):
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        bubble = ctk.CTkFrame(row, fg_color=USER_BUBBLE if mine else MAX_BUBBLE, corner_radius=14)
        bubble.pack(side="right" if mine else "left", padx=(80, 0) if mine else (0, 80))
        if note:
            ctk.CTkLabel(bubble, text=note, text_color="#b8c4ff" if mine else MUTED,
                         font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14, pady=(8, 0))
        ctk.CTkLabel(bubble, text=text, text_color=TEXT, wraplength=520, justify="left",
                     font=ctk.CTkFont(size=14)).pack(anchor="w", padx=14, pady=(2 if note else 8, 8))
        self._bubbles.append(row)
        while len(self._bubbles) > CHAT_KEEP:
            self._bubbles.pop(0).destroy()
        self.after(10, lambda: self.chat._parent_canvas.yview_moveto(1.0))

    def show_user(self, text):
        self._bubble(text, mine=True)

    def show_result(self, result, source):
        lines = answer_lines(result)
        if lines:
            self._bubble("\n".join(lines), mine=False,
                         note=None if result.success else "couldn't do that")
        if (result.stop or result.restart) and self.on_exit:
            self.on_exit(result)

    def show_notice(self, message):
        """Show an alert or reminder without interrupting speech or opening a dialog."""
        self._bubble(message, mine=False, note="heads up")

    # ---------- activity (the live trace) ----------

    def _build_trace(self, page):
        self._heading(page, "Activity", "every tool Max runs, live")
        self.trace_box = ctk.CTkTextbox(page, state="disabled", wrap="none", fg_color=PANEL,
                                        corner_radius=16, text_color=TEXT,
                                        font=ctk.CTkFont(family="Consolas", size=13))
        self.trace_box.pack(fill="both", expand=True, padx=22, pady=(0, 20))

    def show_trace(self, line):
        self._append(self.trace_box, line, TRACE_KEEP)

    # ---------- memory ----------

    def _build_memory(self, page):
        head = self._heading(page, "Memory")
        ctk.CTkButton(head, text="Refresh", width=86, fg_color=CARD, hover_color=LINE,
                      command=self.refresh_memory).pack(side="right")
        self.memory_list = ctk.CTkScrollableFrame(page, fg_color=PANEL, corner_radius=16)
        self.memory_list.pack(fill="both", expand=True, padx=22, pady=(0, 20))
        self.refresh_memory()

    def refresh_memory(self):
        """Rebuild the list. Called from the Tk thread only (the delete job posts it)."""
        for child in self.memory_list.winfo_children():
            child.destroy()
        rows = memory_tab.rows()
        if not rows:
            ctk.CTkLabel(self.memory_list, text=f"{self.agent.name} doesn't remember anything yet.",
                         text_color=MUTED).pack(anchor="w", padx=12, pady=12)
            return
        for fact_id, text in rows:
            row = ctk.CTkFrame(self.memory_list, fg_color=CARD, corner_radius=12)
            row.pack(fill="x", padx=6, pady=4)
            ctk.CTkLabel(row, text=f"#{fact_id}", text_color=MUTED, width=40).pack(side="left", padx=(12, 0))
            ctk.CTkLabel(row, text=text, text_color=TEXT, anchor="w", wraplength=560,
                         justify="left").pack(side="left", fill="x", expand=True, padx=8, pady=10)
            ctk.CTkButton(row, text="Forget", width=76, fg_color="transparent", border_width=1,
                          border_color=DANGER, text_color=DANGER, hover_color=LINE,
                          command=lambda i=fact_id: self._forget(i)).pack(side="right", padx=10)

    def _forget(self, fact_id):
        def job():
            result = memory_tab.delete(self.agent, fact_id)
            self.controller.bridge.post(self.refresh_memory)  # the list follows the tool, not the click
            return result
        self.controller.run(job)

    # ---------- settings ----------

    def _build_settings(self, page):
        self._heading(page, "Settings")
        config = self.agent.config
        form = ctk.CTkFrame(page, fg_color=PANEL, corner_radius=16)
        form.pack(fill="x", padx=22, pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)

        def label(row, text, note):
            box = ctk.CTkFrame(form, fg_color="transparent")
            box.grid(row=row, column=0, sticky="w", padx=18, pady=12)
            ctk.CTkLabel(box, text=text, text_color=TEXT, font=ctk.CTkFont(size=14)).pack(anchor="w")
            ctk.CTkLabel(box, text=note, text_color=MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w")

        label(0, "Voice", "how Max sounds")
        self.voice = ctk.CTkSegmentedButton(form, values=list(settings_tab.VOICES))
        self.voice.set(config.get("VOICE", settings_tab.DEFAULTS["VOICE"]))
        self.voice.grid(row=0, column=1, sticky="w", padx=18)

        label(1, "Proactive alerts", "battery, RAM and disk warnings")
        self.alerts = ctk.CTkSwitch(form, text="")
        if config.get("ALERTS_ON", False):
            self.alerts.select()
        self.alerts.grid(row=1, column=1, sticky="w", padx=18)

        label(2, "Wake words", "comma separated")
        self.wake_words = ctk.CTkEntry(form, width=360, fg_color=CARD, border_width=0)
        self.wake_words.insert(0, ", ".join(wake_words_from(config)))
        self.wake_words.grid(row=2, column=1, sticky="w", padx=18)

        label(3, "Listen-now key", "wakes Max without the wake word")
        self.hotkey_entry = ctk.CTkEntry(form, width=360, fg_color=CARD, border_width=0)
        self.hotkey_entry.insert(0, config.get("PTT_HOTKEY", DEFAULT_HOTKEY))
        self.hotkey_entry.grid(row=3, column=1, sticky="w", padx=18)

        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.pack(fill="x", padx=22)
        ctk.CTkButton(actions, text="Save", width=100, height=38, corner_radius=12,
                      command=self._save_settings).pack(side="left")
        self.settings_status = ctk.CTkLabel(actions, text="", text_color=MUTED)
        self.settings_status.pack(side="left", padx=14)

    def _save_settings(self):
        form = {"VOICE": self.voice.get(),
                "ALERTS_ON": bool(self.alerts.get()),
                "WAKE_WORDS": self.wake_words.get(),
                "PTT_HOTKEY": self.hotkey_entry.get()}
        refused = []

        def take_hotkey(spec):
            if not self.on_hotkey(spec):
                refused.append(spec)

        changed = settings_tab.save_and_apply(self.agent, form, on_hotkey=take_hotkey)
        if not changed:
            message = "Nothing changed."
        else:
            message = "Saved: " + ", ".join(changed) + "."
            if refused:
                message += f" {refused[0]} was not accepted, the old key still works."
        self.settings_status.configure(text=message)

    # ---------- system ----------

    def _show_stats(self, reading):
        self.stats_label.configure(text=stats_tab.bar(reading))
        for key, (value, bar) in self.meters.items():
            text = reading.get(key)
            value.configure(text=text or "–")
            level = percent(text)
            bar.set(level or 0)
            if key != "battery" and level is not None:
                bar.configure(progress_color=DANGER if level > 0.85 else ACCENT)

    def _toggle_mute(self):
        self.controller.muted = not self.controller.muted
        muted = self.controller.muted
        self.mic_button.configure(text="🔇  Voice off" if muted else "🎙  Voice on",
                                  fg_color=CARD if muted else ACCENT)

    def _visibility(self, _event=None):
        """Stats run only while the window is on screen: hidden or minimised
        means the sampler stops, so an idle dashboard costs nothing."""
        if not self._alive:
            return
        if self.state() in ("normal", "zoomed"):
            self.stats.start()
        else:
            self.stats.stop()

    def _pump(self):
        """One Tk tick: hand the worker's posts to the widgets, mirror its state."""
        if not self._alive:
            return
        self.controller.bridge.drain()
        state = self.controller.state
        if state != self._shown_state:
            self._shown_state = state
            text, colour = STATES.get(state, (state, MUTED))
            self.state_label.configure(text=text)
            self.orb.itemconfigure(self._orb_dot, fill=colour)
            self.orb.itemconfigure(self._orb_ring, outline=colour)
        self.after(PUMP_MS, self._pump)

    # ---------- windows ----------

    def hide(self):
        """To the tray: the window goes, Max keeps working."""
        self.withdraw()

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def prompt(self, kind, question):
        """The pop-up the agent waits at, asked from the worker through the
        Bridge. Closing the window is always No (confirm) or empty (ask)."""
        if self.state() != "normal":
            self.show()  # Max is in the tray: the answer still has to be seen
        dialog = ctk.CTkToplevel(self, fg_color=PANEL)
        dialog.title(f"{self.agent.name} asks" if kind == "ask" else f"{self.agent.name} needs your yes")
        dialog.resizable(False, False)
        answer = []

        def finish(value):
            answer.append(value)
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False if kind == "confirm" else None))
        dialog.transient(self)
        dialog.geometry(f"+{self.winfo_x() + 300}+{self.winfo_y() + 220}")

        ctk.CTkLabel(dialog, text=question, wraplength=420, justify="left", text_color=TEXT,
                     font=ctk.CTkFont(size=15)).pack(padx=26, pady=(24, 18), anchor="w")
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=26, pady=(0, 22))

        entry = None
        if kind == "confirm":
            ctk.CTkButton(buttons, text="No", width=96, height=36, fg_color=CARD, hover_color=LINE,
                          command=lambda: finish(False)).pack(side="right", padx=(8, 0))
            ctk.CTkButton(buttons, text="Yes, do it", width=110, height=36,
                          command=lambda: finish(True)).pack(side="right")
            dialog.bind("<Escape>", lambda event: finish(False))
        else:
            entry = ctk.CTkEntry(buttons, width=300, fg_color=CARD, border_width=0)
            entry.pack(side="left", padx=(0, 8))
            entry.bind("<Return>", lambda event: finish(entry.get().strip() or None))
            ctk.CTkButton(buttons, text="OK", width=70,
                          command=lambda: finish(entry.get().strip() or None)).pack(side="left", padx=4)
            ctk.CTkButton(buttons, text="Cancel", width=80, fg_color=CARD, hover_color=LINE,
                          command=lambda: finish(None)).pack(side="left", padx=4)

        dialog.grab_set()
        dialog.focus_force()
        if entry is not None:
            entry.focus_force()
        self.wait_window(dialog)
        if not answer:
            return False if kind == "confirm" else None
        return answer[0]

    # ---------- plumbing ----------

    @staticmethod
    def _append(box, text, keep):
        box.configure(state="normal")
        box.insert("end", text + "\n")
        lines = box.get("1.0", "end-1c").splitlines()
        if len(lines) > keep:
            box.delete("1.0", f"{len(lines) - keep + 1}.0")
        box.configure(state="disabled")
        box.see("end")

    def destroy(self):
        """Real exit: stop the sampler first so no timer touches a dead window."""
        self._alive = False
        stats = getattr(self, "stats", None)
        if stats is not None:
            stats.stop()
        super().destroy()
