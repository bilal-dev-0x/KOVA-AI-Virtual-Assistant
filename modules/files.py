import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from modules import system_control
from modules.logger import get_logger
from modules.result import Result
from modules.tool_registry import BASE_DIR

# File tools: find, open, create, rename, organise a folder (with an undo log that survives a
# restart) and delete to the Recycle Bin. Only in your own folders (Desktop, Documents, ...)
# and on non-system fixed drives; never in Windows, Program Files, AppData or Max's own folder.
# Programs and scripts are never opened, because opening them would run code.
#
# The Windows calls are small functions at the bottom, so tests can replace them.

log = get_logger()

# Windows' own IDs for your folders; Desktop and Documents may live in OneDrive
KNOWN_FOLDERS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
FOLDER_WORDS = {"desktop": "desktop", "documents": "documents", "document": "documents", "docs": "documents",
                "downloads": "downloads", "download": "downloads", "pictures": "pictures", "photos": "pictures",
                "music": "music", "songs": "music", "videos": "videos", "video": "videos"}
# Opening these runs code (or points Windows at something that does)
RUNS_CODE = {".exe", ".bat", ".cmd", ".ps1", ".psm1", ".psc1", ".vbs", ".vbe", ".vb", ".js", ".jse", ".ws", ".wsf",
             ".wsh", ".msi", ".msp", ".msc", ".lnk", ".url", ".reg", ".scr", ".com", ".pif", ".cpl", ".hta", ".jar",
             ".py", ".pyw", ".appref-ms", ".chm", ".application", ".appinstaller", ".msix", ".msixbundle", ".appx",
             ".inf", ".scf", ".settingcontent-ms", ".library-ms", ".search-ms", ".gadget", ".xll"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "$recycle.bin", "system volume information",
             "appdata", "windowsapps", "recovery", "steamlibrary", "site-packages"}
# Never renamed or deleted, wherever they are: Windows' own and Steam's library
PROTECTED_PARTS = {"$recycle.bin", "system volume information", "recovery", "steamlibrary", "windowsapps"}
PROTECTED_FILES = {"pagefile.sys", "swapfile.sys", "hiberfil.sys"}
# Only these get sorted: your own loose files, not a drive or a project laid out by hand
ORGANISABLE = ("downloads", "desktop", "documents")
FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM = 0x2, 0x4
FIND_SECONDS = 3
FIND_LIMIT = 20
DELETE_LIMIT = 20
FOLDER_FILE_LIMIT = 200  # a folder with more than this inside is deleted by hand, not by voice
RECENT_SECONDS = 120     # a file changed this recently may still be downloading
PARTIAL = {".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload"}
CATEGORIES = {
    "Pictures": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg", ".ico", ".tif", ".tiff"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx",
                  ".md", ".epub"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv"},
    "Music": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".iso"},
    "Installers": {".exe", ".msi", ".msix", ".appx", ".apk"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".h", ".cs", ".asm", ".ipynb",
             ".sql", ".sh", ".bat", ".ps1"},
}
KNOWN_SUFFIXES = set().union(*CATEGORIES.values()) | RUNS_CODE
ORGANISE_LOG_DIR = Path(BASE_DIR) / "data" / "organise_log"
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
ORDINALS = {"first": 0, "one": 0, "1": 0, "1st": 0, "second": 1, "two": 1, "2": 1, "2nd": 1, "third": 2,
            "three": 2, "3": 2, "3rd": 2, "fourth": 3, "four": 3, "4": 3, "4th": 3, "fifth": 4, "five": 4,
            "5": 4, "5th": 4, "last": -1}
FIND_FILLER = {"my", "the", "a", "an", "file", "files", "called", "named", "document", "documents", "for", "me",
               "on", "in", "pc", "computer", "laptop", "this", "all", "disk", "drive", "hard",
               "dot", "format", "extension"}  # "pic dot png" is pic.png
NEAR = 0.75  # how close a misheard word must be to a name ("vichora" for "visora")

