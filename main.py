import argparse
import json
import os
import re
import time

from desktop_agent import Agent
from modules.logger import get_logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# The assistant's name and wake words come from ASSISTANT_NAME / WAKE_WORDS in config.json
YES_WORDS = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "confirm", "go ahead", "of course")

log = get_logger()


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("config.json not found, running with local commands only.")
        print("Copy config.json.example to config.json to add your API keys.")
    except json.JSONDecodeError as e:
        print("config.json is not valid JSON:", e)
    return {}


def is_yes(answer):
    answer = re.sub(r"[^\w\s]", " ", (answer or "").lower()).split()
    text = " ".join(answer)
    return any(text == word or text.startswith(word + " ") for word in YES_WORDS)


def respond(result, voice, name="KOVA"):
    print(f"{name}: {result.message}")
    if result.detail:
        print(result.detail)

    if voice:
        from modules.voice_handler import speak
        for line in result.message.split("\n"):
            if line.strip():
                speak(line)


def run_text(agent):
    print(f"{agent.name} text mode. Type 'help' for commands, 'exit' to quit.")

    agent.confirm = lambda question: is_yes(input(f"{agent.name}: {question} (yes/no) "))
    agent.ask = lambda question: input(f"{agent.name}: {question} ")

    while True:
        try:
            line = input("You: ")
        except EOFError:
            break

        if not line.strip():
            continue

        result = agent.handle(line)
        respond(result, voice=False, name=agent.name)
        if result.stop:
            break


def run_voice(agent):
    from modules import voice_handler
    from modules.voice_handler import speak

    voice_handler.configure(agent.config)

    def listen(wait_seconds=5, max_seconds=10):
        heard = voice_handler.listen_text(wait_seconds=wait_seconds, max_seconds=max_seconds)
        if heard:
            print("You said:", heard)
            log.info("Heard: %s", heard)
        return heard

    def ask(question):
        speak(question)
        return listen(wait_seconds=6)

    def confirm(question):
        answer = ask(question)
        if answer is None:
            answer = ask("Sorry, I didn't hear you. Yes or no?")
        log.info("Confirmation %r -> %r", question, answer)
        return is_yes(answer)

    agent.confirm = confirm
    agent.ask = ask
    # Short misheard fragments ("close the", "get updated") are not worth a Gemini call
    agent.min_ai_words = 3

    # Once woken, Max keeps listening without the wake word until it hears
    # "go to sleep" or nothing for AWAKE_TIMEOUT seconds (0 = never sleep on its own)
    timeout = agent.config.get("AWAKE_TIMEOUT", 60)
    awake = False
    last_active = 0.0

    speak(f"Initializing {agent.name}....")
    print(f"Say '{agent.name}' to wake me up.")

    while True:
        if awake and timeout and time.time() - last_active > timeout:
            awake = False
            print(f"💤 No commands for {timeout} seconds, going to sleep.")
            speak("Going to sleep.")
            log.info("Fell asleep after %ss idle", timeout)

        if not awake:
            print("💤 Listening for wake word...")
            heard = listen(wait_seconds=3, max_seconds=6)
            if not heard:
                continue

            command = agent.find_wake_word(heard)
            if command is None:
                continue

            awake = True
            last_active = time.time()
            log.info("Woke up")

            if not agent.normalize(command):
                speak(f"Yes {agent.title}? I'm listening.")
                last_active = time.time()
                continue
        else:
            print("🎤 Listening (awake)...")
            command = listen(wait_seconds=5)
            if not command:
                continue

        result = agent.handle(command)
        respond(result, voice=True, name=agent.name)
        # Count from when Max finished speaking, so long answers don't use up the timeout
        last_active = time.time()

        if result.stop:
            break
        if result.sleep:
            awake = False


def main():
    parser = argparse.ArgumentParser(description="KOVA desktop assistant")
    parser.add_argument("--text", action="store_true", help="type commands instead of speaking")
    parser.add_argument("--mic-test", action="store_true", help="show live mic levels and a suggested threshold")
    args = parser.parse_args()

    if args.mic_test:
        from modules.voice_handler import mic_test
        mic_test()
        return

    agent = Agent(load_config())
    log.info("KOVA started in %s mode", "text" if args.text else "voice")

    try:
        if args.text:
            run_text(agent)
        else:
            run_voice(agent)
    except KeyboardInterrupt:
        print("\nKOVA shutting down.")

    log.info("KOVA stopped")


if __name__ == "__main__":
    main()
