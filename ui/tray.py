import threading

from modules.logger import get_logger

# The icon in the notification area: Show/Hide the dashboard, Mute the mic, Quit.
# pystray runs its own message-pump thread, so only the menu actions come back
# to the UI, through the callbacks given here. pystray is imported inside
# start(), so tests never show a real icon and Max still starts without it.

log = get_logger()


def icon_image(size=64):
    """The tray picture, drawn in code so no image file ships with KOVA."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (size, size), (18, 24, 38))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=int(size * 0.55))
    box = draw.textbbox((0, 0), "M", font=font)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "M", fill=(120, 200, 255), font=font)
    return image


class Tray:
    """The tray icon. is_visible/is_muted are read when the menu opens, so the
    labels always tell the truth about the window and the microphone."""

    def __init__(self, on_show, on_hide, on_mute, on_quit, is_visible, is_muted):
        self.on_show = on_show
        self.on_hide = on_hide
        self.on_mute = on_mute
        self.on_quit = on_quit
        self.is_visible = is_visible
        self.is_muted = is_muted
        self._icon = None
        self._thread = None

    @property
    def running(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    def toggle(self):
        (self.on_hide if self.is_visible() else self.on_show)()

    def _menu(self, backend):
        return backend.Menu(
            backend.MenuItem(lambda item: "Hide dashboard" if self.is_visible() else "Show dashboard",
                             self.toggle, default=True),
            backend.MenuItem(lambda item: "Unmute mic" if self.is_muted() else "Mute mic", self.on_mute),
            backend.MenuItem("Quit", self.on_quit),
        )

    def start(self, backend=None):
        """Show the icon. False when pystray isn't available: the dashboard
        still works, only without a tray icon."""
        if backend is None:
            try:
                import pystray
            except ImportError as e:
                log.warning("No tray icon: %s", e)
                return False
            backend = pystray
        try:
            icon = backend.Icon("kova_max", icon_image(), "KOVA Max", menu=self._menu(backend))
        except Exception:
            log.exception("Could not build the tray icon")
            return False
        self._icon = icon
        self._thread = threading.Thread(target=icon.run, name="kova-ui-tray", daemon=True)
        self._thread.start()
        log.info("Tray icon shown")
        return True

    def stop(self):
        icon, self._icon = self._icon, None
        self._thread = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                log.debug("Tray icon already gone")
