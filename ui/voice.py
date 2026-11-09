import threading
import time

from modules.logger import get_logger

# Voice mode inside the dashboard: the same loop as `python main.py` (say the
# wake word, Max stays awake until "go to sleep" or AWAKE_TIMEOUT of quiet),
# running on its own thread while the window takes typing at the same time.
#
# It never runs a request itself: what it hears goes to the Controller like a
# typed line, and it waits for the answer to be spoken before listening again,
# so Max never hears his own voice and never answers two things at once.

log = get_logger()

PAUSE = 0.3  # muted or busy: how long to wait before checking again


class VoiceLoop:
    def __init__(self, agent, controller, listen, speak, unsure=lambda: False,
                 finish_cut_off=lambda text, listen: text, clock=time.time):
        self.agent = agent
        self.controller = controller
        self.listen = listen                  # listen(wait_seconds, max_seconds) -> text | None
        self.speak = speak
        self.unsure = unsure                  # True when Google wasn't sure what it heard
        self.finish_cut_off = finish_cut_off
        self.clock = clock
        self.timeout = agent.config.get("AWAKE_TIMEOUT", 60)
        self.awake = False
        self.last_active = 0.0
        self._stop = threading.Event()
        self._thread = None

    # ---------- life ----------

    def start(self):
        self.controller.voice_state = "asleep"
        self._thread = threading.Thread(target=self._run, name="kova-ui-voice", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.controller.voice_state = "off"
        # Not joined: a listen in progress ends on its own within seconds, and the thread is a daemon

    def wake(self):
        """The push-to-talk key: awake now, no wake word needed."""
        self._set_awake(True)

    def _set_awake(self, awake):
        self.awake = awake
        self.last_active = self.clock()
        self.controller.voice_state = "awake" if awake else "asleep"

    def _run(self):
        words = self.agent.wake_words or [self.agent.name]
        self.speak(f"{self.agent.name} is online. Say {words[0]} when you need me.")
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                # A broken microphone or network blip must not end voice for the whole session
                log.exception("Voice loop step failed")
                self._stop.wait(2)

    # ---------- one round ----------

    def step(self):
        """Listen once and act on it. Returns what was sent to the agent, or None."""
        if self.controller.muted or self.controller.busy:
            self._stop.wait(PAUSE)  # muted, or a typed request is running: leave the mic alone
            return None

        if self.awake and self.timeout and self.clock() - self.last_active > self.timeout:
            log.info("Fell asleep after %ss idle", self.timeout)
            self._set_awake(False)
            self.speak("Going to sleep.")

        if not self.awake:
            heard = self.listen(wait_seconds=3, max_seconds=6)
            if not heard or self._stop.is_set() or self.controller.muted:
                return None
            command = self.agent.find_wake_word(heard)
            if command is None:
                return None
            log.info("Woke up")
            self._set_awake(True)
            if not self.agent.normalize(command):
                self.speak(f"Yes {self.agent.title}? I'm listening.")
                self.last_active = self.clock()
                return None
        else:
            command = self.listen(wait_seconds=5, max_seconds=10)
            if not command or self._stop.is_set() or self.controller.muted:
                return None

        if self.unsure():
            log.info("Not acted on, speech recognition was unsure: %s", command)
            self.speak(f"Sorry {self.agent.title}, I didn't catch that clearly. Please say it again.")
            self.last_active = self.clock()
            return None
        command = self.finish_cut_off(command, self.listen)

        ignore_noise = getattr(self.agent, "ignore_awake_noise", None)
        if self.awake and ignore_noise and ignore_noise(command):
            log.info("Ignored: %s", self.agent.normalize(command))
            self.last_active = self.clock()
            return None

        done = threading.Event()
        if not self.controller.submit(command, source="voice", done=done):
            return None
        done.wait()  # answered and spoken; the next listen must not catch Max's own voice
        self.last_active = self.clock()
        return command

    def went_to_sleep(self):
        """The agent answered "go to sleep": back to waiting for the wake word."""
        self._set_awake(False)

    # ---------- approvals by voice ----------

    def prompt(self, kind, question, is_yes):
        """Ask out loud and listen for the answer, like voice mode does."""
        self.speak(question)
        timeout = self.agent.config.get("APPROVAL_SECONDS", 45)
        answer = self.listen(wait_seconds=timeout, max_seconds=timeout)
        if kind == "ask":
            return answer
        if answer is None:
            # Google often misses a lone "yes", so ask for a few more words
            self.speak("Sorry, I didn't hear you. Say yes do it, or no.")
            answer = self.listen(wait_seconds=6, max_seconds=10)
        log.info("Confirmation %r -> %r", question, answer)
        if answer is None:
            self.speak("I didn't hear a yes, so I left it.")
            return False
        return is_yes(answer, question)