_known = {}
_found = []       # the last find results, for "open the second one"
_last = {"path": None}  # the file or folder the last action was about, for "rename it"


# ---------- places ----------

def known_folder(key):
    """The real path of Desktop, Documents, ... (asked from Windows once, then cached)."""
    if key not in _known:
        _known[key] = _known_folder_path(KNOWN_FOLDERS[key]) or str(Path.home() / key.capitalize())
    return _known[key]


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def _inside(path, root):
    path, root = _norm(path), _norm(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives
        return False


def allowed_roots():
    return [known_folder(k) for k in KNOWN_FOLDERS] + [f"{d}:\\" for d in _fixed_drives() if not _is_system_drive(d)]


def blocked_roots():
    env = os.environ
    roots = [env.get("SystemRoot", r"C:\Windows"), env.get("ProgramFiles", r"C:\Program Files"),
             env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), env.get("ProgramData", r"C:\ProgramData"),
             str(Path.home() / "AppData"), BASE_DIR]
    return [r for r in roots if r]


def check_place(path, change=True):
    """None if Max may work with path, else a Result saying why not. change: renaming, moving
    or deleting it (then a whole drive or one of your main folders itself is refused too)."""
    real = os.path.realpath(path)
    if any(_inside(real, root) for root in blocked_roots()):
        return Result(False, f"{real} is a system folder or my own, so I won't touch it.")
    if not any(_inside(real, root) for root in allowed_roots()):
        return Result(False, f"I only work in your own folders and on your D drive, not in {real}.")
    if change:
        if Path(real).parent == Path(real):
            return Result(False, f"{real} is a whole drive, so I won't change it.")
        if any(_norm(real) == _norm(known_folder(k)) for k in KNOWN_FOLDERS):
            return Result(False, f"{Path(real).name} is one of your main folders, so I won't change it itself.")
        parts = {p.lower() for p in Path(real).parts}
        if parts & PROTECTED_PARTS or Path(real).name.lower() in PROTECTED_FILES:
            return Result(False, f"{Path(real).name} belongs to Windows or Steam, so I won't change it.")
        if os.path.isdir(real) and _looks_installed(real):
            return Result(False, f"{Path(real).name} looks like an installed program or game. "
                                 "Use its uninstaller or change it yourself.")
        if _inside_program(real):
            return Result(False, f"{Path(real).name} is part of an installed program or game, so I won't change it.")
    return None


def _has_program(folder):
    """True if folder has a .exe or .dll at its top level (stops at the first; unreadable counts)."""
    try:
        with os.scandir(folder) as entries:
            return any(e.is_file() and Path(e.name).suffix.lower() in (".exe", ".dll") for e in entries)
    except OSError:
        return True


def _inside_program(path):
    """True if the folder holding path, or the one above it, has a program at its top level
    (a .pak next to VALORANT.exe). Never looks at a drive root or one of your main folders:
    a setup.exe in Downloads doesn't make your other downloads part of a program."""
    stops = {_norm(known_folder(k)) for k in KNOWN_FOLDERS}
    folder = os.path.dirname(path)
    for _ in range(2):
        if Path(folder).parent == Path(folder) or _norm(folder) in stops:
            return False
        if _has_program(folder):
            return True
        folder = os.path.dirname(folder)
    return False


def _looks_installed(folder):
    """True if the folder has a program (.exe or .dll) at its top or one level down; stops at the first."""
    try:
        with os.scandir(folder) as top:
            subfolders = []
            for entry in top:
                if entry.is_file() and Path(entry.name).suffix.lower() in (".exe", ".dll"):
                    return True
                if entry.is_dir(follow_symlinks=False):
                    subfolders.append(entry.path)
        for sub in subfolders:
            with os.scandir(sub) as inner:
                if any(e.is_file() and Path(e.name).suffix.lower() in (".exe", ".dll") for e in inner):
                    return True
    except OSError:
        return True  # can't look inside: don't risk it
    return False


