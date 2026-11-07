"""Day 6 step 3: status bar figures and the sampler thread. psutil and the
PowerShell GPU counters are faked, so nothing here depends on this PC."""
import time

from modules import performance_monitor as perf
from ui import stats


def fake_system(monkeypatch, cpu=12.4, ram=None, battery=None, gpu=3.0):
    ram = ram if ram is not None else {"used_gb": 6.2, "total_gb": 16.0, "percent": 39}
    monkeypatch.setattr(perf, "cpu_percent", lambda interval=1.0: cpu)
    monkeypatch.setattr(perf, "ram_info", lambda: ram)
    monkeypatch.setattr(perf, "battery_info", lambda: battery)
    monkeypatch.setattr(perf, "_gpu_samples", lambda: ["pid_1|3.4"])
    monkeypatch.setattr(perf, "gpu_usage", lambda lines: (gpu, {1: gpu}))


# ---------- one reading ----------

def test_every_figure_is_formatted_for_the_bar(monkeypatch):
    fake_system(monkeypatch, battery={"percent": 85, "plugged": True})
    got = stats.collect()
    assert got == {"cpu": "12%", "ram": "6.2/16.0 GB", "ram_percent": "39%",
                   "battery": "85% charging", "gpu": "3%"}
    assert stats.bar(got) == "CPU 12%   |   RAM 6.2/16.0 GB (39%)   |   GPU 3%   |   Battery 85% charging"


def test_no_battery_and_no_gpu_are_left_out_not_guessed(monkeypatch):
    def no_gpu_counters():
        raise OSError("no counters")

    fake_system(monkeypatch, battery=None)
    monkeypatch.setattr(perf, "_gpu_samples", no_gpu_counters)
    got = stats.collect()
    assert got["battery"] is None and got["gpu"] is None
    assert stats.bar(got) == "CPU 12%   |   RAM 6.2/16.0 GB (39%)"


def test_on_battery_is_shown_as_such(monkeypatch):
    fake_system(monkeypatch, battery={"percent": 41, "plugged": False})
    assert stats.collect()["battery"] == "41% on battery"


def test_nothing_readable_says_so():
    assert stats.bar({}) == "Reading the system..."
    assert stats.bar({"cpu": None, "ram": None, "gpu": None, "battery": None}) == "Reading the system..."


# ---------- the sampler ----------

def collector(sample, push, every=0.02):
    return stats.StatsCollector(push, every=every, sample=sample)


def wait_for(condition, timeout=5):
    end = time.time() + timeout
    while not condition():
        if time.time() > end:
            raise AssertionError("timed out")
        time.sleep(0.005)


def test_samples_repeatedly_until_stopped():
    pushes, samples = [], []
    kova = collector(sample=lambda: samples.append(1) or {"cpu": "1%"}, push=pushes.append)
    assert kova.start() is True
    wait_for(lambda: len(pushes) >= 2)
    kova.stop()
    assert kova.running is False
    # After stop() returns no further sample can be pushed
    snapshot = len(pushes)
    time.sleep(0.08)
    assert len(pushes) == snapshot


def test_start_twice_keeps_only_one_thread():
    pushes = []
    kova = collector(sample=lambda: {"cpu": "1%"}, push=pushes.append)
    assert kova.start() is True
    assert kova.start() is False
    kova.stop()
    assert kova.running is False


def test_a_failed_reading_does_not_kill_the_collector():
    pushes = []
    calls = []

    def sample():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("powershell gone")
        return {"cpu": "9%"}

    kova = collector(sample=sample, push=pushes.append, every=0.01)
    kova.start()
    wait_for(lambda: pushes)
    kova.stop()
    assert pushes[-1] == {"cpu": "9%"}


def test_a_collector_starts_again_after_the_window_is_shown_again():
    """Hide stops it, show starts it: the window is visible on and off all day."""
    pushes = []
    kova = collector(sample=lambda: {"cpu": "1%"}, push=pushes.append, every=0.01)
    assert kova.start() is True
    wait_for(lambda: pushes)
    kova.stop()

    first = len(pushes)
    assert kova.start() is True
    wait_for(lambda: len(pushes) > first)
    kova.stop()
    assert kova.running is False
    snapshot = len(pushes)
    time.sleep(0.06)
    assert len(pushes) == snapshot
