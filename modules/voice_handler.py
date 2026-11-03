import os
import tempfile
import time

from modules.logger import get_logger

# Heavy audio libraries are imported inside the functions that use them,
# so text mode starts fast and never loads them.

log = get_logger()
_offline_engine = None


def _pronounce(text):
    # gTTS reads "KOVA" letter by letter, so spell it the way it should sound
    for word in ("KOVA", "Kova", "kova"):
        text = text.replace(word, "Koh-vuh")
    return text


def _speak_online(text):
    import pygame
    from gtts import gTTS

    filename = os.path.join(tempfile.gettempdir(), "kova_voice.mp3")
    gTTS(text=text, lang="en").save(filename)

    pygame.mixer.init()
    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()

    while pygame.mixer.music.get_busy():
        time.sleep(0.1)

    pygame.mixer.music.unload()

    if os.path.exists(filename):
        os.remove(filename)


def _speak_offline(text):
    # pyttsx3 uses the voices built into Windows, so it works without internet
    global _offline_engine
    import pyttsx3

    if _offline_engine is None:
        _offline_engine = pyttsx3.init()
        _offline_engine.setProperty("rate", 175)

    _offline_engine.say(text)
    _offline_engine.runAndWait()


def speak(text):
    text = _pronounce(text)
    try:
        _speak_online(text)
    except Exception as e:
        log.warning("gTTS failed (%s), using offline voice", e)
        try:
            _speak_offline(text)
        except Exception as e:
            print("Error with speech output:", e)
            log.error("Offline voice failed: %s", e)


