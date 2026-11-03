from modules import performance_monitor as pm


def test_cpu_in_range():
    assert 0 <= pm.cpu_percent(interval=0.2) <= 100


def test_ram_in_range():
    ram = pm.ram_info()
    assert ram["total_gb"] > 0
    assert 0 < ram["used_gb"] <= ram["total_gb"]
    assert 0 <= ram["percent"] <= 100


def test_disks_found():
    disks = pm.disk_info()
    assert disks, "expected at least one drive"
    for d in disks:
        assert 0 <= d["free_gb"] <= d["total_gb"]


def test_system_status_has_summary_and_detail():
    result = pm.system_status()
    assert result.success
    assert "CPU" in result.message
    assert "RAM" in result.detail and "Disk" in result.detail
