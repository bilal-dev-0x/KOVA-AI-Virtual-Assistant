"""Notice repeated, low-risk action sequences without storing free-form user data."""
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from modules.tool_registry import BASE_DIR, SAFE, get

STATE_FILE = Path(BASE_DIR) / "data" / "routine_habits.json"
ROUTINES_FILE = Path(BASE_DIR) / "routines.json"
_ALLOWED = {"open_app", "move_window", "side_by_side"}
_SAFE_ARG = re.compile(r"^[\w .-]{1,100}$")
MAX_DAYS = 30
MAX_GAP_SECONDS = 15 * 60


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".kova-habits-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _clean_step(row):
    if not row.get("success") or row.get("source") not in {"local", "agent"}:
        return None
    name, args = row.get("tool"), row.get("args")
    registered = get(name) if isinstance(name, str) else None
    if not registered or registered.name not in _ALLOWED or registered.level != SAFE or not isinstance(args, dict):
        return None
    clean, error = registered.validate(args)
    if error or any(not isinstance(value, (str, int, float, bool)) for value in clean.values()):
        return None
    if any(not _SAFE_ARG.fullmatch(str(value)) for value in clean.values()):
        return None
    return {"tool": name, "args": clean}


def _clean_sequence(steps):
    if not isinstance(steps, list) or len(steps) < 3:
        return None
    return steps if all(_clean_step({"source": "local", "success": True, **step}) for step in steps) else None


def new_entries(log_path, offset):
    try:
        with open(log_path, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            stream.seek(offset if offset <= size else 0)
            raw = stream.read().decode("utf-8", errors="replace")
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    except OSError:
        return []


def observe(entries, today=None, state_path=None):
    """Track a short stream of safe actions; offer a 3+ step sequence repeated on 3 days."""
    if not entries:
        return None
    today = today or date.today()
    state_path = Path(state_path or STATE_FILE)
    state = _read(state_path)
    sequences = state.setdefault("sequences", {})
    tail = state.setdefault("tail", [])
    offered = state.setdefault("offered", [])
    suggestion = None
    for row in entries:
        step = _clean_step(row)
        try:
            stamp = datetime.fromisoformat(row["time"])
        except (KeyError, TypeError, ValueError):
            tail.clear()
            continue
        if step is None or stamp.date() > today or stamp.date() < today - timedelta(days=MAX_DAYS):
            tail.clear()
            continue
        if tail:
            try:
                previous = datetime.fromisoformat(tail[-1]["time"])
            except (KeyError, ValueError):
                tail.clear()
                previous = None
            if previous and (previous.date() != stamp.date() or not 0 <= (stamp - previous).total_seconds() <= MAX_GAP_SECONDS):
                tail.clear()
        tail.append({"time": stamp.isoformat(), "step": step})
        tail[:] = tail[-12:]
        if len(tail) < 3:
            continue
        steps = [item["step"] for item in tail[-3:]]
        signature = json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        dates = set(sequences.get(key, {}).get("days", []))
        dates.add(stamp.date().isoformat())
        item = sequences.setdefault(key, {"steps": steps, "days": []})
        item["steps"] = steps
        item["days"] = sorted(day for day in dates if day >= (today - timedelta(days=MAX_DAYS)).isoformat())
        if len(item["days"]) >= 3 and key not in offered:
            offered.append(key)
            state["pending"] = {"key": key, "steps": steps}
            suggestion = key
    if not any(state.get(key) for key in ("sequences", "tail", "offered", "pending")):
        return None
    _write(state_path, state)
    return suggestion


def pending(state_path=None):
    candidate = _read(state_path or STATE_FILE).get("pending")
    if not isinstance(candidate, dict):
        return None
    steps = _clean_sequence(candidate.get("steps", []))
    return {"key": candidate.get("key"), "steps": steps} if steps else None


def save_suggested(name, state_path=None, routines_path=None):
    name = " ".join((name or "").split())
    if not name or len(name) > 50 or any(ch in name for ch in "\r\n/\\"):
        return False, "Give the routine a short name."
    candidate = pending(state_path)
    if not candidate:
        return False, "There's no repeated routine suggestion waiting to save."
    routines_path = Path(routines_path or ROUTINES_FILE)
    data = _read(routines_path)
    rows = data.get("routines", []) if isinstance(data.get("routines", []), list) else []
    if any(str(row.get("name", "")).casefold() == name.casefold() for row in rows if isinstance(row, dict)):
        return False, f"A routine called {name} already exists."
    rows.append({"name": name, "phrases": [name], "steps": candidate["steps"]})
    _write(routines_path, {"routines": rows})
    state = _read(state_path or STATE_FILE)
    state.pop("pending", None)
    _write(state_path or STATE_FILE, state)
    from modules import routines
    routines._cache["key"] = None
    return True, f"Saved the {name} routine with {len(candidate['steps'])} safe steps."