def place(where):
    """'desktop', 'my documents', 'd drive coding arc' or a full path -> a folder path, or None."""
    where = (where or "desktop").strip().lower()
    where = re.sub(r"^(on |in |inside |at )?(my |the )?", "", where).removesuffix(" folder")
    if where in FOLDER_WORDS:
        return known_folder(FOLDER_WORDS[where])
    path = system_control.spoken_path(where) or (where if os.path.isabs(where) else None)
    return path if path and os.path.isdir(path) else None


def where_said(path):
    """'C:\\Users\\Bilal\\Documents\\cv.pdf' -> 'Documents': how a folder is said out loud."""
    parent = os.path.dirname(path)
    for key in KNOWN_FOLDERS:
        if _norm(parent) == _norm(known_folder(key)):
            return key.capitalize()
    return parent


# ---------- finding ----------

def _query_words(query):
    # Single letters ("in d drive") say where, not what
    return [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in FIND_FILLER and len(w) > 1]


def _matches(name, words):
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    return all(any(t.startswith(w) for t in tokens) for w in words)


def _near(name, words):
    """Every word is in the name or spelled nearly like a word of it: speech recognition hears
    "vichora" for "visora". Only for words of 4 letters or more."""
    from difflib import SequenceMatcher
    tokens = re.findall(r"[a-z0-9]+", name.lower())

    def close(w, t):
        if t.startswith(w):
            return True
        if len(w) < 4 or len(t) < 4:
            return False
        m = SequenceMatcher(None, w, t)
        return m.quick_ratio() >= NEAR and m.ratio() >= NEAR
    return all(any(close(w, t) for t in tokens) for w in words)


def find(query, seconds=FIND_SECONDS, limit=FIND_LIMIT):
    """Files and folders whose name has every word of the query, from your main folders first,
    then other drives. Stops after `seconds` or `limit` hits, so it never ties up the PC."""
    words = _query_words(query)
    if not words:
        return Result(False, "What should I look for?")
    deadline = time.time() + seconds
    order = ["desktop", "documents", "downloads", "pictures", "music", "videos"]
    roots = list(dict.fromkeys([known_folder(k) for k in order] + allowed_roots()))
    # realpath only for the few roots: per entry it opens every file (slow, and wakes OneDrive files)
    skip = {_norm(root) for root in blocked_roots()}
    hits, near, seen, timed_out = [], [], set(), False
    for root in roots:
        stack = [root]
        while stack and len(hits) < limit:
            if time.time() > deadline:
                timed_out = True
                break
            folder = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if name.startswith(".") or name.lower() in SKIP_DIRS:
                    continue
                key = os.path.normcase(entry.path)
                if key in seen or key in skip:
                    continue
                seen.add(key)
                if _matches(name, words):
                    hits.append(entry.path)
                elif not hits and len(near) < limit and _near(name, words):
                    near.append(entry.path)  # used only if nothing matches exactly
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
        if timed_out or len(hits) >= limit:
            break

    hits = hits or near
    exact = [h for h in hits if " ".join(_query_words(Path(h).stem)) == " ".join(words)]
    hits = exact + sorted((h for h in hits if h not in exact), key=_mtime, reverse=True)
    _found[:] = hits
    if not hits:
        message = f"I couldn't find anything called {' '.join(words)} in your folders."
        if timed_out:
            message += f" I stopped looking after {seconds} seconds."
        return Result(False, message)
    _last["path"] = hits[0]
    said = [f"{n}, {Path(h).name} in {where_said(h)}" for n, h in zip(("one", "two", "three", "four", "five"), hits)]
    count = f"{len(hits)}{'+' if timed_out or len(hits) >= limit else ''}"
    message = (f"I found {Path(hits[0]).name} in {where_said(hits[0])}." if len(hits) == 1
               else f"I found {count}: " + "; ".join(said) + ". Say open the first one, or another number.")
    return Result(True, message, detail="\n".join(f"  {i}. {h}" for i, h in enumerate(hits, 1)))


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def pick(ref):
    """What 'the second one', 'it', a name or a full path means: a path, or None."""
    ref = (ref or "").strip().strip('"')
    low = re.sub(r"^(the |number |file |folder )+", "", ref.lower())
    low = re.sub(r" (one|file|folder|result)$", "", low)
    if low in ORDINALS:
        index = ORDINALS[low]
        return _found[index] if _found and index < len(_found) else None
    if low in ("it", "that", "this", "them", "that file", "this file", "that folder", "this folder"):
        return _last["path"]
    spoken = system_control.spoken_path(ref) if re.match(r"^[a-z](:| drive)", low) else None
    if spoken or os.path.isabs(ref):
        return spoken or ref
    # Compared word by word, so "pitbull png" and "pitbull dot png" both mean pitbull.png
    said = _words_of(low)

    def named(name):
        return said in (_words_of(name), _words_of(Path(name).stem))
    for path in _found:
        if named(Path(path).name):
            return path
    for key in ("desktop", "documents", "downloads"):
        folder = known_folder(key)
        try:
            for entry in os.scandir(folder):
                if named(entry.name):
                    return entry.path
        except OSError:
            continue
    return None


