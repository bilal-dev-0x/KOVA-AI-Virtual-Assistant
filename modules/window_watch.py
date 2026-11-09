import threading
import time

from modules.logger import get_logger

# Max reads each window as it comes to the front, so "click Save" doesn't have to wait for a
# read first. One small thread looks at the front window twice a second (a cheap call); when a
# window has stayed in front for a moment, it reads the names of its buttons, links and fields
# once and keeps them. Clicking then searches only for that one name.
#
# Only names and kinds are kept, never what is typed in a field, and only in memory. Terminals,
# admin and hung windows, and KOVA's own windows are never read. WINDOW_WATCH: false in
# config.json turns it off.

log = get_logger()

POLL_SECONDS = 0.5
SETTLE_SECONDS = 0.8     # a window must stay in front this long before it is read
FRESH_SECONDS = 30       # names older than this are read again on the next click
KEEP = 12                # windows remembered

_seen = {}               # hwnd -> (title, time read, [Control without element])
_lock = threading.Lock()
_thread = None
_stop = threading.Event()


def controls_for(hwnd, title):
    """The names read for this window while it had this title, if fresh; else None."""
    with _lock:
        entry = _seen.get(hwnd)
    if not entry:
        return None
    seen_title, when, controls = entry
    if seen_title != title or time.time() - when > FRESH_SECONDS:
        return None
    return controls


def remember(hwnd, title, controls):
    from modules.ui_automation import Control
    names = [Control(c.name, c.kind, None, c.password) for c in controls if c.name and not c.password]
    with _lock:
        _seen[hwnd] = (title, time.time(), names)
        if len(_seen) > KEEP:
            oldest = min(_seen, key=lambda h: _seen[h][1])
            _seen.pop(oldest, None)


def forget_all():
    with _lock:
        _seen.clear()


def _may_read(hwnd, title):
    from modules import ui_automation, windows
    if title.startswith("KOVA"):   # target_window already skips KOVA's own windows; the pet too
        return False
    try:
        return not (ui_automation._is_terminal(hwnd) or ui_automation._is_hung(hwnd)
                    or windows._runs_as_admin(hwnd) is True)
    except Exception:
        return False


def _watch():
    from modules import ui_automation, windows
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    last = None
    since = 0.0
    while not _stop.wait(POLL_SECONDS):
        try:
            front = windows.target_window()
        except Exception:
            front = None
        if front is None:
            last = None
            continue
        hwnd, app, title = front
        if (hwnd, title) != last:
            last, since = (hwnd, title), time.time()
            continue
        if time.time() - since < SETTLE_SECONDS or controls_for(hwnd, title) is not None:
            continue
        if not _may_read(hwnd, title):
            continue
        started = time.time()
        try:
            controls = ui_automation.read_controls(hwnd)
        except Exception as e:
            log.debug("Reading %s ahead failed: %s", app, e)
            remember(hwnd, title, [])
            continue
        remember(hwnd, title, controls)
        log.debug("Read %s ahead: %d controls in %.1fs", app, len(controls), time.time() - started)


def start(config=None):
    """Begin watching (dashboard mode). False when turned off in config."""
    global _thread
    if config is not None and config.get("WINDOW_WATCH", True) is False:
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_watch, name="kova-window-watch", daemon=True)
    _thread.start()
    log.info("Reading windows ahead for quick clicks")
    return True


def stop():
    _stop.set()
    forget_all()
