import speech_recognition as sr
import webbrowser
import os
import subprocess
import time
import music_library
import requests
from openai import OpenAI
from datetime import datetime
from gtts import gTTS
import pygame
import json

# Load API keys from config.json
try:
    with open("config.json", "r") as f:
        config = json.load(f)
except FileNotFoundError:
    print("config.json not found. Copy config.json.example to config.json and add your API keys.")
    exit()

api_key = config.get("OPENAI_API_KEY")
news_api = config.get("NEWS_API_KEY", "")

if not api_key:
    print("Please set your OpenAI API key in config.json before running Jarvis.")
    exit()

client = OpenAI(api_key=api_key)
recognizer = sr.Recognizer()

def speak(text):
    try:
        filename = "jarvis_voice.mp3"
        tts = gTTS(text=text, lang="en")
        tts.save(filename)

        pygame.mixer.init()
        pygame.mixer.music.load(filename)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

        pygame.mixer.music.unload()
        os.remove(filename)

    except Exception as e:
        print("Error with gTTS:", e)

def aiprocess(command):
    print("Thinking for best.....")
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are Jarvis, a helpful AI virtual assistant, give short responses and greet 'Bilal'."},
            {"role": "user", "content": command}
        ]
    )
    return response.choices[0].message.content

def open_whatsapp():
    subprocess.run([
        "powershell",
        "(Get-StartApps -Name 'WhatsApp').AppID | ForEach-Object { Start-Process \"shell:AppsFolder\\$_\" }"
    ], shell=True)
    speak("Opening WhatsApp")

if __name__ == "__main__":
    speak("Initializing Jarvis....")

    while True:
        try:
            chrome_path = "C:/Program Files/Google/Chrome/Application/chrome.exe %s"
            webpath = webbrowser.get(chrome_path)

            with sr.Microphone() as source:
                print("Listening for wake word... 🎤")
                audio = recognizer.listen(source, timeout=3)

            command = recognizer.recognize_google(audio)
            print("You said:", command)

            if "jarvis" in command.lower():
                speak("Yes Bilal?")
                with sr.Microphone() as source:
                    print("Listening for command... 🎤")
                    recognizer.adjust_for_ambient_noise(source, duration=1)
                    audio = recognizer.listen(source, timeout=5)

                text = recognizer.recognize_google(audio)
                print("Your command:", text)

                if "hello" in text.lower():
                    speak("Hi Bilal, how are you?")

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
                        speak("Sorry Bilal, this song is not in your library.")

                elif "global news" in text.lower():
                    if not news_api:
                        speak("News API key is not configured.")
                    else:
                        r = requests.get(f"https://newsapi.org/v2/top-headlines?country=pk&apiKey={news_api}")
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
                            speak("Sorry Bilal, I could not fetch the news at this moment.")

                elif "pakistani news" in text.lower():
                    speak("Pakistani news not configured in this release.")

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