def listen_with_sounddevice(seconds=4, sample_rate=16000):
    import sounddevice as sd
    import speech_recognition as sr

    try:
        recording = sd.rec(
            int(seconds * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16"
        )
        sd.wait()

        audio_data = sr.AudioData(recording.tobytes(), sample_rate, 2)
        return audio_data

    except Exception as e:
        print("Microphone recording error:", e)
        log.error("Microphone recording error: %s", e)
        return None


# ---------- listen until the speaker stops ----------

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
NOISE_MULTIPLIER = 2.0   # speech must be this much louder than the room to start recording
KEEP_GOING = 0.7         # ...and this fraction of that to keep recording once started
MIN_THRESHOLD = 100      # never treat near-silence as speech
# Speech is detected only in this band. Room hum (fans, motors) sits below 1000 Hz,
# while the parts of speech that make words understandable sit around 1-4 kHz.
SPEECH_BAND = (1000, 4000)
HIGH_PASS_HZ = 200       # hum below this is removed before audio goes to Google
SKIP_CHUNKS = 2          # the first 200 ms after the mic opens often contain a click
ADAPT_RATE = 0.05        # how fast the room-noise estimate follows quiet moments

_noise = None            # current estimate of the room's background level
_fixed_threshold = None  # MIC_THRESHOLD from config.json, if set


def configure(config):
    """MIC_THRESHOLD in config.json turns off auto-calibration (see: python main.py --mic-test)."""
    global _fixed_threshold
    if config.get("MIC_THRESHOLD"):
        _fixed_threshold = float(config["MIC_THRESHOLD"])


def current_threshold():
    if _fixed_threshold is not None:
        return _fixed_threshold
    return max(MIN_THRESHOLD, (_noise or 0) * NOISE_MULTIPLIER)


def _level(chunk):
    """Loudness of a chunk in the speech band only, on the same scale as RMS.

    Measuring everything made a fan's hum look as loud as a voice. A constant
    DC offset falls outside the band, so it is ignored too.
    """
    import numpy as np
    spectrum = np.abs(np.fft.rfft(chunk.astype(np.float32))) ** 2
    freqs = np.fft.rfftfreq(len(chunk), 1 / SAMPLE_RATE)
    band = (freqs >= SPEECH_BAND[0]) & (freqs < SPEECH_BAND[1])
    return float(np.sqrt(2 * spectrum[band].sum()) / len(chunk))


def _remove_hum(samples):
    """Cut everything below HIGH_PASS_HZ, which is mostly fan or electrical hum."""
    import numpy as np
    spectrum = np.fft.rfft(samples.astype(np.float32))
    spectrum[np.fft.rfftfreq(len(samples), 1 / SAMPLE_RATE) < HIGH_PASS_HZ] = 0
    cleaned = np.fft.irfft(spectrum, n=len(samples))
    return np.clip(cleaned, -32768, 32767).astype(np.int16)


def _adapt(level):
    """Let the noise estimate drift toward quiet moments, so one bad calibration fixes itself."""
    global _noise
    _noise = level if _noise is None else (1 - ADAPT_RATE) * _noise + ADAPT_RATE * level


def collect_phrase(chunks, threshold, wait_seconds, max_seconds, silence_seconds=0.8, pre_roll=3,
                   on_quiet=None):
    """Pick one spoken phrase out of a stream of 100 ms audio chunks.

    Waits up to wait_seconds for speech to start, then records until
    silence_seconds of quiet or max_seconds in total. Keeps a few chunks from
    just before the speech so the first syllable isn't cut off.
    threshold is a number or a function returning the current one; on_quiet(level)
    is called for every quiet chunk while waiting, to learn the room's noise.
    Returns the list of chunks, or None if nobody spoke.
    """
    get_threshold = threshold if callable(threshold) else (lambda: threshold)

    # Count whole chunks; adding 0.1 repeatedly drifts (8 x 0.1 = 0.7999...)
    wait_chunks = round(wait_seconds / CHUNK_SECONDS)
    max_chunks = round(max_seconds / CHUNK_SECONDS)
    silence_chunks = round(silence_seconds / CHUNK_SECONDS)

    before, phrase = [], []
    waited = silent = 0

    for chunk in chunks:
        level = _level(chunk)
        # Hysteresis: starting needs the full threshold, but once you're talking
        # softer syllables (KEEP_GOING of it) still count, so sentences aren't cut short
        limit = get_threshold() * (KEEP_GOING if phrase else 1.0)
        loud = level > limit
        if not phrase:
            if loud:
                phrase = before + [chunk]
                continue
            if on_quiet:
                on_quiet(level)
            before = (before + [chunk])[-pre_roll:]
            waited += 1
            if waited >= wait_chunks:
                return None
        else:
            phrase.append(chunk)
            silent = 0 if loud else silent + 1
            if silent >= silence_chunks or len(phrase) >= max_chunks:
                break

    return phrase or None


def _mic_chunks(stream, size):
    while True:
        data, _ = stream.read(size)
        yield data[:, 0].copy()


def _open_chunks(stream, size):
    chunks = _mic_chunks(stream, size)
    for _ in range(SKIP_CHUNKS):
        next(chunks)
    return chunks


def _calibrate(chunks, samples=8):
    global _noise
    if _noise is None and _fixed_threshold is None:
        levels = sorted(_level(next(chunks)) for _ in range(samples))
        _noise = levels[len(levels) // 2]  # median ignores a single bump
        log.info("Mic calibrated: room noise %.0f, speech threshold %.0f", _noise, current_threshold())


def record_phrase(wait_seconds=5, max_seconds=10):
    import numpy as np
    import sounddevice as sd
    import speech_recognition as sr

    size = int(SAMPLE_RATE * CHUNK_SECONDS)
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=size) as stream:
            chunks = _open_chunks(stream, size)
            _calibrate(chunks)
            adapt = None if _fixed_threshold is not None else _adapt
            phrase = collect_phrase(chunks, current_threshold, wait_seconds, max_seconds, on_quiet=adapt)
    except Exception as e:
        print("Microphone recording error:", e)
        log.error("Microphone recording error: %s", e)
        return None

    if not phrase:
        return None
    log.info("Recorded %.1fs (threshold %.0f)", len(phrase) * CHUNK_SECONDS, current_threshold())
    return sr.AudioData(_remove_hum(np.concatenate(phrase)).tobytes(), SAMPLE_RATE, 2)


def mic_test(seconds=15):
    """Show live mic levels so the threshold can be checked or set by hand."""
    import sounddevice as sd

    size = int(SAMPLE_RATE * CHUNK_SECONDS)
    print(f"Mic test for {seconds} seconds: stay quiet for 3 seconds, then talk normally.\n")
    quiet, speech = [], []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=size) as stream:
        chunks = _open_chunks(stream, size)
        for i in range(round(seconds / CHUNK_SECONDS)):
            level = _level(next(chunks))
            (quiet if i < 30 else speech).append(level)
            bar = "#" * min(60, int(level / 100))
            print(f"\r{'QUIET' if i < 30 else 'TALK '} {level:6.0f} |{bar:<60}|", end="", flush=True)

    room = sorted(quiet)[len(quiet) // 2]
    loud = sorted(speech)[int(len(speech) * 0.9)] if speech else 0
    suggested = max(MIN_THRESHOLD, room * NOISE_MULTIPLIER)
    print(f"\n\nRoom noise: {room:.0f}   Your speech (loud parts): {loud:.0f}")
    print(f"Auto threshold would be: {suggested:.0f}")
    if loud and loud < suggested * 1.2:
        middle = (room + loud) / 2
        print(f"Your voice is close to the room noise. Try setting \"MIC_THRESHOLD\": {middle:.0f} in config.json,")
        print("or move closer to the mic / raise its volume in Windows sound settings.")
    else:
        print("Looks good: your voice is clearly louder than the room.")


def recognize(audio):
    """Turn recorded audio into text. Returns None if nothing was understood."""
    import speech_recognition as sr

    if audio is None:
        return None

    try:
        return sr.Recognizer().recognize_google(audio)
    except sr.UnknownValueError:
        return None
    except sr.RequestError:
        print("⚠️ Could not connect to recognition service.")
        log.warning("Speech recognition service unreachable")
        return None


def listen_text(wait_seconds=5, max_seconds=10):
    """Wait for speech, record until the speaker stops, and recognize it.
    Returns None if nobody spoke or nothing was understood."""
    return recognize(record_phrase(wait_seconds, max_seconds))
