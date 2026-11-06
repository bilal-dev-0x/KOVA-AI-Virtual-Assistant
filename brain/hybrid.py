import threading
import time

from brain.base import BrainError, Provider, Session
from brain.budget import WARN_AT
from modules.logger import get_logger

# Gemini first, the KOVA server as the backup. Gemini answers in 2-3 seconds while its free
# quota lasts; when it can't (the day's quota is used up, it's busy, or the internet is down),
# the KOVA server answers instead, so Max never goes without a brain. After a Gemini failure the
# server keeps answering for BACKUP_MINUTES before Gemini is tried again, so a used-up quota
# doesn't cost a failed request every time. Gemini sees the screen; the server can't see
# pictures, so it gets the screen's text, read by Windows OCR.

BACKUP_MINUTES = 10
SWITCH_NOTE = ("🖥️  Gemini can't answer right now, so the KOVA server will. "
               "Its first answer can take a minute.")
READ_NOTE = "🖥️  Gemini can't look right now: reading the screen's text for the KOVA server."

log = get_logger()


class HybridProvider(Provider):
    name = "gemini+server"

    def __init__(self, primary, backup):
        self.primary = primary
        self.backup = backup
        self._primary_back_at = 0.0  # Gemini is tried again from this time on
        self._warmed = False
        self._warming = None  # the warm-up thread while it runs

    # ---------- getting the server ready before it is needed ----------

    def running_low(self):
        """True when Gemini is close to stopping for the day: most of Max's own limit used, or
        half of its models out of quota."""
        budget = getattr(self.primary, "budget", None)
        if budget is None:
            return False
        try:
            data = budget._load()
        except Exception:
            return False
        models = set(getattr(self.primary, "models", []))
        out = len(models & set(data["exhausted"]))
        if (budget.limit and data["requests"] >= budget.limit) or (models and out == len(models)):
            return False  # already out: the request that falls back does the reading itself
        near = bool(budget.limit) and data["requests"] >= budget.limit * WARN_AT
        tired = bool(models) and out >= max(1, len(models) // 2)
        return near or tired

    def warm_backup(self, system_prompt, tools):
        """Once a day, when Gemini runs low: load the server's model and let it read the system
        prompt and tools in the background while Gemini still answers. The first server answer
        then takes seconds instead of a minute and a half."""
        if self._warmed or not hasattr(self.backup, "warm_up") or not self.running_low():
            return
        self._warmed = True

        def warm():
            started = time.time()
            try:
                self.backup.warm_up(system_prompt, tools)
                log.info("KOVA server ready as the backup brain (%.0fs)", time.time() - started)
            except Exception as e:
                log.warning("Getting the KOVA server ready failed: %s", e)
        self._warming = threading.Thread(target=warm, name="warm-server", daemon=True)
        self._warming.start()

    def wait_for_warm_up(self):
        """A request sent while the warm-up still runs would read everything again next to it:
        let the warm-up finish, then the request starts from what it read."""
        if self._warming is not None and self._warming.is_alive():
            self._warming.join()

    @property
    def can_look(self):
        return self.primary.can_look or self.backup.can_look

    def primary_ok(self):
        return time.time() >= self._primary_back_at

    def primary_failed(self, error):
        if self.primary_ok():
            log.warning("Gemini failed (%s), the KOVA server answers for the next %s minutes", error, BACKUP_MINUTES)
            print(SWITCH_NOTE)
        self._primary_back_at = time.time() + BACKUP_MINUTES * 60

    def session(self, system_prompt, tools):
        return HybridSession(self, system_prompt, tools)

    def search(self, query):
        return self.primary.search(query) if self.primary_ok() else None

    def look(self, image, question, context="", read=None):
        """Gemini sees the picture; when it can't, the server answers from the screen's text."""
        if self.primary_ok() and self.primary.can_look:
            try:
                return self.primary.look(image, question, context, read=read)
            except BrainError as e:
                if not self.backup.can_look:
                    raise
                self.primary_failed(e)
        if not self.backup.can_look:
            raise BrainError("Gemini can't look right now, and the KOVA server can't read the screen here.")
        print(READ_NOTE)
        self.wait_for_warm_up()
        return self.backup.look(image, question, context, read=read)


class HybridSession(Session):
    def __init__(self, provider, system_prompt, tools):
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools
        self._sessions = {"primary": provider.primary.session(system_prompt, tools), "backup": None}
        self.active = "primary"

    def _session(self, which):
        if self._sessions[which] is None:
            self._sessions[which] = self.provider.backup.session(self.system_prompt, self.tools)
        return self._sessions[which]

    def send(self, text):
        if self.provider.primary_ok():
            self.provider.warm_backup(self.system_prompt, self.tools)
            self.active = "primary"
            try:
                return self._session("primary").send(text)
            except BrainError as e:
                self._session("primary").reset()
                self.provider.primary_failed(e)
        self.active = "backup"
        self.provider.wait_for_warm_up()
        try:
            return self._session("backup").send(text)
        except BrainError as e:
            raise BrainError(f"Gemini can't answer right now, and {str(e)[0].lower()}{str(e)[1:]}") from e

    def send_results(self, results):
        # Tool results go back to the brain that asked for them: the other one never saw the calls
        return self._session(self.active).send_results(results)

    def reset(self):
        for session in self._sessions.values():
            if session is not None:
                session.reset()
        self.active = "primary"
