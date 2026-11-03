from modules.result import Result

# Everything here runs only when asked. There is no background polling.

GB = 1024 ** 3


def cpu_percent(interval=1.0):
    import psutil
    return psutil.cpu_percent(interval=interval)


def ram_info():
    import psutil
    mem = psutil.virtual_memory()
    return {"used_gb": (mem.total - mem.available) / GB, "total_gb": mem.total / GB, "percent": mem.percent}


def disk_info():
    import psutil
    disks = []
    for part in psutil.disk_partitions(all=False):
        if not part.fstype or "cdrom" in part.opts:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        if usage.total < GB:
            continue  # empty card reader or virtual drive
        disks.append({
            "drive": part.device.rstrip("\\"),
            "free_gb": usage.free / GB,
            "total_gb": usage.total / GB,
            "percent": usage.percent
        })
    return disks


def battery_info():
    import psutil
    battery = psutil.sensors_battery()
    if battery is None:
        return None
    return {"percent": battery.percent, "plugged": battery.power_plugged}


def cpu_report():
    return Result(True, f"CPU usage is {cpu_percent():.0f} percent.")


def ram_report():
    ram = ram_info()
    return Result(True, f"RAM usage is {ram['percent']:.0f} percent, "
                        f"{ram['used_gb']:.1f} of {ram['total_gb']:.1f} gigabytes.")


def disk_report():
    disks = disk_info()
    if not disks:
        return Result(False, "I couldn't read any drives.")

    detail = "\n".join(f"  {d['drive']}  {d['free_gb']:.0f} GB free of {d['total_gb']:.0f} GB "
                       f"({d['percent']:.0f}% used)" for d in disks)
    spoken = ", ".join(f"{d['drive'][0]} drive has {d['free_gb']:.0f} gigabytes free" for d in disks)
    return Result(True, spoken + ".", detail=detail)


def battery_report():
    battery = battery_info()
    if battery is None:
        return Result(False, "I couldn't find a battery on this computer.")
    state = "charging" if battery["plugged"] else "on battery"
    return Result(True, f"Battery is at {battery['percent']:.0f} percent, {state}.")


def system_status():
    cpu = cpu_percent()
    ram = ram_info()
    disks = disk_info()
    battery = battery_info()

    spoken = f"CPU is at {cpu:.0f} percent and RAM at {ram['percent']:.0f} percent"
    if battery:
        spoken += f", battery {battery['percent']:.0f} percent"

    lines = [
        f"  CPU      {cpu:.0f}%",
        f"  RAM      {ram['used_gb']:.1f} / {ram['total_gb']:.1f} GB ({ram['percent']:.0f}%)"
    ]
    for d in disks:
        lines.append(f"  Disk {d['drive']}  {d['free_gb']:.0f} GB free of {d['total_gb']:.0f} GB")
    if battery:
        lines.append(f"  Battery  {battery['percent']:.0f}% ({'charging' if battery['plugged'] else 'on battery'})")

    return Result(True, spoken + ".", detail="\n".join(lines))
