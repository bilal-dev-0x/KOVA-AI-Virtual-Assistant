import numpy as np

from modules.voice_handler import collect_phrase

QUIET = np.zeros(1600, dtype=np.int16)
# A 2 kHz tone: inside the speech band the level is measured in
LOUD = (2000 * np.sin(2 * np.pi * 2000 * np.arange(1600) / 16000)).astype(np.int16)


def chunks(pattern):
    """'..##..' -> quiet, quiet, loud, loud, quiet, quiet (100 ms each)"""
    return iter([LOUD if c == "#" else QUIET for c in pattern])


def test_nobody_speaks_returns_none():
    assert collect_phrase(chunks("." * 60), threshold=500, wait_seconds=3, max_seconds=10) is None


def test_records_until_silence():
    phrase = collect_phrase(chunks("...####" + "." * 20), threshold=500, wait_seconds=3, max_seconds=10)
    # 3 chunks of pre-roll + 4 speech + 8 silence (0.8 s) = 15
    assert len(phrase) == 15


def test_short_pause_inside_sentence_is_kept():
    phrase = collect_phrase(chunks("##...##" + "." * 20), threshold=500, wait_seconds=3, max_seconds=10)
    assert sum(1 for c in phrase if c is LOUD) == 4


def test_stops_at_max_length():
    phrase = collect_phrase(chunks("#" * 200), threshold=500, wait_seconds=3, max_seconds=2)
    assert len(phrase) == 20


def test_speech_starting_late_is_still_caught():
    phrase = collect_phrase(chunks("." * 25 + "###" + "." * 10), threshold=500, wait_seconds=3, max_seconds=10)
    assert phrase is not None


def test_dc_offset_is_not_noise():
    from modules.voice_handler import _level
    assert _level(np.full(1600, 800, dtype=np.int16)) < 1
    assert _level(LOUD) > 1000


def test_threshold_follows_quiet_room(monkeypatch):
    from modules import voice_handler as vh
    monkeypatch.setattr(vh, "_noise", 1000.0)   # a bad, too-high calibration
    monkeypatch.setattr(vh, "_fixed_threshold", None)
    start = vh.current_threshold()
    quiet = (50 * np.sin(np.arange(1600) / 3)).astype(np.int16)
    collect_phrase(iter([quiet] * 60), vh.current_threshold, wait_seconds=6, max_seconds=10, on_quiet=vh._adapt)
    assert vh.current_threshold() < start / 4


def test_fixed_threshold_from_config(monkeypatch):
    from modules import voice_handler as vh
    monkeypatch.setattr(vh, "_fixed_threshold", None)
    vh.configure({"MIC_THRESHOLD": 900})
    assert vh.current_threshold() == 900
    monkeypatch.setattr(vh, "_fixed_threshold", None)


def test_low_hum_is_not_speech():
    from modules.voice_handler import _level
    hum = (3000 * np.sin(2 * np.pi * 188 * np.arange(1600) / 16000)).astype(np.int16)
    assert _level(hum) < 100         # loud fan hum at 188 Hz barely registers (its RMS is 2100)
    assert _level(LOUD) > 1000       # a 2 kHz tone does


def test_remove_hum_keeps_speech_band():
    from modules.voice_handler import _remove_hum
    t = np.arange(16000) / 16000
    hum = 3000 * np.sin(2 * np.pi * 100 * t)
    voice = 1000 * np.sin(2 * np.pi * 1000 * t)
    cleaned = _remove_hum((hum + voice).astype(np.int16)).astype(np.float32)
    assert abs(cleaned.std() - voice.std()) < 50


def test_soft_syllables_keep_recording():
    medium = (LOUD * 0.6).astype(np.int16)  # ~850: below the start threshold, above 70% of it
    stream = iter([LOUD] * 3 + [medium] * 12 + [QUIET] * 20)
    phrase = collect_phrase(stream, threshold=1000, wait_seconds=3, max_seconds=10)
    # all 12 medium chunks are kept, then 8 quiet ones end the phrase
    assert len(phrase) == 3 + 12 + 8


def test_medium_level_does_not_start_recording():
    medium = (LOUD * 0.6).astype(np.int16)
    assert collect_phrase(iter([medium] * 40), threshold=1000, wait_seconds=3, max_seconds=10) is None


# ---------- listening in two languages at once ----------

import pytest  # noqa: E402
import speech_recognition as sr  # noqa: E402

from modules import voice_handler  # noqa: E402


@pytest.fixture
def heard(monkeypatch):
    """Fake Google: {language: (text, confidence)} or an exception for that language."""
    answers = {}

    def fake(audio, language):
        answer = answers.get(language, (None, 0.0))
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(voice_handler, "_recognize_in", fake)
    voice_handler.set_languages(["en-US", "en-IN"])
    yield answers
    voice_handler.set_languages(["en-US", "en-IN"])


def test_the_more_confident_language_wins_and_max_switches(heard):
    heard.update({"en-US": ("find mizoram", 0.62), "en-IN": ("find visora", 0.91)})
    assert voice_handler.recognize(object()) == "find visora"
    assert voice_handler._language == "en-IN"


def test_the_current_language_keeps_a_small_head_start(heard):
    voice_handler.set_languages(["en-IN", "en-US"])
    heard.update({"en-US": ("open chrome", 0.88), "en-IN": ("open crome", 0.85)})
    assert voice_handler.recognize(object()) == "open crome"   # 0.85 + 0.05 beats 0.88
    assert voice_handler._language == "en-IN"


def test_only_one_language_heard_anything(heard):
    heard.update({"en-IN": ("max", 0.7)})
    assert voice_handler.recognize(object()) == "max"


def test_nothing_heard_in_any_language(heard):
    assert voice_handler.recognize(object()) is None


def test_offline_in_one_language_still_uses_the_other(heard):
    heard.update({"en-US": sr.RequestError("dns"), "en-IN": ("open notepad", 0.8)})
    assert voice_handler.recognize(object()) == "open notepad"


def test_offline_in_every_language(heard, capsys):
    heard.update({"en-US": sr.RequestError("dns"), "en-IN": sr.RequestError("dns")})
    assert voice_handler.recognize(object()) is None
    assert "Could not connect" in capsys.readouterr().out


def test_languages_from_config():
    voice_handler.configure({"SPEECH_LANGUAGE": "en-US"})
    assert voice_handler._languages == ["en-US", "en-IN"]
    voice_handler.configure({"SPEECH_LANGUAGES": ["en-IN", "ur-PK", "en-US", "en-GB"]})
    assert voice_handler._languages == ["en-IN", "ur-PK", "en-US"]   # at most 3 requests per phrase
    voice_handler.set_languages(["en-US", "en-IN"])
