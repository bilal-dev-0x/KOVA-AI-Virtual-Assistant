import argparse
import json
import os
import re
import sys
import time

from desktop_agent import Agent
from modules import alerts, reminders
from modules.logger import get_logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# The assistant's name and wake words come from ASSISTANT_NAME / WAKE_WORDS in config.json
YES_WORDS = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright", "all right", "do it", "confirm",
             "go ahead", "of course")
# Any of these makes the answer a no, even next to a yes word ("ok, don't close it")
NO_WORDS = ("no", "nope", "nah", "not", "dont", "never", "cancel", "stop", "wait", "negative", "leave it")
# Repeating the action back ("close it" to "Should I close calculator?") is also a yes
ACTION_WORDS = ("open", "close", "restart", "end", "set", "play", "mute", "forget", "delete", "remove",
                "remember", "save")

# Said while Max waits for a slow brain answer
CANCEL = re.compile(r"\b(cancel|stop|never ?mind|forget (it|that)|leave it)\b")

log = get_logger()
_instance_lock = None  # kept for the whole run: Windows frees the name when Max exits


def single_instance():
    """False if another Max is already running. Two copies both answer every command."""
    global _instance_lock
    if os.name != "nt":
        return True
    import ctypes
    ERROR_ALREADY_EXISTS = 183
    _instance_lock = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\KOVA_Max_voice")
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


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


def is_yes(answer, question=""):
    """True only for a clear yes. 'I said yes' is a yes; 'ok you did well, not close it' is not.
    Repeating the question's action ("close it" to "Should I close calculator?") counts as yes."""
    text = (answer or "").lower().replace("'", "").replace("’", "")
    text = " " + " ".join(re.sub(r"[^\w\s]", " ", text).split()) + " "

    def says(phrase):
        return f" {phrase} " in text

    if any(says(word) for word in NO_WORDS):
        return False
    asked = set(re.findall(r"\w+", question.lower()))
    return any(says(word) for word in YES_WORDS) or any(says(w) for w in ACTION_WORDS if w in asked)


def respond(result, voice, name="KOVA"):
    print(f"{name}: {result.message}")
    if result.detail:
        print(result.detail)

    if voice:
        from modules.voice_handler import speak
        for line in result.message.split("\n"):
            if line.strip():
                speak(line)


END_OF_INPUT = {"\x04", "\x1a"}  # Ctrl+D / Ctrl+Z typed on a Windows console arrive as text


def typed(prompt):
    """input() that returns None at the end of input instead of raising EOFError."""
    try:
        line = input(prompt)
    except EOFError:
        return None
    return None if line.strip() in END_OF_INPUT else line


def run_text(agent):
    print(f"{agent.name} text mode. Type 'help' for commands, 'exit' to quit.")

    # Ending input in the middle of a question is a no, not a crash
    agent.confirm = lambda question: is_yes(typed(f"{agent.name}: {question} (yes/no) "), question)
    agent.ask = lambda question: typed(f"{agent.name}: {question} ")

    while True:
        line = typed("You: ")
        if line is None:
            print()
            break

        if not line.strip():
            continue

        with reminders.hold_short_timers():
            result = agent.handle(line)
            respond(result, voice=False, name=agent.name)
        if result.stop:
            return result


def run_voice(agent):
    import queue

    from modules import reminders, voice_handler
    from modules.voice_handler import speak

    voice_handler.configure(agent.config)

    def cancel_heard():
        """While the relay brain is thinking, keep the mic open for "cancel"."""
        heard = voice_handler.listen_text(wait_seconds=2, max_seconds=4)
        if not heard:
            return False
        log.info("Heard while waiting: %s", heard)
        if CANCEL.search(heard.lower()):
            return True
        print(f"(Still waiting for Claude. Say cancel to stop. Heard: {heard})")
        return False

    if hasattr(agent.provider, "while_waiting"):
        agent.provider.while_waiting = cancel_heard

    # A reminder fires on its timer thread; it is printed at once and spoken by
    # this loop between two listens, so it never talks over Max
    due = queue.Queue()

    def on_reminder(message):
        reminders.default_notify(message)
        due.put(f"Reminder, {agent.title}: {message}")

    def on_alert(message):
        alerts.default_notify(message)
        due.put(f"Heads up, {agent.title}. {message}")

    reminders.on_fire = on_reminder
    alerts.on_alert = on_alert

    def listen(wait_seconds=5, max_seconds=10):
        heard = voice_handler.listen_text(wait_seconds=wait_seconds, max_seconds=max_seconds)
        if heard:
            print("You said:", heard)
            log.info("Heard: %s (sure %.2f)", heard, voice_handler.last_confidence)
        return heard

    def ask(question):
        speak(question)
        return listen(wait_seconds=6)

    def confirm(question):
        answer = ask(question)
        if answer is None:
            # Google often misses a lone "yes", so ask for a few more words
            answer = ask("Sorry, I didn't hear you. Say yes do it, or no.")
        log.info("Confirmation %r -> %r", question, answer)
        return is_yes(answer, question)

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
        while not due.empty():
            speak(due.get())

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

        # Google wasn't sure what it heard: say it again, rather than act on a guess
        if voice_handler.unsure():
            log.info("Not acted on, speech recognition was unsure: %s", command)
            speak(f"Sorry {agent.title}, I didn't catch that clearly. Please say it again.")
            last_active = time.time()
            continue
        # "tell me the difference between" + a pause: the rest comes in the next breath
        command = voice_handler.finish_cut_off(command, listen)

        # A 10 second timer counts from when Max finished saying "timer set"
        with reminders.hold_short_timers():
            result = agent.handle(command)
            respond(result, voice=True, name=agent.name)
        # Count from when Max finished speaking, so long answers don't use up the timeout
        last_active = time.time()

        if result.stop:
            return result
        if result.sleep:
            awake = False


def main():
    parser = argparse.ArgumentParser(description="KOVA desktop assistant")
    parser.add_argument("--text", action="store_true", help="type commands instead of speaking")
    parser.add_argument("--mic-test", action="store_true", help="show live mic levels and a suggested threshold")
    args = parser.parse_args()

    # Emoji and trace arrows must not crash when output is redirected (Windows defaults to cp1252)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    # Real pixels on a 125% or 150% screen, so snapped windows land where they should
    from modules import windows
    windows.dpi_aware()

    if args.mic_test:
        from modules.voice_handler import mic_test
        mic_test()
        return

    if not args.text and not single_instance():
        print("Max is already running in another window. Close that one first, or say 'restart yourself' to it.")
        log.info("Second copy refused: Max is already running")
        return

    try:
        while True:
            # "restart yourself" comes back here with config.json read again
            agent = Agent(load_config())
            log.info("KOVA started in %s mode", "text" if args.text else "voice")
            if agent.config.get("ALERTS_ON"):
                alerts.start(agent.config)
            try:
                result = run_text(agent) if args.text else run_voice(agent)
            finally:
                alerts.stop()
            if not (result and result.restart):
                break
            log.info("KOVA restarting")
    except KeyboardInterrupt:
        print("\nKOVA shutting down.")

    log.info("KOVA stopped")


if __name__ == "__main__":
    main()
