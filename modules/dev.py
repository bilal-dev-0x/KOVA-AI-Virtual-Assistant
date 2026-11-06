import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from modules.fuzzy import best_match
from modules.logger import get_logger
from modules.result import Result
from modules.tool_registry import BASE_DIR

# Developer companion: your projects, VS Code, tests and git.
#   - A project is a folder with .git in PROJECT_ROOTS (default D:\Coding Arc, two levels down).
#     It is called by its GitHub name when it has one: "kova" finds the KOVA-AI-Virtual-Assistant
#     repo even though its folder has an older name.
#   - Every command is fixed (git, python -m pytest); the AI never writes one.
#   - Commit only after you say yes to the message and the files; push asks again on its own,
#     because it sends your code to GitHub.

log = get_logger()

DEFAULT_ROOTS = [r"D:\Coding Arc"]
TEST_SECONDS = 300
GIT_SECONDS = 30
# Never committed, whatever .gitignore says
SECRET_FILES = re.compile(r"(^|/)(config\.json|permissions\.json|\.env(\..*)?|.*\.key|.*\.pem|id_rsa.*|"
                          r"credentials.*\.json|.*secret.*)$", re.IGNORECASE)

_config = {"roots": DEFAULT_ROOTS}
_last = {"project": None}


@dataclass
class Project:
    name: str      # how it is said: the GitHub name, "KOVA AI Virtual Assistant"
    path: str
    remote: str = ""


def configure(config):
    _config["roots"] = (config or {}).get("PROJECT_ROOTS") or DEFAULT_ROOTS


def _spoken(repo):
    """'KOVA-AI-Virtual-Assistant' -> 'KOVA AI Virtual Assistant'."""
    return " ".join(re.sub(r"[-_.]+", " ", repo).split())


def _remote_name(path):
    try:
        text = (Path(path) / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r'url\s*=\s*\S*?([^/\s]+?)(\.git)?\s*$', text, re.MULTILINE)
    return match[1] if match else ""


def projects():
    """Every git project in the roots and one level below, plus Max's own."""
    found = {}
    folders = [BASE_DIR]
    for root in _config["roots"]:
        try:
            for entry in os.scandir(root):
                if entry.is_dir():
                    folders.append(entry.path)
                    try:
                        folders += [e.path for e in os.scandir(entry.path) if e.is_dir()]
                    except OSError:
                        pass
        except OSError:
            continue
    for folder in folders:
        if os.path.isdir(os.path.join(folder, ".git")):
            key = os.path.normcase(os.path.realpath(folder))
            remote = _remote_name(folder)
            found.setdefault(key, Project(_spoken(remote or Path(folder).name), folder, remote))
    return sorted(found.values(), key=lambda p: p.name.lower())


def _plain(text):
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def find_project(name=""):
    """The project name means ("kova", "visora", "my kova project"), the last one used when name
    is empty or "it", else None."""
    name = re.sub(r"^(my|the)\s+|\s+(project|repo|repository|code)$", "", (name or "").strip(), flags=re.IGNORECASE)
    if name.lower() in ("", "it", "this", "that", "this project"):
        return _last["project"] or next((p for p in projects() if _same(p.path, BASE_DIR)), None)
    wanted = _plain(name)
    choices = {}
    for project in projects():
        for said in (project.name, Path(project.path).name):
            choices[_plain(said)] = project
    # "kovas tests" (said for "KOVA's tests") means kova too
    for asked in dict.fromkeys([wanted, re.sub(r"s\b", "", wanted)]):
        for said, project in choices.items():
            if said == asked or said.split()[:len(asked.split())] == asked.split():
                return project
    match = best_match(wanted, choices.keys(), cutoff=0.7)
    return choices[match] if match else None


def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _project_or_problem(name):
    project = find_project(name)
    if project is None:
        known = ", ".join(p.name for p in projects()[:6]) or "none"
        return None, Result(False, f"I can't find a project called {name}. Your projects: {known}.")
    _last["project"] = project
    return project, None


