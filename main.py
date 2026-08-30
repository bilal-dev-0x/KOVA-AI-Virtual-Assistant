import speech_recognition as sr
import webbrowser
import os
import subprocess
import time
import music_library
import requests
from google import genai
from datetime import datetime
from gtts import gTTS
import pygame
import json
import sounddevice as sd


USER_TITLE = "Commander"


# Load API keys from config.json
try:
    with open("config.json", "r") as f:
        config = json.load(f)
except FileNotFoundError:
    print("config.json not found. Copy config.json.example to config.json and add your API keys.")
    exit()


gemini_api_key = config.get("GEMINI_API_KEY")
gemini_model = config.get("GEMINI_MODEL", "gemini-3.6-flash")
news_api = config.get("NEWS_API_KEY", "")


if not gemini_api_key:
    print("Please set your Gemini API key in config.json before running KOVA.")
    exit()


client = genai.Client(api_key=gemini_api_key)
recognizer = sr.Recognizer()


def listen_with_sounddevice(seconds=4, sample_rate=16000):
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
        return None


def speak(text):
    try:
        filename = "kova_voice.mp3"
        tts = gTTS(text=text, lang="en")
        tts.save(filename)

        pygame.mixer.init()
        pygame.mixer.music.load(filename)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

        pygame.mixer.music.unload()

        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        print("Error with gTTS:", e)


def aiprocess(command):
    print("Thinking for best.....")

    chat = client.chats.create(model=gemini_model)

    prompt = f"""
You are KOVA, a helpful AI virtual assistant.
Give short responses.
Address the user naturally as '{USER_TITLE}'.

User command: {command}
"""

    response = chat.send_message(prompt)

    if response.text:
        return response.text.strip()

    return f"{USER_TITLE}, I could not generate a response right now."


def open_whatsapp():
    subprocess.run([
        "powershell",
        "(Get-StartApps -Name 'WhatsApp').AppID | ForEach-Object { Start-Process \"shell:AppsFolder\\$_\" }"
    ], shell=True)
    speak("Opening WhatsApp")

def main():
    speak("Initializing KOVA....")

    while True:
        try:
            chrome_path = "C:/Program Files/Google/Chrome/Application/chrome.exe %s"
            webpath = webbrowser.get(chrome_path)

            print("Listening for wake word... 🎤")
            audio = listen_with_sounddevice(seconds=3)

            if audio is None:
                continue

            command = recognizer.recognize_google(audio)
            print("You said:", command)

            if "kova" in command.lower():
                speak(f"Yes {USER_TITLE}?")

                print("Listening for command... 🎤")
                audio = listen_with_sounddevice(seconds=5)

                if audio is None:
                    continue

                text = recognizer.recognize_google(audio)
                print("Your command:", text)

                if "hello" in text.lower():
                    speak(f"Hi {USER_TITLE}, how are you?")

                elif "open youtube" in text.lower():
                    webpath.open("https://www.youtube.com/")

                elif "open google" in text.lower():
                    webpath.open("https://www.google.com/")

                elif "open spotify" in text.lower():
                    webpath.open("https://open.spotify.com/")

                elif text.lower().startswith("play"):
                    song = text.lower().replace("play", "").strip()

                    if song in music_library.library:
                        speak(f"Playing {song}")
                        webpath.open(music_library.library[song])
                    else:
                        speak(f"Sorry {USER_TITLE}, this song is not in your library.")

                elif "global news" in text.lower():
                    if not news_api:
                        speak("News API key is not configured.")
                    else:
                        r = requests.get(
                            f"https://newsapi.org/v2/top-headlines?country=pk&apiKey={news_api}"
                        )
                        data = r.json()

                        if data.get("status") == "ok":
                            articles = data.get("articles", [])
                            speak("Here are the top headlines.")

                            for i, article in enumerate(articles[:5], start=1):
                                headline = article.get("title")

                                if headline:
                                    print(f"Headline {i}: {headline}")
                                    speak(f"Headline {i}: {headline}")
                        else:
                            speak(f"Sorry {USER_TITLE}, I could not fetch the news at this moment.")

                elif "pakistani news" in text.lower():
                    speak("Pakistani news is not configured in this release.")

                elif "open whatsapp" in text.lower():
                    open_whatsapp()
                    time.sleep(5)
                    break

                elif "today date" in text.lower():
                    today = datetime.now()
                    formatted_date = today.strftime("%A, %d %B %Y")
                    print(formatted_date)
                    speak(f"Today is {formatted_date}")

                elif "current time" in text.lower():
                    now = datetime.now()
                    hour = now.strftime("%I").lstrip("0")
                    minute = now.strftime("%M")
                    am_pm = now.strftime("%p")

                    if minute == "00":
                        speak(f"It is {hour} o'clock {am_pm}")
                    else:
                        speak(f"The time is {hour} {minute} {am_pm}")

                    print(now.strftime("%I:%M %p"))

                else:
                    output = aiprocess(text)
                    lines = output.split("\n")

                    for line in lines:
                        if line.strip():
                            print(line)
                            speak(line)

        except sr.WaitTimeoutError:
            print("⏳ No speech detected. Try again...")

        except sr.UnknownValueError:
            print("❌ Could not understand your speech.")

        except sr.RequestError:
            print("⚠️ Could not connect to recognition service.")

        except Exception as e:
            print("Error:", e)


if __name__ == "__main__":
    main()