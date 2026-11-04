"""Manual microphone smoke test -- NOT an automated test.

Run it by hand:  python mic_smoke.py

It speaks one line and records a single clip, so it needs a real microphone.
The automated suite lives in tests/ (see pytest.ini) and never touches hardware.
For an interactive check of levels and the speech threshold use:
    python main.py --mic-test
"""

from modules.voice_handler import listen_with_sounddevice, speak

speak("Hello! KOVA voice module is working.")

print("Recording started...")

audio = listen_with_sounddevice()

if audio:
    print("Recording successful!")
    print("Audio data received.")
else:
    print("Recording failed!")