def _words_of(text):
    return " ".join(w for w in re.findall(r"[a-z0-9]+", text.lower()) if w != "dot")


def _missing(ref):
    if (ref or "").lower().strip() in ("it", "that", "this"):
        return Result(False, "I don't know which file you mean yet. Find it first, or say its name.")
    return Result(False, f"I can't find {ref}. Say find, then its name, first.")


# ---------- open, create, rename ----------

def open_item(ref):
    path = pick(ref)
    if path is None or not os.path.exists(path):
        return _missing(ref)
    problem = check_place(path, change=False)
    if problem:
        return problem
    if os.path.isfile(path) and Path(path).suffix.lower() in RUNS_CODE:
        return Result(False, f"{Path(path).name} is a program or a script. I don't open those, because that "
                             "would run it. Open it yourself if you trust it.")
    _start(path)
    _last["path"] = path
    log.info("Opened %s", path)
    return Result(True, f"Opening {Path(path).name}")


def valid_name(name):
    """None if name is fine for a file or folder, else why not."""
    name = (name or "").strip()
    if not name or name in (".", ".."):
        return "the name is empty"
    if BAD_NAME.search(name):
        return 'a name can\'t have \\ / : * ? " < > |'
    if name.rstrip(" .") != name:
        return "a name can't end with a dot or a space"
    if Path(name).stem.lower() in RESERVED:
        return f"Windows doesn't allow the name {name}"
    if len(name) > 120:
        return "the name is too long"
    return None


def unique(path):
    """path, or 'name (1).ext', 'name (2).ext' ... when it is taken: nothing is ever overwritten."""
    path = Path(path)
    if not path.exists():
        return str(path)
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return str(candidate)
    raise FileExistsError(path)


def create(called, kind="folder", where="desktop", text=""):
    problem = valid_name(called)
    if problem:
        return Result(False, f"I can't use that name: {problem}.")
    folder = place(where)
    if folder is None:
        return Result(False, f"I can't find the folder {where}.")
    problem = check_place(folder, change=False)
    if problem:
        return problem
    name = called.strip()
    if kind != "folder" and not Path(name).suffix:
        name += ".txt"
    target = os.path.join(folder, name)
    if os.path.exists(target):
        return Result(False, f"{name} already exists in {where_said(target)}.")
    if kind == "folder":
        os.mkdir(target)
    else:
        with open(target, "x", encoding="utf-8") as f:
            f.write(text)
    _last["path"] = target
    log.info("Created %s", target)
    stamp = _mtime(target)

    def undo():
        if kind == "folder":
            try:
                os.rmdir(target)  # only works while it is empty
            except OSError:
                return Result(False, f"{name} isn't empty any more, so I left it.")
        elif _mtime(target) == stamp:
            os.remove(target)
        else:
            return Result(False, f"{name} was changed since, so I left it.")
        return Result(True, f"Removed {name} again.")

    result = Result(True, f"Created the {'folder' if kind == 'folder' else 'file'} {name} in {where_said(target)}.")
    result.undo, result.undo_text = undo, f"remove {name} again"
    return result


