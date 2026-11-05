import os
import re
import subprocess
import time

from modules.logger import get_logger
from modules.result import Result

# Uptime, startup apps (read only) and Windows power plans.
# Power plans change only through powercfg with a fixed plan ID from PLANS below:
# no text from the user or the AI ever reaches the command line.

log = get_logger()


# ---------- uptime ----------

def uptime_seconds():
    import psutil
    return time.time() - psutil.boot_time()


def say_duration(seconds):
    """93784 -> '1 day, 2 hours and 3 minutes'."""
    seconds = int(seconds)
    parts = []
    for size, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count} {name}{'s' if count > 1 else ''}")
    if not parts:
        return "less than a minute"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def uptime_report():
    seconds = uptime_seconds()
    message = f"Your PC has been on for {say_duration(seconds)}."
    if seconds > 3 * 86400:
        message += " A restart now and then keeps Windows quick."
    return Result(True, message)


# ---------- startup apps (view only) ----------

RUN_KEYS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
]
APPROVED_KEYS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"),
]


def _registry_values(hive, path):
    import winreg
    root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    values = {}
    try:
        with winreg.OpenKey(root, path) as key:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                values[name] = data
                i += 1
    except OSError:
        pass
    return values


def _disabled_names():
    """Entries switched off in Task Manager's Startup tab (first byte 3 = disabled)."""
    off = set()
    for hive, path in APPROVED_KEYS:
        for name, data in _registry_values(hive, path).items():
            if isinstance(data, bytes) and data[:1] == b"\x03":
                off.add(name.lower())
    return off


def _startup_folders():
    folders = [os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
               os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\StartUp")]
    return [f for f in folders if os.path.isdir(f)]


def _pretty(name):
    """'MicrosoftEdgeAutoLaunch_1F8C9CAA...' -> 'Microsoft Edge'; 'Steam.lnk' -> 'Steam'."""
    name = re.sub(r"\.(lnk|exe|bat|cmd|url)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[_ ]?[0-9A-Fa-f]{16,}$", "", name)           # one entry per browser profile
    if name.endswith("AutoLaunch"):                                # "MicrosoftEdgeAutoLaunch" -> "Microsoft Edge"
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.removesuffix("AutoLaunch"))
    return name.replace("_", " ").strip()


def startup_apps():
    """[(name, where, enabled)] for everything that starts with Windows."""
    if os.name != "nt":
        return []
    disabled = _disabled_names()
    found = {}
    for hive, path in RUN_KEYS:
        for name in _registry_values(hive, path):
            found.setdefault(name.lower(), (_pretty(name), "registry", name.lower() not in disabled))
    for folder in _startup_folders():
        for entry in os.listdir(folder):
            if entry.lower() == "desktop.ini":
                continue
            found.setdefault(entry.lower(), (_pretty(entry), "Startup folder", entry.lower() not in disabled))
    return sorted(found.values(), key=lambda item: item[0].lower())


def _grouped(names):
    """['Microsoft Edge', 'Microsoft Edge', 'OneDrive'] -> ['Microsoft Edge (2 entries)', 'OneDrive']."""
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return [f"{name} ({n} entries)" if n > 1 else name for name, n in counts.items()]


def startup_report():
    apps = startup_apps()
    if not apps:
        return Result(True, "Nothing extra starts with Windows.")
    on = _grouped([name for name, _, enabled in apps if enabled])
    off = _grouped([name for name, _, enabled in apps if not enabled])
    message = f"{len(on)} app{'s' if len(on) != 1 else ''} start with Windows: {', '.join(on[:8])}"
    if len(on) > 8:
        message += f", and {len(on) - 8} more"
    message += "."
    if off:
        message += f" {len(off)} more {'is' if len(off) == 1 else 'are'} switched off."
    detail = "\n".join(f"  {'on ' if enabled else 'off'}  {name:<34} {where}" for name, where, enabled in apps)
    detail += "\n  To change these: Task Manager > Startup. Max only reads this list."
    return Result(True, message, detail=detail)


# ---------- power plans ----------

# Windows' own plan IDs; these never change between PCs
PLANS = {
    "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "high performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "power saver": "a1841308-3bae-4152-9a37-4ff3d8a9d01b",
    "ultimate performance": "e9a42b02-d5df-448d-aa00-03f14749eb61",
}
GUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+\((.+?)\)(\s*\*)?", re.I)


def _powercfg(*args):
    out = subprocess.run(["powercfg", *args], capture_output=True, text=True, timeout=10)
    return out.returncode, out.stdout


def power_plans():
    """{guid: (name, active)} for the plans this PC has."""
    code, out = _powercfg("/list")
    if code != 0:
        return {}
    return {m[1].lower(): (m[2].strip(), bool(m[3])) for m in GUID.finditer(out)}


def active_plan():
    """(guid, name) of the plan in use, or (None, None)."""
    for guid, (name, active) in power_plans().items():
        if active:
            return guid, name
    return None, None


def power_plan_report():
    _, name = active_plan()
    if name is None:
        return Result(False, "I couldn't read the power plan.")
    return Result(True, f"You're on the {name} power plan.")


def set_power_plan(plan):
    """Switch to one of PLANS by its name. Undo switches back to the plan before."""
    guid = PLANS.get(plan)
    if guid is None:
        return Result(False, f"I don't know a power plan called {plan}.")
    plans = power_plans()
    if guid not in plans:
        return Result(False, f"This PC doesn't have the {plan} plan. "
                             f"It has: {', '.join(name for name, _ in plans.values())}.")
    before, before_name = active_plan()
    if before == guid:
        return Result(True, f"You're already on the {plans[guid][0]} plan.")
    code, _ = _powercfg("/setactive", guid)
    if code != 0 or active_plan()[0] != guid:
        return Result(False, f"Windows didn't switch to the {plan} plan.")
    log.info("Power plan %s -> %s", before_name, plans[guid][0])
    result = Result(True, f"Switched to the {plans[guid][0]} power plan.")
    if before:
        def undo():
            code, _ = _powercfg("/setactive", before)
            return Result(code == 0, f"Back on the {before_name} plan." if code == 0
                          else f"I couldn't switch back to {before_name}.")
        result.undo = undo
        result.undo_text = f"switch back to the {before_name} plan"
    return result
