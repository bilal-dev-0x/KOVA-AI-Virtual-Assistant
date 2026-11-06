import io
import re
import threading
import time

from brain.base import BrainError
from modules import windows
from modules.logger import get_logger
from modules.result import Result

# Max looks at the screen only when you ask: one screenshot of the window in front (not the
# terminal Max runs in), a named app's window, or the whole screen. It is shrunk to a JPEG in
# memory, never written to disk (the relay brain's copy is deleted after its answer), and
# kept about a minute so "and how do I fix it?" asks about the same picture.

log = get_logger()

MAX_WIDTH = 1280
JPEG_QUALITY = 80
KEEP_SECONDS = 60
# Windows whose title or program has one of these words are private: no screenshot unless
# you say "look anyway". SCREEN_BLOCKLIST in config.json replaces this list.
DEFAULT_BLOCKLIST = ["password", "passwords", "1password", "bitwarden", "lastpass", "keepass", "dashlane",
                     "keeper", "authenticator", "bank", "banking", "hbl", "meezan", "ubl", "mcb", "alfalah",
                     "allied bank", "askari", "jazzcash", "easypaisa", "sadapay", "nayapay", "paypal",
                     "whatsapp", "telegram", "signal", "inprivate", "incognito"]
NO_BRAIN = "Looking at the screen needs Gemini, and it isn't set up."

_last = {"image": None, "label": "", "time": 0.0, "qa": []}  # the picture kept for follow-ups
_blocked = {"request": None, "time": 0.0}                     # what "look anyway" would look at
_forget_timer = None
_lock = threading.Lock()


# ---------- picking and checking ----------

def blocked_word(texts, blocklist):
    """The blocklist word found in any of the texts, as a whole word, or None. The longest wins,
    so "Meezan Bank" is named as meezan, not just bank."""
    for text in texts:
        low = (text or "").lower()
        for word in sorted(blocklist, key=len, reverse=True):
            if re.search(rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])", low):
                return word
    return None


def blocklist(config):
    words = (config or {}).get("SCREEN_BLOCKLIST")
    return [str(w) for w in words] if isinstance(words, list) else DEFAULT_BLOCKLIST


class Private(Exception):
    """A private window would end up in the screenshot; args[0] is the blocklist word."""


class OffScreen(Exception):
    """The window isn't on any screen, so its screen pixels would be a blank picture."""


def _shown_texts(inside=None, skip=()):
    """'program title' of every window on screen that isn't minimised; with inside=(l, t, r, b),
    only those overlapping that rectangle."""
    texts = []
    for hwnd, process, title in windows.visible_windows():
        if hwnd in skip or windows._is_minimised(hwnd):
            continue
        if inside is not None:
            x, y, width, height = windows._get_rect(hwnd)
            left, top, right, bottom = inside
            if x >= right or y >= bottom or x + width <= left or y + height <= top:
                continue
        texts.append(f"{process} {title}")
    return texts


def _pick(what, app):
    """(what to grab, label, texts to check against the blocklist), or a Result. What to grab is
    a window handle, ("box", (left, top, right, bottom)) for one monitor, or ("all", None)."""
    if what == "all screens":
        return ("all", None), "all your screens", _shown_texts()
    if what == "screen":
        # The monitor you're working on: the one with the window in front. Two monitors in one
        # 1280 px picture would make the text too small to read.
        front = windows.target_window()
        box = windows._monitor_rect(front[0]) if front else windows._monitor_rect(None)
        return ("box", box), "your screen", _shown_texts(inside=box)
    if app:
        hwnd, name, title = windows.find(app)
        if hwnd is None:
            return title  # the Result explaining why
        process = name
    else:
        found = windows.target_window()
        if found is None:
            return Result(False, "I can't see a window to look at.")
        hwnd, process, title = found
        name = process
    if windows._is_minimised(hwnd):
        return Result(False, f"The {name} window is minimised. Open it and ask me again.")
    from modules import system_control
    return hwnd, f"the {name} window, {system_control.spoken_title(title)}", [f"{process} {title}"]


# ---------- the picture ----------

def shrink(image):
    """A PIL image -> JPEG bytes at most MAX_WIDTH wide, made in memory."""
    image = image.convert("RGB")
    if image.width > MAX_WIDTH:
        image = image.resize((MAX_WIDTH, round(image.height * MAX_WIDTH / image.width)))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def recent(now=None):
    """True while the last picture can still be asked about."""
    now = time.time() if now is None else now
    return _last["image"] is not None and now - _last["time"] < KEEP_SECONDS


def forget():
    with _lock:
        _last.update(image=None, label="", time=0.0, qa=[])


def reset():
    """Forget the picture and stop its timer (tests, and Max shutting down)."""
    global _forget_timer
    with _lock:
        if _forget_timer is not None:
            _forget_timer.cancel()
            _forget_timer = None
    forget()
    _blocked.update(request=None, time=0.0)


def _keep(image, label):
    """Keep the picture for follow-ups; a sleeping timer drops it after KEEP_SECONDS (no polling)."""
    global _forget_timer
    with _lock:
        _last.update(image=image, label=label, time=time.time(), qa=[])
        if _forget_timer is not None:
            _forget_timer.cancel()
        _forget_timer = threading.Timer(KEEP_SECONDS, forget)
        _forget_timer.daemon = True
        _forget_timer.start()


# ---------- looking ----------

