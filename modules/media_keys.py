import ctypes

from modules.result import Result

# Windows media keys: the same as pressing the volume/play keys on a keyboard.
# They work with YouTube in the browser, Spotify, VLC and most players.
KEYS = {
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
    "next": 0xB0,
    "previous": 0xB1,
    "play_pause": 0xB3,
}
KEYEVENTF_EXTENDEDKEY = 0x1
KEYEVENTF_KEYUP = 0x2
VOLUME_STEPS = 5  # each press is 2%, so one command changes the volume by 10%


def press(key, times=1):
    vk = KEYS[key]
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


def volume_up():
    press("volume_up", VOLUME_STEPS)
    return Result(True, "Volume up.")


def volume_down():
    press("volume_down", VOLUME_STEPS)
    return Result(True, "Volume down.")


def mute():
    press("mute")
    return Result(True, "Toggled mute.")


def play_pause():
    press("play_pause")
    return Result(True, "Done.")


def next_track():
    press("next")
    return Result(True, "Next track.")


def previous_track():
    press("previous")
    return Result(True, "Previous track.")
