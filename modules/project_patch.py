"""Apply one reviewed unified diff to a project file through KOVA's action runner."""
import difflib
import hashlib
import os
import re
import tempfile
import uuid
from pathlib import Path

from modules.result import Result
from modules.tool_registry import BASE_DIR

MAX_PATCH = 20_000
MAX_FILE = 500_000
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*['\"]\S{8,}")
_PREVIEWS = {}


def _key(path, patch, project):
    return hashlib.sha256(f"{project}\0{path}\0{patch}".encode("utf-8")).hexdigest()


def _root(project=""):
    if not project:
        return Path(BASE_DIR).resolve(), None
    from modules import dev
    found = dev.find_project(project)
    if found is None:
        return None, f"I can't find a project called {project}."
    return Path(found.path).resolve(), None


def _target(path, project=""):
    raw = (path or "").strip().replace("\\", "/")
    candidate = Path(raw)
    if not raw or candidate.is_absolute() or any(part in {"", ".", ".."} for part in raw.split("/")):
        return None, "Use a relative path inside the KOVA project."
    if any(part.lower() in {".git", "data", "logs", ".venv", "venv", "node_modules"}
           for part in candidate.parts):
        return None, "That project path is protected."
    if candidate.name.lower() in {"config.json", "credentials.json", "secrets.json"} or \
            candidate.name.lower().startswith(".env") or "secret" in candidate.name.lower():
        return None, "Secret and configuration files are protected."
    root, error = _root(project)
    if error:
        return None, error
    unresolved = root
    for part in candidate.parts:
        unresolved = unresolved / part
        if unresolved.is_symlink():
            return None, "Symbolic links are not patchable."
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, "That path is outside the project."
    if target.is_symlink() or not target.is_file():
        return None, "Choose an existing regular project file."
    if target.stat().st_size > MAX_FILE:
        return None, "That file is too large for a reviewed patch."
    return target, None


def _apply_unified(original, patch, path):
    if not isinstance(patch, str) or not patch.strip() or len(patch) > MAX_PATCH:
        return None, "The patch is empty or exceeds 20,000 characters."
    if _SECRET.search(patch):
        return None, "The patch appears to contain a secret, so I stopped."
    rows = patch.splitlines()
    if len(rows) < 3 or not rows[0].startswith("--- ") or not rows[1].startswith("+++ "):
        return None, "Give me a standard unified diff for exactly one existing file."
    old_name = rows[0][4:].split("\t", 1)[0].removeprefix("a/")
    new_name = rows[1][4:].split("\t", 1)[0].removeprefix("b/")
    if old_name != path or new_name != path or old_name == "/dev/null" or new_name == "/dev/null":
        return None, "The diff must change only the named existing file."
    source = original.splitlines()
    output, cursor, changed, i = [], 0, False, 2
    while i < len(rows):
        match = _HUNK.match(rows[i])
        if not match:
            return None, "The diff has an invalid or unsupported section."
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_count = int(match.group(4) or 1)
        start = max(old_start - 1, 0)
        if start < cursor or start > len(source):
            return None, "The patch hunks overlap or point outside the file."
        output.extend(source[cursor:start])
        cursor = start
        consumed = produced = 0
        i += 1
        while i < len(rows) and not rows[i].startswith("@@ "):
            row = rows[i]
            if row.startswith("\\ No newline at end of file"):
                i += 1
                continue
            if not row or row[0] not in " +-":
                return None, "The diff contains an unsupported line."
            text = row[1:]
            if row[0] in " -":
                if cursor >= len(source) or source[cursor] != text:
                    return None, "The patch context no longer matches the file; nothing changed."
                cursor += 1
                consumed += 1
            if row[0] in " +":
                output.append(text)
                produced += 1
            changed |= row[0] != " "
            i += 1
        if consumed != old_count or produced != new_count:
            return None, "The diff hunk counts are inconsistent."
    output.extend(source[cursor:])
    if not changed:
        return None, "The diff makes no changes."
    return output, None


def preview(path, patch, project=""):
    target, error = _target(path, project)
    if error:
        return None, error
    try:
        raw = target.read_bytes()
        original = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "That file can't be read as UTF-8 text."
    updated, error = _apply_unified(original, patch, path.replace("\\", "/"))
    if error:
        return None, error
    newline = "\r\n" if b"\r\n" in raw else "\n"
    result = newline.join(updated) + (newline if raw.endswith((b"\n", b"\r")) else "")
    diff = "\n".join(difflib.unified_diff(original.splitlines(), result.splitlines(),
                                         fromfile=path, tofile=path, lineterm=""))
    if not diff or len(diff) > MAX_PATCH:
        return None, "The resulting patch is empty or too large to review."
    return (target, raw, result.encode("utf-8"), diff), None


def apply(path, patch, project=""):
    prepared = _PREVIEWS.pop(_key(path, patch, project), None)
    if prepared is None:
        return Result(False, "I don't have a matching reviewed preview for that patch.")
    target, before, after, diff = prepared
    # Detect edits made after the user saw and approved the preview.
    if target.read_bytes() != before:
        return Result(False, "The file changed after preview; I stopped without applying the patch.")
    backup_dir = Path(BASE_DIR) / "data" / "patch_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{uuid.uuid4().hex}.bak"
    backup.write_bytes(before)
    try:
        fd, temporary = tempfile.mkstemp(prefix=".kova-patch-", dir=str(target.parent))
        with os.fdopen(fd, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except (OSError, UnboundLocalError):
            pass
        backup.unlink(missing_ok=True)
        return Result(False, f"I couldn't apply the patch: {exc}")

    def undo():
        if not backup.exists():
            return Result(False, "The patch backup is missing.")
        if target.read_bytes() != after:
            return Result(False, "The file changed after the patch; I left those newer edits alone.")
        fd, temporary = tempfile.mkstemp(prefix=".kova-undo-", dir=str(target.parent))
        with os.fdopen(fd, "wb") as stream:
            stream.write(backup.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        backup.unlink(missing_ok=True)
        return Result(True, f"Restored {path} to its contents before the patch.")

    result = Result(True, f"Applied the reviewed patch to {path}.", detail=diff)
    result.undo = undo
    result.undo_text = f"undo the patch to {path}"
    return result


def check_args(args, trace=None):
    prepared, error = preview(args["path"], args["patch"], args.get("project", ""))
    if error:
        return Result(False, error)
    _PREVIEWS.clear()
    _PREVIEWS[_key(args["path"], args["patch"], args.get("project", ""))] = prepared
    if trace:
        trace(f"Patch preview for {args['path']} (review this before approval):\n{prepared[3]}")
    return None
