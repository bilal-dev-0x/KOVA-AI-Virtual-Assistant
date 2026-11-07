import threading

from modules import performance_monitor as perf
from modules.logger import get_logger

# System figures for the dashboard's status bar. One sample is taken every 2
# seconds on a background thread — never on the Tk thread, where the GPU
# counters (a PowerShell call) would freeze the window — and only while the
# window is showing them. stop() ends the thread, so a hidden window costs
# nothing: no polling, no CPU when nothing is shown.

log = get_logger()
EVERY_SECONDS = 2.0


def collect():
    """One reading as strings ready for the bar. A figure this PC can't read
    (no battery, no GPU counters) stays None instead of being guessed."""
    stats = {"cpu": None, "ram": None, "ram_percent": None, "battery": None, "gpu": None}

    try:
        stats["cpu"] = f"{perf.cpu_percent(interval=0.2):.0f}%"
    except Exception as e:
        log.debug("CPU reading failed: %s", e)

    try:
        ram = perf.ram_info()
        stats["ram"] = f"{ram['used_gb']:.1f}/{ram['total_gb']:.1f} GB"
        stats["ram_percent"] = f"{ram['percent']:.0f}%"
    except Exception as e:
        log.debug("RAM reading failed: %s", e)

    try:
        battery = perf.battery_info()
        if battery:
            state = "charging" if battery["plugged"] else "on battery"
            stats["battery"] = f"{battery['percent']:.0f}% {state}"
    except Exception as e:
        log.debug("Battery reading failed: %s", e)

    try:
        overall, _ = perf.gpu_usage(perf._gpu_samples())
        stats["gpu"] = f"{overall:.0f}%"
    except Exception as e:
        log.debug("GPU reading failed: %s", e)

    return stats


def bar(stats):
    """'CPU 12%   |   RAM 6.2/16.0 GB (39%)   |   GPU 3%   |   Battery 85% charging'"""
    parts = []
    if stats.get("cpu"):
        parts.append(f"CPU {stats['cpu']}")
    if stats.get("ram"):
        parts.append(f"RAM {stats['ram']} ({stats.get('ram_percent')})")
    if stats.get("gpu"):
        parts.append(f"GPU {stats['gpu']}")
    if stats.get("battery"):
        parts.append(f"Battery {stats['battery']}")
    return "   |   ".join(parts) or "Reading the system..."


class StatsCollector:
    """Samples every `every` seconds until stopped. One thread at most, and
    none at all once stop() has been called."""

    def __init__(self, push, every=EVERY_SECONDS, sample=collect):
        self.push = push          # push(stats) runs on the sampler thread
        self.every = every
        self.sample = sample
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Begin sampling (does nothing if it is already going)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            stop = threading.Event()   # each run has its own, so a slow old
            self._stop = stop          # sample can't outlive its replacement
            self._thread = threading.Thread(target=self._loop, args=(stop,),
                                            name="kova-ui-stats", daemon=True)
            self._thread.start()
        return True

    def stop(self):
        """End sampling. Returns once no further push can happen."""
        with self._lock:
            self._stop.set()
            self._thread = None

    def _loop(self, stop):
        while not stop.is_set():
            try:
                stats = self.sample()
            except Exception:
                log.exception("Stats reading failed")
                stats = None
            with self._lock:
                if stop.is_set():
                    return
                if stats:
                    self.push(stats)
            stop.wait(self.every)
