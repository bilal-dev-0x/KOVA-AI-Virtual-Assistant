import asyncio
import io
import re

from modules.logger import get_logger

# The text on a screenshot, read by the OCR built into Windows 10/11 (Windows.Media.Ocr): free,
# offline and quick. The KOVA server can't see pictures, so when Gemini can't look, Max reads
# the screen's text here and the server answers from that. Words only: no pictures or colours.
#
# Needs the winrt-Windows.Media.Ocr packages (see requirements.txt) and an OCR language, which
# Windows has for its display language.

SCALE = 2  # small screen text reads much better twice as big (still well under a second)
# Hidden before the text goes anywhere: 4-8 digit codes on a line about a code (not an "error
# code"), and card numbers
CODE_WORDS = re.compile(r"\b((?<!error )code|otp|pin|passcode|password|verification|verify|cvv|cvc|token|2fa)\b",
                        re.I)
SHORT_CODE = re.compile(r"(?<![\w.,:/-])\d(?:[ -]?\d){3,7}(?![\d.,:/-]?\d)")
NEAR_LINES = 1  # a 4-digit number counts as a code only this close to the word (2026 is a year)
CARD = re.compile(r"(?<!\d)\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}(?!\d)")

log = get_logger()


class OcrError(Exception):
    """Windows OCR isn't available here; message is safe to show."""


def available():
    try:
        _engine()
        return True
    except OcrError:
        return False


def read_text(image):
    """The text in a picture (JPEG/PNG bytes or a PIL image), one line per text line, top to bottom."""
    from PIL import Image
    if isinstance(image, (bytes, bytearray)):
        image = Image.open(io.BytesIO(image))
    image = image.convert("RGBA")
    engine, limit = _engine()
    image = image.resize((image.width * SCALE, image.height * SCALE), Image.LANCZOS)
    if max(image.size) > limit:
        ratio = limit / max(image.size)
        image = image.resize((int(image.width * ratio), int(image.height * ratio)))
    return asyncio.run(_recognize(engine, image))


def hide_secrets(text):
    """The text with login codes, PINs and card numbers replaced by dots, so they never leave
    this PC or get read out (live test: the server read out an OTP from the screen).
    OCR reads a window in columns, so "code is" and "482913" can land lines apart: once the text
    mentions a code anywhere, every 5-8 digit number is hidden, and 4-digit ones near that word."""
    text = CARD.sub("•••• •••• •••• ••••", text)
    lines = text.splitlines()
    about_codes = [i for i, line in enumerate(lines) if CODE_WORDS.search(line)]
    if not about_codes:
        return text

    def hide(match, near):
        digits = sum(c.isdigit() for c in match.group())
        return "•••••" if digits >= 5 or near else match.group()
    return "\n".join(SHORT_CODE.sub(lambda m: hide(m, any(abs(i - j) <= NEAR_LINES for j in about_codes)), line)
                     for i, line in enumerate(lines))


def _engine():
    """The OCR engine for the user's languages and the biggest picture side it takes."""
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError as e:
        raise OcrError("Reading the screen without Gemini needs the Windows OCR packages "
                       "(pip install -r requirements.txt).") from e
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrError("Windows has no OCR language installed.")
    return engine, OcrEngine.max_image_dimension


async def _recognize(engine, image):
    from PIL import Image
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    r, g, b, a = image.split()
    writer = DataWriter()
    writer.write_bytes(Image.merge("RGBA", (b, g, r, a)).tobytes())  # Windows wants BGRA
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                    image.width, image.height, BitmapAlphaMode.PREMULTIPLIED)
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines).strip()