def look(question, what="window", app="", same_picture=False, provider=None, config=None, anyway=False):
    """Answer a question about the screen. One brain request; the screenshot never touches disk."""
    if provider is None or not getattr(provider, "can_look", False):
        return Result(False, NO_BRAIN)

    image = None
    if same_picture:
        # One snapshot under the lock: the timer may forget the picture at any moment
        with _lock:
            if _last["image"] is not None and time.time() - _last["time"] < KEEP_SECONDS:
                image, label, qa = _last["image"], _last["label"], list(_last["qa"])
        if image is not None:
            context = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa)
    if image is None:  # a first look, or the last picture is gone: take a new one
        picked = _pick(what, app)
        if isinstance(picked, Result):
            return picked
        target, label, texts = picked
        words = [] if anyway else blocklist(config)
        try:
            word = blocked_word(texts, words)
            if word:
                raise Private(word)
            if isinstance(target, tuple):
                kind, box = target
                image = shrink(_grab_screen(None if kind == "all" else box))
            else:
                image = shrink(_grab_window(target, words))
        except Private as e:
            _blocked.update(request={"question": question, "what": what, "app": app}, time=time.time())
            log.info("Screenshot refused: '%s' is on the private list", e.args[0])
            return Result(False, f"That looks private ({e.args[0]}), so I didn't look. "
                                 "Say look anyway if you want me to.")
        except OffScreen:
            return Result(False, "I can't see that window on screen. Bring it into view and ask me again.")
        except Exception as e:
            log.exception("Screenshot failed")
            return Result(False, "I couldn't take the screenshot.", detail=str(e))
        _keep(image, label)
        context = ""

    print(f"📸 Looking at {label}")
    log.info("Looking at %s (%s KB)", label, len(image) // 1024)
    try:
        answer = provider.look(image, question, context)
    except BrainError as e:
        return Result(False, str(e))
    if not answer:
        return Result(False, "I couldn't get an answer about the picture. Try asking another way.")
    with _lock:
        if _last["image"] is image:
            _last["qa"].append((question, answer))
    return Result(True, answer)


def look_anyway(provider=None, config=None):
    """Repeat the last refused look, once, within KEEP_SECONDS."""
    request = _blocked["request"]
    if request is None or time.time() - _blocked["time"] > KEEP_SECONDS:
        return Result(False, "There's nothing waiting for me to look at.")
    _blocked["request"] = None
    return look(**request, provider=provider, config=config, anyway=True)


# ---------- Windows screenshots (replaced in tests) ----------

def _grab_screen(box=None):
    """One monitor's pixels, box = (left, top, right, bottom) in screen coordinates; None = all monitors."""
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=box, all_screens=True)


def _grab_window(hwnd, words=()):
    """The window's own picture through PrintWindow, so a window lying on top of it (a chat,
    a password manager) is never in the shot. Falls back to the screen pixels if the app
    draws nothing that way (the picture comes back black). The screen pixels can include an
    always-on-top window, so then every window over that area is checked too (raises Private)."""
    image = _print_window(hwnd)
    if image is not None and image.getbbox() is not None:
        return image
    x, y, width, height = windows._get_rect(hwnd)
    box = (x, y, x + width, y + height)
    left, top, right, bottom = windows._monitor_rect(hwnd)  # the nearest screen
    if x >= right or y >= bottom or x + width <= left or y + height <= top:
        raise OffScreen()
    word = blocked_word(_shown_texts(inside=box, skip={hwnd} | windows._own_windows()), words)
    if word:
        raise Private(word)
    from modules import system_control
    system_control._bring_to_front(hwnd)
    time.sleep(0.3)  # let it draw after coming to the front
    image = _grab_screen(box)
    if image is None or image.getbbox() is None:
        raise OffScreen()  # blank: not worth a brain request
    return image


def _print_window(hwnd):
    import ctypes
    from ctypes import wintypes
    from PIL import Image

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    user32, gdi32 = _gdi()
    _, _, width, height = windows._get_rect(hwnd)
    if width <= 0 or height <= 0:
        return None
    screen_dc = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    old = gdi32.SelectObject(dc, bitmap)
    selected = True
    try:
        PW_RENDERFULLCONTENT = 2  # also draws Chrome and other GPU-drawn windows
        if not user32.PrintWindow(windows._h(hwnd), dc, PW_RENDERFULLCONTENT):
            return None
        # GetDIBits needs the bitmap out of the device context first
        gdi32.SelectObject(dc, old)
        selected = False
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,
                                  biPlanes=1, biBitCount=32, biCompression=0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(dc, bitmap, 0, height, ctypes.addressof(pixels), ctypes.addressof(header), 0):
            return None
        image = Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1)
        # Cut off the invisible frame (8 px around a maximised window), so only what you see is sent
        left, top, right, bottom = windows._frame_border(hwnd)
        return image.crop((left, top, width - right, height - bottom)) if right + left < width else image.copy()
    finally:
        if selected:
            gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen_dc)


_GDI = {}


def _gdi():
    """Private user32/gdi32 with pointer-size handles (the defaults would cut them to 32 bits)."""
    import ctypes
    from ctypes import wintypes
    if not _GDI:
        user32 = ctypes.WinDLL("user32")
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
        gdi32 = ctypes.WinDLL("gdi32")
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
        gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        _GDI.update(user32=user32, gdi32=gdi32)
    return _GDI["user32"], _GDI["gdi32"]