def list_report():
    found = projects()
    if not found:
        return Result(True, "I couldn't find any git projects.")
    names = [p.name for p in found]
    return Result(True, f"You have {len(names)} projects: " + ", ".join(names[:-1]) + " and " + names[-1] + ".",
                  detail="\n".join(f"  {p.name:<36} {p.path}" for p in found))


# ---------- VS Code and a summary ----------

def open_in_vscode(name):
    project, problem = _project_or_problem(name)
    if problem:
        return problem
    code = _vscode()
    if code is None:
        return Result(False, "I can't find VS Code on this PC.")
    _popen([code, project.path])
    log.info("Opened %s in VS Code", project.path)
    return Result(True, f"Opening {project.name} in VS Code.")


def summary(name):
    project, problem = _project_or_problem(name)
    if problem:
        return problem
    counts, total = {}, 0
    for folder, dirs, names in os.walk(project.path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv",
                                                                         ".venv", "logs", "data")]
        for n in names:
            ext = Path(n).suffix.lower()
            if ext in (".py", ".js", ".ts", ".html", ".css", ".cpp", ".c", ".java", ".cs", ".asm", ".ipynb", ".sql"):
                counts[ext] = counts.get(ext, 0) + 1
                total += 1
        if total > 5000:
            break
    langs = ", ".join(f"{n} {ext.lstrip('.')}" for ext, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3])
    branch = _git(project.path, "rev-parse", "--abbrev-ref", "HEAD")[1].strip()
    last = _git(project.path, "log", "-1", "--format=%s (%cr)")[1].strip()
    changed = len([line for line in _git(project.path, "status", "--porcelain")[1].splitlines() if line.strip()])
    message = (f"{project.name}: {total} code files ({langs or 'none'}), on branch {branch or 'unknown'}. "
               f"Last commit: {last or 'none'}. " + (f"{changed} files changed since." if changed else "Nothing changed since."))
    return Result(True, message, detail=f"  {project.path}")


# ---------- tests ----------

