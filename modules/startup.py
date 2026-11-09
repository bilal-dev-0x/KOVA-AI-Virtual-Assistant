import os
import sys

from modules.logger import get_logger

# "Start with Windows": one shortcut in the user's own Startup folder. No admin
# rights, no registry, nothing outside the user's profile, and removing the
# shortcut undoes it completely. It is only ever written when the user turns
# the switch on in Settings.

log = get_logger()

SHORTCUT_NAME = "KOVA.lnk"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def startup_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def shortcut_path():
    folder = startup_dir()
    return os.path.join(folder, SHORTCUT_NAME) if folder else None


def is_enabled():
    path = shortcut_path()
    return bool(path) and os.path.isfile(path)


def pythonw():
    """The windowless Python next to the one running KOVA, so no console flashes at sign-in."""
    folder = os.path.dirname(sys.executable)
    candidate = os.path.join(folder, "pythonw.exe")
    return candidate if os.path.isfile(candidate) else sys.executable


def _write_shortcut(path, target, arguments, folder, icon):
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortCut(path)
    link.TargetPath = target
    link.Arguments = arguments
    link.WorkingDirectory = folder
    link.Description = "KOVA desktop assistant"
    if icon:
        link.IconLocation = icon
    link.save()


def enable(write=_write_shortcut):
    """Create the shortcut. Returns (ok, message)."""
    path = shortcut_path()
    if not path or not os.path.isdir(os.path.dirname(path)):
        return False, "The Startup folder was not found."
    main = os.path.join(PROJECT_DIR, "main.py")
    try:
        write(path, pythonw(), f'"{main}" --ui', PROJECT_DIR, None)
    except ImportError:
        return False, "Start with Windows needs pywin32 (pip install pywin32)."
    except Exception as e:
        log.warning("Startup shortcut could not be written: %s", e)
        return False, "The Startup shortcut could not be written."
    log.info("Start with Windows: on")
    return True, "KOVA will start when you sign in to Windows."


def disable():
    """Remove the shortcut KOVA made. Returns (ok, message)."""
    path = shortcut_path()
    if not path or not os.path.isfile(path):
        return True, "KOVA will not start with Windows."
    try:
        os.remove(path)
    except OSError as e:
        log.warning("Startup shortcut could not be removed: %s", e)
        return False, "The Startup shortcut could not be removed."
    log.info("Start with Windows: off")
    return True, "KOVA will not start with Windows."