def rename(ref, new_name):
    path = pick(ref)
    if path is None or not os.path.exists(path):
        return _missing(ref)
    problem = check_place(path)
    if problem:
        return problem
    new_name = (new_name or "").strip()
    problem = valid_name(new_name)
    if problem:
        return Result(False, f"I can't use that name: {problem}.")
    old = Path(path)
    new_suffix = Path(new_name).suffix.lower()
    if new_suffix in RUNS_CODE and new_suffix != old.suffix.lower():
        return Result(False, f"I won't turn a file into a {new_suffix} program or script.")
    if old.is_file() and old.suffix and new_suffix not in KNOWN_SUFFIXES:
        new_name += old.suffix  # "rename cv.pdf to my cv" keeps .pdf; "report 2.0" too
    target = old.with_name(new_name)
    if target.exists() and _norm(target) != _norm(old):
        return Result(False, f"{new_name} already exists there, so I didn't rename it.")
    os.rename(old, target)
    _last["path"] = str(target)
    log.info("Renamed %s -> %s", old, target)

    def undo():
        if not target.exists() or old.exists():
            return Result(False, f"I couldn't rename it back to {old.name}.")
        os.rename(target, old)
        _last["path"] = str(old)
        return Result(True, f"It's called {old.name} again.")

    result = Result(True, f"Renamed {old.name} to {target.name}.")
    result.undo, result.undo_text = undo, f"rename it back to {old.name}"
    return result


# ---------- organise a folder ----------

def category(path):
    suffix = Path(path).suffix.lower()
    for name, suffixes in CATEGORIES.items():
        if suffix in suffixes:
            return name
    return "Other"


def organisable(where):
    """(folder, None) for Downloads, Desktop or Documents, else (None, Result) why not. A drive or a
    folder you laid out yourself (a code project, D:\\Coding Arc) is never sorted."""
    folder = place(where)
    if folder is None or not any(_norm(folder) == _norm(known_folder(k)) for k in ORGANISABLE):
        return None, Result(False, "I only sort your Downloads, Desktop or Documents folder, so I left "
                                   f"{where} as it is.")
    problem = check_place(folder, change=False)
    if problem:
        return None, problem
    if os.path.isdir(os.path.join(folder, ".git")):
        return None, Result(False, f"{Path(folder).name} is a code project, so I won't sort it.")
    return folder, None


def _hidden(entry):
    try:
        return bool(entry.stat().st_file_attributes & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))
    except (OSError, AttributeError):
        return False


def organise_plan(folder, now=None):
    """[(file, category)] for the loose files in folder. Left alone: still-downloading and very new
    files, hidden and system files (Thumbs.db, desktop.ini)."""
    now = time.time() if now is None else now
    plan = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_file(follow_symlinks=False) or entry.name.lower() in ("desktop.ini", "thumbs.db"):
            continue
        if _hidden(entry) or entry.name.startswith("."):
            continue
        if Path(entry.name).suffix.lower() in PARTIAL or now - _mtime(entry.path) < RECENT_SECONDS:
            continue
        plan.append((entry.path, category(entry.path)))
    return plan


