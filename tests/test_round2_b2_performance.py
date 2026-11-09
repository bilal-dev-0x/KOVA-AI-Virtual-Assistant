from scripts import measure_performance
from scripts.measure_performance import _command


def test_measurement_can_use_current_user_pet_setting():
    assert _command("current")[1:] == ["main.py", "--ui"]


def test_reaper_measurement_overrides_only_in_child_memory():
    off = " ".join(_command("off"))
    on = " ".join(_command("on"))
    assert "'PET_ON': False" in off
    assert "'PET_ON': True" in on
    assert "load_config=lambda" in off and "main.main()" in off
    assert "config.json" not in off.lower() and "config.json" not in on.lower()


def test_cpu_sampler_keeps_the_same_psutil_counter_between_samples(monkeypatch):
    class Process:
        pid = 5

        def __init__(self):
            self.readings = iter((0, 37.5))

        def create_time(self):
            return 123.0

        def cpu_percent(self, interval=None):
            assert interval is None
            return next(self.readings)

    process = Process()
    monkeypatch.setattr(measure_performance, "_CPU_HANDLES", {})
    assert measure_performance._cpu_percent(process) == 0.0
    assert measure_performance._cpu_percent(process) == 37.5
