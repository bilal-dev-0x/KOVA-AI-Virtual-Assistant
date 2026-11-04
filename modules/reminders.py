import itertools
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from modules.logger import get_logger

# Each reminder is a sleeping timer thread: nothing polls, so waiting costs no CPU.
# Reminders live in memory only for now; Day 3's SQLite memory can keep them across restarts.

log = get_logger()
_ids = itertools.count(1)
_pending = {}
_lock = threading.Lock()

# main.py replaces this so a reminder can be spoken in voice mode
on_fire = None

# A short timer set while Max is still answering starts when the answer is done:
# "set a timer for 10 seconds" went off before Max had even said "timer set"
SHORT_SECONDS = 60
_holding = 0
_held = []


@dataclass
class Reminder:
    id: int
    message: str
    due: float
    timer: threading.Timer = None
    seconds: float = 0

    def when(self):
        return datetime.fromtimestamp(self.due).strftime("%I:%M %p").lstrip("0")


def default_notify(message):
    print(f"\n⏰ Reminder: {message}")
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


def _fire(reminder_id):
    with _lock:
        reminder = _pending.pop(reminder_id, None)
    if reminder is None:
        return
    log.info("Reminder %s fired: %s", reminder_id, reminder.message)
    (on_fire or default_notify)(reminder.message)


def add(message, seconds):
    reminder = Reminder(next(_ids), message, time.time() + seconds, seconds=seconds)
    reminder.timer = threading.Timer(seconds, _fire, args=(reminder.id,))
    reminder.timer.daemon = True  # don't keep Max running just for a timer
    with _lock:
        _pending[reminder.id] = reminder
        hold = _holding and seconds <= SHORT_SECONDS
        if hold:
            _held.append(reminder)
    if not hold:
        reminder.timer.start()
    log.info("Reminder %s set for %s: %s", reminder.id, reminder.when(), message)
    return reminder


@contextmanager
def hold_short_timers():
    """Short timers added inside this block start counting when it ends."""
    global _holding
    with _lock:
        _holding += 1
    try:
        yield
    finally:
        with _lock:
            _holding -= 1
            ready = [] if _holding else list(_held)
            if not _holding:
                _held.clear()
        for reminder in ready:
            if reminder.id in _pending:
                reminder.due = time.time() + reminder.seconds
                reminder.timer.start()


def pending():
    with _lock:
        return sorted(_pending.values(), key=lambda r: r.due)


def cancel(reminder_id=None):
    """Cancel one reminder, or all of them when reminder_id is None. Returns how many."""
    with _lock:
        ids = list(_pending) if reminder_id is None else [reminder_id]
        cancelled = [_pending.pop(i) for i in ids if i in _pending]
    for reminder in cancelled:
        reminder.timer.cancel()
    return len(cancelled)


def seconds_until(clock_time, now=None):
    """Seconds until a time of day like "17:30" or "5:30 pm"; tomorrow if it has passed."""
    now = now or datetime.now()
    text = clock_time.strip().lower().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    due = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return (due - now).total_seconds()


def describe_delay(seconds):
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    text = f"{hours} hour{'s' if hours != 1 else ''}"
    return text + (f" {minutes} minute{'s' if minutes != 1 else ''}" if minutes else "")