def describe_plan(plan):
    """'12 pictures, 9 documents and 3 other files'."""
    counts = {}
    for _, cat in plan:
        counts[cat] = counts.get(cat, 0) + 1
    parts = []
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        word = "other file" if cat == "Other" else cat.lower().rstrip("s")
        parts.append(f"{n} {word}{'s' if n > 1 else ''}")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def organise(where="downloads", now=None):
    folder, problem = organisable(where)
    if problem:
        return problem
    plan = organise_plan(folder, now)
    if not plan:
        return Result(True, f"{Path(folder).name} has no loose files to sort.")

    record = {"folder": folder, "time": datetime.now().isoformat(timespec="seconds"), "moves": [], "created": []}
    failed, unusable = 0, set()
    try:
        for source, cat in plan:
            target_dir = os.path.join(folder, cat)
            try:
                if cat in unusable:
                    raise OSError(f"{target_dir} can't be used")
                if not os.path.isdir(target_dir):
                    os.mkdir(target_dir)  # fails if a FILE has that name: then the category is skipped
                    record["created"].append(target_dir)
                target = unique(os.path.join(target_dir, os.path.basename(source)))
                os.rename(source, target)  # same drive: a quick rename, never a copy or an overwrite
                record["moves"].append({"from": source, "to": target})
            except OSError as e:
                if not os.path.isdir(target_dir):
                    unusable.add(cat)
                failed += 1
                log.warning("Could not move %s: %s", source, e)
    finally:
        # Written whatever happened, so every file that did move can be put back
        log_path = _write_log(record)
    moved = len(record["moves"])
    log.info("Organised %s: %s moved, %s failed, log %s", folder, moved, failed, log_path)
    message = f"Sorted {moved} file{'s' if moved != 1 else ''} in {Path(folder).name} into folders."
    if failed:
        message += f" {failed} couldn't be moved; they may be open, or a folder name was taken by a file."
    result = Result(moved > 0, message, detail=f"  Undo log: {log_path}")
    if moved:
        result.undo, result.undo_text = (lambda: undo_organise(log_path=log_path)), f"put {Path(folder).name} back"
    return result


def _write_log(data):
    ORGANISE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = ORGANISE_LOG_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return str(path)


def latest_log(folder=None):
    """The newest log not undone yet; with folder, the newest for that folder."""
    try:
        logs = sorted((p for p in ORGANISE_LOG_DIR.glob("*.json") if not p.name.endswith(".undone.json")),
                      reverse=True)
    except OSError:
        return None
    for path in logs:
        if folder is None:
            return str(path)
        try:
            if _norm(json.loads(path.read_text(encoding="utf-8"))["folder"]) == _norm(folder):
                return str(path)
        except (OSError, ValueError, KeyError):
            continue
    return None


def undo_organise(where="", log_path=None):
    """Move every file back where it was, from the undo log (works after a restart too). With
    where ("downloads"), the last organising of that folder."""
    if log_path is None:
        folder = place(where) if where else None
        if where and folder is None:
            return Result(False, f"I can't find the folder {where}.")
        log_path = latest_log(folder)
    if not log_path or not os.path.exists(log_path):
        return Result(False, "I have no organising to undo" + (f" in {where}." if where else "."))
    data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    back, missing, failed = 0, 0, 0
    for move in reversed(data["moves"]):
        if not os.path.exists(move["to"]):
            missing += 1
            continue
        try:
            os.rename(move["to"], unique(move["from"]))
            back += 1
        except OSError as e:
            failed += 1
            log.warning("Could not put back %s: %s", move["to"], e)
    for folder in reversed(data.get("created", [])):
        try:
            os.rmdir(folder)  # only the folders Max made, and only if empty now
        except OSError:
            pass
    if not failed:
        # Done: it won't be undone twice. With failures it stays, so asking again retries them
        os.replace(log_path, log_path.removesuffix(".json") + ".undone.json")
    message = f"Put {back} file{'s' if back != 1 else ''} back in {Path(data['folder']).name}."
    if missing:
        message += f" {missing} had been moved or deleted since."
    if failed:
        message += f" {failed} couldn't be moved back; they may be open. Ask me again in a moment."
    log.info("Undid organising %s: %s back, %s missing, %s failed", data["folder"], back, missing, failed)
    return Result(failed == 0 and (back > 0 or not data["moves"]), message)


# ---------- delete to the Recycle Bin ----------

def _split_targets(targets):
    return [t.strip() for t in re.split(r"[;\n]", targets or "") if t.strip()]


