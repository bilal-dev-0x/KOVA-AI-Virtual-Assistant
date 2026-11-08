import queue
import threading

from modules.logger import get_logger

# Everything crossing between Max's agent threads and the dashboard travels
# through this one queue. The Tk window drains it with a timer; the PyWebView
# window's pump thread waits on next_call().
#
# close() stops the bridge for good, so a window being destroyed can never leave
# a worker blocked on a dialog nobody will answer.

log = get_logger()


class Bridge:
    def __init__(self):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False

    @property
    def closed(self):
        with self._lock:
            return self._closed

    def post(self, fn, *args):
        """Run fn(*args) on the UI thread. False once the bridge is closed."""
        with self._lock:
            if self._closed:
                return False
            self._queue.put((fn, args))
        return True

    def drain(self):
        """Run everything posted so far. Only ever called on the Tk thread."""
        while True:
            try:
                fn, args = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except Exception:
                # One broken callback must not stop the rest or kill the mainloop
                log.exception("UI callback failed")

    def pending(self):
        return self._queue.qsize()

    def next_call(self, timeout=None):
        """Wait for one posted call and run it, for the web UI's pump thread.
        True when something ran. close() wins the race: what was taken before it
        is dropped, like drain() drops what close() finds, so a callback from a
        dead window never runs."""
        try:
            fn, args = self._queue.get(timeout=timeout)
        except queue.Empty:
            return False
        if self.closed:
            return True
        try:
            fn(*args)
        except Exception:
            log.exception("UI callback failed")
        return True

    def close(self):
        """Refuse new work and throw away what is waiting: the window is going away.

        Pending work is dropped, not run: a callback that touches a destroyed
        widget would only raise. Anyone blocked on a prompt sees closed and lets go.
        """
        with self._lock:
            self._closed = True
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
