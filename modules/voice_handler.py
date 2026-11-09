import os
import re
import tempfile
import time

from modules.logger import get_logger

# Heavy audio libraries are imported inside the functions that use them,
# so text mode starts fast and never loads them.

log = get_logger()

# Max speaks with a male or a female voice (VOICE in config.json, or "use a male voice").
# Natural voices come from Microsoft Edge's read-aloud service (edge-tts, needs internet);
# when it fails, female falls back to Google's voice and male to the Windows voice David.
VOICES = {"male": "en-US-GuyNeural", "female": "en-US-JennyNeural"}
OFFLINE_VOICES = {"male": "david", "female": "zira"}
EDGE_RETRY_SECONDS = 120  # after a failure, use the fallback this long instead of waiting again
_gender = "female"
_edge_down_until = 0.0


def set_voice(gender):
    global _gender
    if gender not in VOICES:
        raise ValueError(f"unknown voice {gender!r}")
    _gender = gender


def current_voice():
    return _gender


def _pronounce(text):
    # gTTS reads "KOVA" letter by letter, so spell it the way it should sound
    for word in ("KOVA", "Kova", "kova"):
        text = text.replace(word, "Koh-vuh")
    return text


EDGE_HOST = "speech.platform.bing.com"
EDGE_MAX_RETRY_SECONDS = 30 * 60
DNS_WAIT_SECONDS = 1.5
_edge_failures = 0


def _host_resolves(host, wait_seconds=DNS_WAIT_SECONDS):
    """True if the name resolves within wait_seconds. A broken DNS server can take 10 s to say no,
    which Max would otherwise spend silent before every line."""
    import socket
    import threading
    found = []
    def look_up():
        try:
            found.append(bool(socket.getaddrinfo(host, 443)))
        except OSError:
            found.append(False)
    worker = threading.Thread(target=look_up, daemon=True)
    worker.start()
    worker.join(wait_seconds)
    return bool(found and found[0])


def _edge_failed(reason):
    """Use the fallback for a while; longer each time it fails again (2, 4, 8... up to 30 minutes)."""
    global _edge_down_until, _edge_failures
    _edge_failures += 1
    wait = min(EDGE_RETRY_SECONDS * 2 ** (_edge_failures - 1), EDGE_MAX_RETRY_SECONDS)
    _edge_down_until = time.time() + wait
    log.warning("Natural voice failed (%s), using the fallback for %ss", reason, wait)


def _edge_mp3(text, filename):
    """Save text as speech with the natural voice. Returns False if the service failed."""
    global _edge_failures
    if time.time() < _edge_down_until:
        return False
    if not _host_resolves(EDGE_HOST):
        _edge_failed("no DNS answer")
        return False
    try:
        import asyncio
        import edge_tts
        speech = edge_tts.Communicate(text, VOICES[_gender], connect_timeout=5, receive_timeout=6)
        asyncio.run(speech.save(filename))
        _edge_failures = 0
        return True
    except Exception as e:
        _edge_failed(type(e).__name__)
        return False


def _speak_online(text):
    import pygame

    # One file per running Max, so two copies never fight over it
    filename = os.path.join(tempfile.gettempdir(), f"kova_voice_{os.getpid()}.mp3")
    if not _edge_mp3(text, filename):
        if _gender != "female":
            raise RuntimeError("no online male voice")  # speak() falls back to David
        from gtts import gTTS
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
    # pyttsx3 uses the voices built into Windows, so it works without internet.
    # A fresh engine every time: a reused one went silent from the second line on
    import pyttsx3

    engine = pyttsx3.init()
    engine.setProperty("rate", 175)
    _choose_offline_voice(engine)
    engine.say(text)
    engine.runAndWait()
    engine.stop()
    del engine


_speaking = False


def is_speaking():
    """True while a line is being said out loud (the window shows Max speaking)."""
    return _speaking


def speak(text):
    global _speaking
    _speaking = True
    try:
        _say(text)
    finally:
        _speaking = False


def _say(text):
    text = _pronounce(text)
    try:
        _speak_online(text)
    except Exception as e:
        # The male voice while edge is down is the expected fallback, not news on every line
        (log.debug if str(e) == "no online male voice" else log.warning)(
            "Online voice failed (%s), using the Windows voice", e)
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
        if "-9983" in str(e) or "-9988" in str(e):
            # 7 Nov log: the stream is closed under a listen when Max stops; nothing went wrong
            log.info("Microphone closed while listening: %s", e)
            return None
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
PAD_SECONDS = 0.3        # silence added around each clip: Google often misses a short lone word
SKIP_CHUNKS = 2          # the first 200 ms after the mic opens often contain a click
ADAPT_RATE = 0.05        # how fast the room-noise estimate follows quiet moments

_noise = None            # current estimate of the room's background level
_fixed_threshold = None  # MIC_THRESHOLD from config.json, if set
_language = "en-US"      # the language that heard you best last time
# SPEECH_LANGUAGES in config.json, e.g. ["en-US", "en-IN", "ur-PK"]; Pakistani English is often
# heard better as en-IN, and ur-PK gives Urdu text (that goes to the brain, not the commands)
_languages = ["en-US", "en-IN"]
STICKY = 0.05
# How sure Google was of the last thing heard (0-1; 0.5 when it didn't say). Below UNSURE Max asks
# you to say it again instead of acting on a guess (voice test: "search and ud" played a random video).
# SPEECH_MIN_CONFIDENCE in config.json changes it; 0 turns it off.
last_confidence = 1.0
UNSURE = 0.35
# A command that ends on one of these was cut off by a pause ("tell me the difference between")
CUT_OFF = re.compile(r"\b(between|and|or|the|a|an|of|to|in|on|with|for|about|from|into|except|than|my|your|"
                     r"is|are|that|which|want|you)$", re.IGNORECASE)