def run_tests(name):
    project, problem = _project_or_problem(name)
    if problem:
        return problem
    root = Path(project.path)
    if not ((root / "pytest.ini").exists() or (root / "tests").is_dir() or (root / "pyproject.toml").exists()):
        return Result(False, f"{project.name} has no tests I know how to run (I run Python tests with pytest).")
    python = _python_for(root)
    print(f"🧪 Running {project.name}'s tests, this can take a minute...")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    code, out = _run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header"], root, TEST_SECONDS, env)
    if code is None:
        return Result(False, f"{project.name}'s tests took longer than {TEST_SECONDS // 60} minutes, so I stopped them.")
    summary_line = next((line for line in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", line)), "")
    failed = re.findall(r"^FAILED (\S+)", out, re.MULTILINE)
    counts = dict((k, int(v)) for v, k in re.findall(r"(\d+) (passed|failed|errors?|skipped)", summary_line))
    passed, broken = counts.get("passed", 0), counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    log.info("Tests of %s: %s", project.name, summary_line.strip("= "))
    if not counts:
        return Result(False, f"The tests of {project.name} didn't run.", detail=out[-1500:])
    if not broken:
        return Result(True, f"All {passed} tests of {project.name} pass.")
    names = [f.split("::")[-1].replace("_", " ") for f in failed[:3]]
    message = f"{broken} of {passed + broken} tests of {project.name} failed" + (": " + "; ".join(names) if names else "")
    return Result(False, message + ".", detail="\n".join(failed[:20]) + "\n\n" + out[-2500:])


def _python_for(root):
    """The project's own virtual environment if it has one, else the Python Max runs on."""
    for venv in (".venv", "venv", "env"):
        candidate = root / venv / "Scripts" / "python.exe"
        if candidate.exists():
            return str(candidate)
    return sys.executable


# ---------- git ----------

def git_status(name):
    project, problem = _project_or_problem(name)
    if problem:
        return problem
    code, out = _git(project.path, "status", "--porcelain", "--branch")
    if code != 0:
        return Result(False, f"git couldn't read {project.name}.", detail=out)
    lines = out.splitlines()
    branch = lines[0][3:] if lines and lines[0].startswith("##") else ""
    files = [line[3:] for line in lines[1:] if line.strip()]
    ahead = re.search(r"ahead (\d+)", branch)
    behind = re.search(r"behind (\d+)", branch)
    parts = [f"{len(files)} file{'s' if len(files) != 1 else ''} changed" if files else "nothing changed"]
    if ahead:
        parts.append(f"{ahead[1]} commit{'s' if ahead[1] != '1' else ''} not pushed")
    if behind:
        parts.append(f"{behind[1]} behind GitHub")
    shown = ", ".join(Path(f.split(" -> ")[-1].strip('"')).name for f in files[:5])
    message = f"{project.name}: " + ", ".join(parts) + (f": {shown}" + (" and more" if len(files) > 5 else "") if files else "") + "."
    return Result(True, message, detail="\n".join(lines))


def git_changes(name):
    """What changed, for the brain to explain or to draft a commit message from."""
    project, problem = _project_or_problem(name)
    if problem:
        return problem
    stat = _git(project.path, "diff", "--stat", "HEAD")[1].strip()
    new = [line[3:] for line in _git(project.path, "status", "--porcelain")[1].splitlines() if line.startswith("??")]
    if not stat and not new:
        return Result(True, f"Nothing changed in {project.name} since the last commit.")
    diff = _git(project.path, "diff", "HEAD", "--unified=1")[1]
    detail = stat + ("\nNew files: " + ", ".join(new) if new else "") + "\n\n" + diff[:6000]
    last = stat.splitlines()[-1].strip() if stat else f"{len(new)} new files"
    return Result(True, f"Changes in {project.name}: {last}" + (f", and {len(new)} new files" if stat and new else "") + ".",
                  detail=detail)


def commit_check(name, message):
    project = find_project(name)
    if project is None:
        return Result(False, f"I can't find a project called {name}.")
    if len((message or "").strip()) < 5:
        return Result(False, "A commit needs a message that says what changed.")
    files = [line[3:].strip('"') for line in _git(project.path, "status", "--porcelain")[1].splitlines() if line.strip()]
    if not files:
        return Result(False, f"There is nothing to commit in {project.name}.")
    secret = next((f for f in files if SECRET_FILES.search(f.replace("\\", "/"))), None)
    if secret:
        return Result(False, f"{secret} looks like a file with keys or passwords, so I won't commit. "
                             "Add it to .gitignore first.")
    return None


def commit(name, message):
    problem = commit_check(name, message)
    if problem:
        return problem
    project, _ = _project_or_problem(name)
    _git(project.path, "add", "-A")
    code, out = _git(project.path, "commit", "-q", "-m", message.strip())
    if code != 0:
        return Result(False, f"git didn't commit {project.name}.", detail=out[-1500:])
    short = _git(project.path, "rev-parse", "--short", "HEAD")[1].strip()
    log.info("Committed %s in %s", short, project.path)
    return Result(True, f"Committed {project.name} as {short}. Say push when you want it on GitHub.")


def push(name):
    project, problem = _project_or_problem(name)
    if problem:
        return problem
    code, out = _git(project.path, "push", timeout=120)
    if code != 0:
        return Result(False, f"The push of {project.name} failed.", detail=out[-1500:])
    log.info("Pushed %s", project.path)
    return Result(True, f"Pushed {project.name} to GitHub.")


# ---------- running programs (replaced in tests) ----------

def _vscode():
    for path in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
                 Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft VS Code" / "Code.exe"):
        if path.exists():
            return str(path)
    return None


def _popen(args):
    subprocess.Popen(args)


def _run(args, cwd, timeout, env=None):
    """(exit code, output), or (None, output so far) after timeout. Fixed arguments, no shell."""
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        return None, (e.stdout or "") if isinstance(e.stdout, str) else ""
    # Warnings ("LF will be replaced by CRLF") go to stderr: only a failure needs them
    if done.returncode == 0:
        return 0, done.stdout or ""
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _git(path, *args, timeout=GIT_SECONDS):
    code, out = _run(["git", *args], path, timeout)
    return (code if code is not None else 1), out