def delete_check(targets):
    """(paths, None) when every target may go to the Recycle Bin, else (None, Result) why not."""
    refs = _split_targets(targets)
    if not refs:
        return None, Result(False, "Delete what?")
    if len(refs) > DELETE_LIMIT:
        return None, Result(False, f"That's {len(refs)} things. I delete at most {DELETE_LIMIT} at once.")
    paths = []
    for ref in refs:
        path = pick(ref)
        if path is None or not os.path.exists(path):
            return None, _missing(ref)
        problem = check_place(path)
        if problem:
            return None, problem
        drive = os.path.splitdrive(os.path.realpath(path))[0]
        # USB sticks, cards and network drives have no Recycle Bin: Windows would delete for good
        if not re.match(r"^[a-zA-Z]:$", drive) or _drive_type(drive + "\\") != DRIVE_FIXED:
            return None, Result(False, f"{Path(path).name} is on a removable or network drive, which has no "
                                       "Recycle Bin. Delete it yourself if you're sure.")
        if os.path.isdir(path) and _count_inside(path, FOLDER_FILE_LIMIT + 1) > FOLDER_FILE_LIMIT:
            return None, Result(False, f"{Path(path).name} has more than {FOLDER_FILE_LIMIT} files in it. "
                                       "Please delete it yourself in File Explorer.")
        paths.append(os.path.realpath(path))
    return list(dict.fromkeys(paths)), None


def _count_inside(folder, stop):
    count = 0
    for _, dirs, names in os.walk(folder):
        count += len(dirs) + len(names)
        if count >= stop:
            break
    return count


def delete(targets):
    paths, problem = delete_check(targets)
    if problem:
        return problem
    if not _recycle(paths):
        return Result(False, "Windows didn't move it to the Recycle Bin.")
    gone = [p for p in paths if not os.path.exists(p)]
    names = [Path(p).name for p in gone]
    log.info("Recycled %s", gone)
    if not gone:
        return Result(False, "Windows didn't move it to the Recycle Bin.")
    if any(_norm(p) == _norm(_last["path"] or "") for p in gone):
        _last["path"] = None
    said = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return Result(len(gone) == len(paths), f"Moved {said} to the Recycle Bin. You can restore it from there.")


def double_null(paths):
    """SHFileOperationW wants the paths separated by one null and ended by two."""
    return "\0".join(paths) + "\0\0"


# ---------- Windows calls (replaced in tests) ----------

DRIVE_FIXED = 3


def _start(path):
    os.startfile(path)


def _known_folder_path(guid):
    if os.name != "nt":
        return None
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_byte * 16)]

    folder_id = GUID()
    ctypes.memmove(folder_id.data, uuid.UUID(guid).bytes_le, 16)
    shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    path = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path)) != 0:
        return None
    try:
        return path.value
    finally:
        ole32.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))


def _fixed_drives():
    """Letters of the fixed (built-in) drives."""
    if os.name != "nt":
        return []
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    letters = [chr(65 + i) for i in range(26) if mask >> i & 1]
    return [d for d in letters if _drive_type(f"{d}:\\") == DRIVE_FIXED]


def _is_system_drive(letter):
    return f"{letter}:".lower() == os.environ.get("SystemDrive", "C:").lower()


def _drive_type(root):
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def _recycle(paths):
    """Send paths to the Recycle Bin with Windows' own undoable delete. True if Windows says done."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x0004, 0x0010, 0x0040, 0x0400
    FOF_WANTNUKEWARNING = 0x4000  # if something can't be recycled after all, Windows asks you first
    buffer = ctypes.create_unicode_buffer(double_null(paths))
    op = SHFILEOPSTRUCTW(hwnd=None, wFunc=FO_DELETE, pFrom=ctypes.cast(buffer, ctypes.c_void_p), pTo=None,
                         fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING)
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    code = shell32.SHFileOperationW(ctypes.byref(op))
    return code == 0 and not op.fAnyOperationsAborted
