from modules.voice_handler import speak
from modules.voice_handler import listen_with_sounddevice

speak("Hello! KOVA voice module is working.")

print("Recording started...")

audio = listen_with_sounddevice()

if audio:
    print("Recording successful!")
    print("Audio data received.")
else:
    print("Recording failed!")