def _choose_offline_voice(engine):
    wanted = OFFLINE_VOICES[_gender]
    for voice in engine.getProperty("voices"):
        if wanted in voice.name.lower():
            engine.setProperty("voice", voice.id)
            return


def configure(config):
    """MIC_THRESHOLD in config.json turns off auto-calibration (see: python main.py --mic-test)."""
    global _fixed_threshold, _language, UNSURE
    if config.get("SPEECH_MIN_CONFIDENCE") is not None:
        UNSURE = float(config["SPEECH_MIN_CONFIDENCE"])
    if config.get("MIC_THRESHOLD"):
        _fixed_threshold = float(config["MIC_THRESHOLD"])
    # Google listens for one language per request, so each of these gets the same recording and
    # the most confident wins (see recognize). Commands are matched in English.
    set_languages(config.get("SPEECH_LANGUAGES") or [config.get("SPEECH_LANGUAGE") or "en-US", "en-IN"])
    for gender in VOICES:
        VOICES[gender] = config.get(f"VOICE_{gender.upper()}") or VOICES[gender]
    if config.get("VOICE") in VOICES:
        set_voice(config["VOICE"])


def set_languages(languages):
    """The languages to listen in, the first one used first. At most 3: each costs a request per phrase."""
    global _languages, _language
    _languages = list(dict.fromkeys(str(lang) for lang in languages if lang))[:3] or ["en-US"]
    _language = _languages[0]


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
        if "-9983" in str(e) or "-9988" in str(e):
            # 7 Nov log: the stream is closed under a listen when Max stops; nothing went wrong
            log.info("Microphone closed while listening: %s", e)
            return None
        print("Microphone recording error:", e)
        log.error("Microphone recording error: %s", e)
        return None

    if not phrase:
        return None
    log.info("Recorded %.1fs (threshold %.0f)", len(phrase) * CHUNK_SECONDS, current_threshold())
    pad = np.zeros(int(SAMPLE_RATE * PAD_SECONDS), dtype=np.int16)
    samples = np.concatenate([pad, _remove_hum(np.concatenate(phrase)), pad])
    return sr.AudioData(samples.tobytes(), SAMPLE_RATE, 2)


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


def pick_language(results, current):
    """The (language, text) to use from {language: (text, confidence)}. The language that won last
    time gets STICKY extra, so Max only switches when another one clearly heard you better."""
    global last_confidence
    # No score from Google counts as middling when comparing languages
    scored = [((0.5 if confidence is None else confidence) + (STICKY if language == current else 0), language, text)
              for language, (text, confidence) in results.items() if text]
    if not scored:
        return None, None
    _, language, text = max(scored)
    last_confidence = results[language][1]
    return language, text


def unsure():
    """True when Google itself wasn't sure of the last thing heard. No score from Google is not
    "unsure": nothing is known, so Max acts as before."""
    return last_confidence is not None and last_confidence < UNSURE


def sure_text():
    """For the log: '0.83', or '?' when Google gave no score (the 7 Nov log showed 0.50 for every
    phrase, which was this default, not a real score)."""
    return "?" if last_confidence is None else f"{last_confidence:.2f}"


def finish_cut_off(text, listen):
    """'tell me the difference between' + a pause: listen a little longer and add the rest."""
    if not text or not CUT_OFF.search(text.strip().rstrip(".,!?")):
        return text
    print("… (listening for the rest)")
    rest = listen(wait_seconds=3, max_seconds=8)
    return f"{text.strip()} {rest}" if rest else text


def _recognize_in(audio, language):
    """(text, confidence) from Google in one language, or (None, 0). Raises RequestError when offline."""
    import speech_recognition as sr
    try:
        result = sr.Recognizer().recognize_google(audio, language=language, show_all=True)
    except sr.UnknownValueError:  # nothing understood (raised even with show_all in 3.17)
        return None, 0.0
    if not isinstance(result, dict) or not result.get("alternative"):
        return None, 0.0
    best = result["alternative"][0]
    # Google gives a confidence for its best guess only, and often none at all: None = unknown
    confidence = best.get("confidence")
    return best.get("transcript"), None if confidence is None else float(confidence)


def recognize(audio):
    """Turn recorded audio into text. Returns None if nothing was understood.

    Every language in SPEECH_LANGUAGES listens to the same recording at once (one request each,
    side by side, so it is no slower), and the most confident wins. Max switches to the language
    that heard you best and keeps it until another one clearly does better.
    """
    import speech_recognition as sr
    from concurrent.futures import ThreadPoolExecutor
    global _language

    if audio is None:
        return None

    results, offline = {}, 0
    with ThreadPoolExecutor(max_workers=len(_languages)) as pool:
        futures = {language: pool.submit(_recognize_in, audio, language) for language in _languages}
        for language, future in futures.items():
            try:
                results[language] = future.result()
            except sr.RequestError:
                offline += 1
            except Exception as e:
                log.warning("Speech recognition in %s failed: %s %s", language, type(e).__name__, e)
    if offline == len(_languages):
        print("⚠️ Could not connect to recognition service.")
        log.warning("Speech recognition service unreachable")
        return None

    language, text = pick_language(results, _language)
    if language and language != _language:
        log.info("Speech language %s -> %s (heard %r)", _language, language, text)
        _language = language
    return text


def listen_text(wait_seconds=5, max_seconds=10):
    """Wait for speech, record until the speaker stops, and recognize it.
    Returns None if nobody spoke or nothing was understood."""
    return recognize(record_phrase(wait_seconds, max_seconds))
