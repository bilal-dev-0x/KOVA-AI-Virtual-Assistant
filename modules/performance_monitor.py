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


def top_processes(sort_by="ram", count=5):
    """Heaviest programs, with every process of a program added together
    (Chrome runs dozens of chrome.exe processes)."""
    import time

    import psutil

    procs = list(psutil.process_iter(["name", "memory_info"]))
    if sort_by == "cpu":
        # cpu_percent needs two readings; the first call only starts measuring
        for p in procs:
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(0.5)

    totals = {}
    for p in procs:
        name = p.info["name"]
        if not name or name.lower() in ("system idle process", "idle"):
            continue
        try:
            value = p.cpu_percent(None) / psutil.cpu_count() if sort_by == "cpu" else p.info["memory_info"].rss / 1024 ** 2
        except (psutil.Error, AttributeError):
            continue
        totals[name] = totals.get(name, 0) + value

    top = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:count]
    if not top:
        return Result(False, "I couldn't read the running processes.")

    unit = (lambda v: f"{v:.0f}% CPU") if sort_by == "cpu" else (lambda v: f"{v:.0f} MB")
    detail = "\n".join(f"  {name:<28} {unit(value)}" for name, value in top)
    spoken = ", ".join(f"{name.removesuffix('.exe')} {unit(value)}" for name, value in top)
    return Result(True, f"Top {len(top)} by {sort_by.upper()}: {spoken}.", detail=detail)


def _program_totals():
    """RAM in MB per program name, e.g. {"chrome.exe": 5743.2}."""
    import psutil
    totals = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        name, mem = p.info["name"], p.info["memory_info"]
        if name and mem:
            totals[name] = totals.get(name, 0) + mem.rss / 1024 ** 2
    return totals


def program_memory(query):
    """RAM used by one program, found by app name ("vs code"), process name ("Code.exe")
    or a near miss from speech recognition ("cloud" for Claude)."""
    from modules import system_control
    from modules.fuzzy import best_match

    totals = _program_totals()
    by_short = {name.lower().removesuffix(".exe"): name for name in totals}
    query = query.lower().strip().removesuffix(".exe")

    # An app from apps.json knows its process names: "vs code" -> Code.exe
    target = system_control.resolve(query)
    if target and target[0] == "app" and target[2].get("process"):
        wanted = {p.lower() for p in target[2]["process"]}
        used = sum(mb for name, mb in totals.items() if name.lower() in wanted)
        if used:
            return Result(True, f"{target[1]} is using {_size(used)} of RAM.")
        return Result(True, f"{target[1]} isn't running.")

    match = best_match(query, by_short.keys(), cutoff=0.6)
    if match is None:
        return Result(False, f"I couldn't find a running program called {query}.")
    return Result(True, f"{match} is using {_size(totals[by_short[match]])} of RAM.")


def _size(mb):
    return f"{mb / 1024:.1f} gigabytes" if mb >= 1024 else f"{mb:.0f} megabytes"


# Windows' own GPU counters, the same numbers Task Manager shows. A fixed command, never AI text.
GPU_COUNTER_SCRIPT = (r"(Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue)"
                      r".CounterSamples | ForEach-Object { $_.InstanceName + '|' + $_.CookedValue }")


def _gpu_samples():
    """Lines like 'pid_1234_luid_0x0_0x19119_phys_0_eng_0_engtype_3d|5.7'."""
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command", GPU_COUNTER_SCRIPT],
                         capture_output=True, text=True, timeout=15).stdout
    return out.splitlines()


def gpu_usage(lines):
    """(overall percent, {pid: percent}) from counter lines.

    Like Task Manager: an engine's load is the sum over processes, the GPU's load is its
    busiest engine, and a process's load is its busiest engine.
    """
    import re
    engines, per_process = {}, {}
    for line in lines:
        instance, _, value = line.rpartition("|")
        match = re.match(r"pid_(\d+)_luid_(\w+?_\w+?)_phys_\d+_eng_(\d+)_engtype_(\w*)", instance)
        if not match:
            continue
        try:
            load = float(value.replace(",", "."))
        except ValueError:
            continue
        pid, luid, engine, kind = match.groups()
        key = (luid, engine, kind)
        engines[key] = engines.get(key, 0) + load
        loads = per_process.setdefault(int(pid), {})
        loads[key] = loads.get(key, 0) + load
    overall = min(100.0, max(engines.values(), default=0.0))
    return overall, {pid: min(100.0, max(loads.values())) for pid, loads in per_process.items()}


def gpu_report(count=3):
    import psutil
    try:
        overall, per_pid = gpu_usage(_gpu_samples())
    except Exception:
        return Result(False, "I couldn't read the GPU counters on this PC.")

    totals = {}
    for pid, load in per_pid.items():
        try:
            name = psutil.Process(pid).name().removesuffix(".exe")
        except psutil.Error:
            continue
        totals[name] = totals.get(name, 0) + load
    busy = sorted(((n, l) for n, l in totals.items() if l >= 1), key=lambda item: -item[1])[:count]

    message = f"GPU usage is {overall:.0f} percent."
    if busy:
        message += " Most of it is " + ", ".join(f"{n} {l:.0f} percent" for n, l in busy) + "."
    temperature = gpu_temperature()
    if temperature:
        message += f" The {temperature[0]} is at {temperature[1]} degrees."
    return Result(True, message)


def gpu_temperature():
    """(name, degrees C) of an NVIDIA GPU from nvidia-smi, or None. A fixed command, never AI text."""
    import shutil
    import subprocess
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        name, degrees = out.splitlines()[0].rsplit(",", 1)
        return name.replace("NVIDIA ", "").strip(), int(degrees)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


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
