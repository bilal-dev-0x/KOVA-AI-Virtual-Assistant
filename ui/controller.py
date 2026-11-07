import threading

from modules import reminders
from modules.result import Result
from modules.logger import get_logger

# One Agent, one worker thread. Typing, the mic button and the push-to-talk hotkey
# all hand their request to this controller, so the window never blocks and only
# one request is ever in flight (Max must not answer two things at once).
#
# The agent's three callbacks cross to the UI through the Bridge:
#   confirm / ask  block the worker until the dialog on the UI thread answers
#   trace          never blocks: it is posted and forgotten
# What the status bar shows lives in .state and is read on every UI tick, so
# there is no status event to race with a request that finishes quickly.

log = get_logger()

SPOKEN = {"mic", "voice"}  # requests that came by voice get spoken answers and spoken questions


class Controller:
    def __init__(self, agent, bridge, on_user=None, on_result=None, on_trace=None,
                 listen=None, prompt=None):
        self.agent = agent
        self.bridge = bridge
        self.on_user = on_user          # on_user(text) shows what was said or typed
        self.on_result = on_result      # on_result(result, source) shows Max's answer
        self.on_trace = on_trace        # on_trace(line) appends to the trace panel
        self.listen = listen            # listen() -> text | None, one recording, on the worker
        self.prompt = prompt            # prompt(kind, question) -> bool | str | None, on the UI thread
        self.speak = None               # speak(line): answers to spoken requests are said out loud
        self.voice_prompt = None        # voice_prompt(kind, question): approvals for spoken requests
        self.on_sleep = None            # on_sleep(): "go to sleep" was answered
        self.voice_state = "off"        # off | asleep | awake, set by the voice loop for the status
        self.muted = False
        self._source = None             # where the request in hand came from
        self._jobs = []
        self._state = "idle"            # idle | working | listening
        self._lock = threading.Lock()
        self._worker = None
        self._closing = False

    @property
    def state(self):
        """For the status bar: muted wins over idle, a request in hand wins over everything."""
        with self._lock:
            if self._state == "idle" and self.muted:
                return "muted"
            if self._state == "idle" and self.voice_state in ("asleep", "awake"):
                return self.voice_state
            return self._state

    @property
    def busy(self):
        with self._lock:
            return bool(self._jobs) or self._worker is not None

    # ---------- wiring ----------

    def attach(self):
        """The one agent asks the UI for approvals and shows its trace here."""
        self.agent.confirm = self.confirm
        self.agent.ask = self.ask
        self.agent.trace = self.trace

    # ---------- work ----------

    def submit(self, text, source="text", done=None):
        """Queue one request. False when empty or the window is closing.
        done (a threading.Event) is set once it is answered and spoken."""
        text = (text or "").strip()
        if not text:
            return False
        return self._enqueue(("say", text, source, done))

    def push_to_talk(self):
        """Queue one recording, like a single press of the mic button."""
        if self.muted:
            return False
        if self.listen is None:
            return False
        return self._enqueue(("mic",))

    def run(self, fn):
        """Run fn() on the worker and post its Result, for UI actions that may
        ask for approval (deleting a memory) and so must never block the Tk thread."""
        return self._enqueue(("call", fn))

    def stop(self):
        """Window is closing: no new work, pending prompts answer No, worker let go."""
        with self._lock:
            self._closing = True
            self._jobs.clear()
            self._state = "idle"
        self.bridge.close()
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=2)  # a request on the network may take longer; it is a daemon

    def _enqueue(self, job):
        with self._lock:
            if self._closing:
                return False
            self._jobs.append(job)
            self._state = "listening" if job[0] == "mic" else "working"
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="kova-ui-agent", daemon=True)
                self._worker.start()
        return True

    def _work(self):
        while True:
            with self._lock:
                if not self._jobs:
                    self._worker = None
                    self._state = "idle"
                    return
                job = self._jobs.pop(0)
            self._run(job)

    def _run(self, job):
        try:
            if job[0] == "mic":
                self._record_and_handle()
            elif job[0] == "call":
                self._finish(job[1](), "ui")
            else:
                _, text, source, done = job
                try:
                    self._handle(text, source)
                finally:
                    if done is not None:
                        done.set()
        except Exception:
            # A broken request must not kill the worker the window depends on
            log.exception("Request failed")

    def _record_and_handle(self):
        heard = self.listen()
        if not heard:
            self._finish(Result(False, "Sorry, I didn't catch that."), "mic")
            return
        self._handle(heard, "mic")

    def _handle(self, text, source):
        with self._lock:
            self._state = "working"
        if self.on_user:
            self.bridge.post(self.on_user, text)
        self._source = source
        try:
            with reminders.hold_short_timers():
                result = self.agent.handle(text)
                self._finish(result, source)
        finally:
            self._source = None

    def _finish(self, result, source):
        if result is not None and self.on_result:
            self.bridge.post(self.on_result, result, source)
        if result is not None and result.sleep and self.on_sleep:
            self.on_sleep()
        if result is not None and result.message and source in SPOKEN and self.speak and not self._closing:
            # Said on the worker, so the next request waits until Max has finished talking
            for line in result.message.split("\n"):
                if line.strip():
                    self.speak(line)

    # ---------- the agent's callbacks ----------

    def confirm(self, question):
        """True only for a clear Yes on the pop-up; closing it is a No."""
        return self._roundtrip("confirm", question) is True

    def ask(self, question):
        """The text typed into the pop-up, or None when it was closed."""
        answer = self._roundtrip("ask", question)
        return answer if isinstance(answer, str) else None

    def trace(self, line):
        if self.on_trace:
            self.bridge.post(self.on_trace, line)

    def _roundtrip(self, kind, question):
        """Post the dialog to the UI thread and wait for its answer.

        Runs on the worker thread. A closed bridge, a window going away or a
        dialog nobody opened all answer "no" instead of waiting forever.
        """
        if self._source in SPOKEN and self.voice_prompt and not self.muted:
            return self.voice_prompt(kind, question)  # asked by voice, answered by voice
        if self.prompt is None or self.bridge.closed:
            return None
        answered = threading.Event()
        answer = []

        def open_prompt():
            try:
                answer.append(self.prompt(kind, question))
            finally:
                answered.set()

        if not self.bridge.post(open_prompt):
            return None
        while not answered.wait(0.1):
            if self.bridge.closed:
                return None
        return answer[0] if answer else None
