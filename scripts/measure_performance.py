"""Measure KOVA startup and process-tree resource use on Windows.
Run: python scripts/measure_performance.py --runs 3 --idle-seconds 120
"""
import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import time
from ctypes import wintypes

import psutil

# Running this file directly sets sys.path[0] to scripts/, not the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desktop_agent import Agent

_CPU_HANDLES = {}


def visible_kova_window(pid):
    found = []
    try:
        owner_pids = {p.pid for p in psutil.Process(pid).children(recursive=True)} | {pid}
    except psutil.Error:
        return None
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, title, length + 1)
        owner = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if title.value.startswith("KOVA —") and owner.value in owner_pids:
            found.append(title.value)
        return True

    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else None


def _cpu_percent(process):
    """Keep psutil's per-process counter between samples; a new object always starts at 0%."""
    stamp = process.create_time()
    cached = _CPU_HANDLES.get(process.pid)
    if cached is None or cached[0] != stamp:
        _CPU_HANDLES[process.pid] = (stamp, process)
        process.cpu_percent(interval=None)
        return 0.0
    return cached[1].cpu_percent(interval=None)


def tree_metrics(pid):
    try:
        root = psutil.Process(pid)
        processes = [root] + root.children(recursive=True)
    except psutil.Error:
        return 0.0, 0.0
    cpu, private_bytes = 0.0, 0
    current = {process.pid for process in processes}
    for old_pid in set(_CPU_HANDLES) - current:
        _CPU_HANDLES.pop(old_pid, None)
    for process in processes:
        try:
            cpu += _cpu_percent(process)
            memory = process.memory_full_info()
            private_bytes += getattr(memory, "uss", getattr(memory, "private", 0))
        except psutil.Error:
            continue
    return cpu, private_bytes / (1024 * 1024)


def stop_started_tree(root):
    """Stop only the root process and descendants it created."""
    try:
        root_process = psutil.Process(root.pid)
        processes = root_process.children(recursive=True) + [root_process]
    except psutil.Error:
        processes = []
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(processes, timeout=10)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    root.wait()


def _command(pet_mode):
    """Launch the normal UI, optionally overriding only PET_ON in memory.

    The override never writes the user's config.json; the app still loads all other
    settings normally, but none of them are printed or returned by this script.
    """
    if pet_mode == "current":
        return [sys.executable, "main.py", "--ui"]
    value = "True" if pet_mode == "on" else "False"
    code = (
        "import main,sys; original=main.load_config; "
        f"main.load_config=lambda: {{**original(), 'PET_ON': {value}}}; "
        "sys.argv=['main.py','--ui']; main.main()"
    )
    return [sys.executable, "-c", code]


def run_once(idle_seconds, pet_mode="current"):
    local_agent = Agent({}, provider=None)
    command_started = time.perf_counter()
    local_result = local_agent.handle("what time is it")
    first_local_answer = time.perf_counter() - command_started
    if not local_result.success:
        raise RuntimeError("Local command did not return successfully: " + local_result.message)
    started = time.perf_counter()
    child = subprocess.Popen(_command(pet_mode), cwd=os.getcwd())
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and child.poll() is None:
            if visible_kova_window(child.pid):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Dashboard window did not appear within 90 seconds.")
        startup = time.perf_counter() - started
        tree_metrics(child.pid)  # prime the CPU counters
        samples = []
        deadline = time.monotonic() + idle_seconds
        while time.monotonic() < deadline:
            time.sleep(min(5, max(0, deadline - time.monotonic())))
            samples.append(tree_metrics(child.pid))
        return (startup, first_local_answer, statistics.median(x[0] for x in samples),
                statistics.median(x[1] for x in samples))
    finally:
        if child.poll() is None:
            stop_started_tree(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--idle-seconds", type=int, default=120)
    parser.add_argument("--pet-mode", choices=("current", "off", "on"), default="current",
                        help="current config or a temporary in-memory Reaper override")
    args = parser.parse_args()
    if args.runs < 1 or args.idle_seconds < 1:
        parser.error("--runs and --idle-seconds must both be positive")
    results = [run_once(args.idle_seconds, args.pet_mode) for _ in range(args.runs)]
    print(json.dumps({"pet_mode": args.pet_mode, "runs": results,
                      "median": [statistics.median(col) for col in zip(*results)]}))


if __name__ == "__main__":
    main()
