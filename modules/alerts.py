import threading
import time

from modules.logger import get_logger

# Alerts: one background thread that looks at the battery, RAM and disks once a minute
# (one cheap psutil read, near-zero CPU) and warns before the PC gets into trouble.
# Off by default: "turn on alerts" sets ALERTS_ON in config.json.
# An alert is spoken between two listens, like a reminder, so it never talks over you.

log = get_logger()

CHECK_SECONDS = 60
COOLDOWN_SECONDS = 30 * 60   # the same alert is repeated at most this often
DEFAULTS = {"ALERT_BATTERY_PERCENT": 20, "ALERT_RAM_PERCENT": 90, "ALERT_DISK_FREE_PERCENT": 10}

# main.py replaces this so an alert is spoken in voice mode
on_alert = None

_thread = None
_stop = threading.Event()
_last_said = {}   # alert key -> when it was last said


def default_notify(message):
    print(f"\n⚠️  {message}")
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def thresholds(config):
    return {key: float(config.get(key, value)) for key, value in DEFAULTS.items()}


def readings():
    """What the checks look at: one quick psutil read of each."""
    from modules import performance_monitor as pm
    ram = pm.ram_info()
    top = pm._top_programs(1) if ram["percent"] >= 50 else []
    return {"battery": pm.battery_info(), "ram": ram["percent"],
            "top_app": top[0][0].removesuffix(".exe") if top else None,
            "disks": pm.disk_info()}


def check(read, limits):
    """[(key, message)] for every limit crossed right now. Pure, so tests can feed it numbers."""
    found = []
    battery = read.get("battery")
    if battery and not battery["plugged"] and battery["percent"] < limits["ALERT_BATTERY_PERCENT"]:
        found.append(("battery", f"Battery is at {battery['percent']:.0f} percent. Plug in the charger."))
    if read.get("ram", 0) > limits["ALERT_RAM_PERCENT"]:
        app = f" {read['top_app']} is using the most." if read.get("top_app") else ""
        found.append(("ram", f"RAM is at {read['ram']:.0f} percent.{app}"))
    for disk in read.get("disks", []):
        free = 100 - disk["percent"]
        if free < limits["ALERT_DISK_FREE_PERCENT"]:
            found.append((f"disk {disk['drive']}", f"Drive {disk['drive']} is almost full: "
                                                   f"{disk['free_gb']:.0f} gigabytes free."))
    return found


def due(found, now=None):
    """The alerts not said in the last COOLDOWN_SECONDS, and marks them said."""
    now = time.time() if now is None else now
    fresh = []
    for key, message in found:
        if now - _last_said.get(key, -COOLDOWN_SECONDS) >= COOLDOWN_SECONDS:
            _last_said[key] = now
            fresh.append(message)
    return fresh


def check_once(config, now=None):
    try:
        found = check(readings(), thresholds(config))
    except Exception:
        log.exception("Alert check failed")
        return []
    for message in due(found, now):
        log.info("Alert: %s", message)
        (on_alert or default_notify)(message)
    return found


FIRST_CHECK_SECONDS = 5  # a short pause first, so starting Max isn't slowed down


def _loop(config):
    if _stop.wait(FIRST_CHECK_SECONDS):
        return
    while True:
        check_once(config)
        if _stop.wait(CHECK_SECONDS):
            return


def running():
    return _thread is not None and _thread.is_alive()


def start(config):
    """Start watching (does nothing if already running)."""
    global _thread
    if running():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(config,), name="alerts", daemon=True)
    _thread.start()
    log.info("Alerts on: %s", thresholds(config))
    return True


def stop():
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=2)
    _thread = None
    _last_said.clear()
    log.info("Alerts off")
