import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import quote_plus

import music_library
from modules import ai_brain, media_keys, performance_monitor, system_control
from modules.fuzzy import best_match
from modules.logger import get_logger
from modules.result import Result

# Safety levels from the roadmap:
#   safe       - runs straight away
#   controlled - KOVA asks first
#   critical   - KOVA asks right before running (used from Day 2 on)
SAFE, CONTROLLED, CRITICAL = "safe", "controlled", "critical"

log = get_logger()


@dataclass
class Command:
    name: str
    pattern: str           # regex; an optional (?P<arg>...) group captures the target
    handler: Callable      # handler(arg) -> Result
    level: str = SAFE
    help: str = ""
    confirm_text: str = ""  # question asked before a controlled/critical command, may use {arg}
    ask_text: str = ""      # asked when the target is missing ("close" -> "Close what?")
    prepare: Callable = None  # prepare(arg) -> (clean_arg, None) or (None, Result) when it can't run

    def __post_init__(self):
        self.regex = re.compile(self.pattern)


# Words people put in front of a command: "now open github", "I said close chrome"
FILLER_PREFIX = re.compile(
    r"^(please|can you|could you|would you|will you|now|then|also|and|so|ok|okay|just|i said|i mean|um|uh)\b ?"
)
ARTICLE = re.compile(r"^(the|my|a|an)( |$)")


def wake_words_from(config):
    """The assistant's name plus the words speech recognition may hear instead of it."""
    words = config.get("WAKE_WORDS") or [config.get("ASSISTANT_NAME", "KOVA")]
    return [w.lower() for w in words]


def wake_pattern(wake_words):
    # Longest first, so "macs" wins over "mac"
    return "|".join(re.escape(w) for w in sorted(wake_words, key=len, reverse=True))


def normalize(text, wake_words=("kova",)):
    """'Hey Max, now can you open Chrome please!' -> 'open chrome'"""
    text = text.lower().replace("'", "").replace("’", "")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    wake = re.compile(rf"^((hey|ok|okay|hi) )?({wake_pattern(wake_words)})\b ?")
    # Repeat until nothing changes: "max max now please open chrome"
    while True:
        before = text
        text = FILLER_PREFIX.sub("", wake.sub("", text))
        if text == before:
            break

    text = re.sub(r" please$", "", text)
    return text.strip()


def clean_arg(arg):
    """'the spotify' -> 'spotify'; 'the' -> ''"""
    return ARTICLE.sub("", (arg or "").strip()).strip()


class Agent:
    def __init__(self, config=None, confirm=None):
        self.config = config or {}
        self.name = self.config.get("ASSISTANT_NAME", "KOVA")
        self.wake_words = wake_words_from(self.config)
        self.title = self.config.get("USER_TITLE", "Commander")
        # confirm(question) -> bool and ask(question) -> str or None;
        # main.py answers them by voice or keyboard, tests pass lambdas
        self.confirm = confirm or (lambda question: False)
        self.ask = lambda question: None
        # Voice mode raises this so misheard one-word fragments don't go to Gemini
        self.min_ai_words = 1
        self.commands = self._build_commands()

    # ---------- command table (first match wins, so specific before general) ----------

    def _build_commands(self):
        wake = wake_pattern(self.wake_words)
        return [
            Command("exit", rf"^(exit|quit|goodbye|bye|good bye|(stop|shut down) ({wake}))$",
                    self.exit, help="exit / goodbye"),
            Command("sleep", r"^(sleep|go to sleep|thats all|that is all|stop listening|thats it|thats all for now)$",
                    self.sleep, help="go to sleep (voice mode)"),
            Command("help", r"^(help|what can you do|list commands|show commands|commands)$",
                    self.help, help="help"),
            Command("greet", r"^(hello|hi|hey|good (morning|afternoon|evening))( there)?$",
                    self.greet),
            # Media keys come before "play <song>" so "play next song" skips a track
            Command("next track", r"^(play )?(next|skip)( (song|track|video))?$",
                    lambda arg: media_keys.next_track(), help="next / skip"),
            Command("previous track", r"^(play )?(the )?(previous|last)( (song|track|video))?$|^go back$",
                    lambda arg: media_keys.previous_track(), help="previous"),
            Command("play pause", r"^(play|pause|resume|continue|play pause)( (the )?(music|song|video))?$"
                                  r"|^stop (the )?(music|song|video)$",
                    lambda arg: media_keys.play_pause(), help="pause / resume"),
            Command("volume up", r"\b(volume up|turn (it |the volume )?up|increase (the )?volume|raise (the )?volume|louder)\b",
                    lambda arg: media_keys.volume_up(), help="volume up / down"),
            Command("volume down", r"\b(volume down|turn (it |the volume )?down|decrease (the )?volume|lower (the )?volume|quieter|softer)\b",
                    lambda arg: media_keys.volume_down()),
            Command("mute", r"^(mute|unmute)( (the )?(volume|sound|audio))?$",
                    lambda arg: media_keys.mute(), help="mute / unmute"),
            Command("play", r"^play (?P<arg>.+)$",
                    self.play, help="play <song>"),
            Command("list apps", r"\b((list|show)( the| my)?( running| open)? apps|(what|which) apps are (running|open))\b",
                    self.list_apps, help="list apps"),
            Command("open", r"^(open|launch|start)( (?P<arg>.+))?$",
                    self.open, help="open <app or website>", ask_text="Open what?"),
            Command("close", r"^(close|quit|kill|exit)( (?P<arg>.+))?$",
                    self.close, level=CONTROLLED, help="close <app>", confirm_text="Close {arg}?",
                    ask_text="Close what?",
                    prepare=lambda arg: system_control.check_app(arg, must_be_running=True)),
            Command("restart", r"^(restart|reopen|relaunch)( (?P<arg>.+))?$",
                    self.restart, level=CONTROLLED, help="restart <app>", confirm_text="Restart {arg}?",
                    ask_text="Restart what?",
                    prepare=lambda arg: system_control.check_app(arg)),
            Command("system status", r"^(status|health|report)$|\b(system (status|report|health)|(pc|computer|laptop) (status|health))\b",
                    lambda arg: performance_monitor.system_status(), help="system status"),
            Command("cpu", r"\b(cpu|processor)\b",
                    lambda arg: performance_monitor.cpu_report(), help="cpu usage"),
            Command("ram", r"\b(ram|memory usage)\b",
                    lambda arg: performance_monitor.ram_report(), help="ram usage"),
            Command("disk", r"\b(disk|disks|storage|drive space|free space)\b",
                    lambda arg: performance_monitor.disk_report(), help="disk space"),
            Command("battery", r"\bbattery\b",
                    lambda arg: performance_monitor.battery_report(), help="battery"),
            Command("date", r"\b(todays? date|what is the date|whats the date|what date is it|date today|what day is it)\b",
                    self.date, help="what's the date"),
            Command("time", r"\b(current time|what time is it|whats the time|what is the time|time now|tell me the time)\b",
                    self.time, help="what time is it"),
            Command("news", r"^((tell me|read|get|show) )?(me )?(the )?((latest|top|global|todays) )?(news|headlines)( today)?$",
                    self.news, help="news"),
        ]

    # ---------- routing ----------

    def normalize(self, text):
        return normalize(text, self.wake_words)

    def find_wake_word(self, heard):
        """Return the text after the wake word, or None if the wake word wasn't said."""
        m = re.search(rf"\b({wake_pattern(self.wake_words)})\b", heard.lower())
        return heard[m.end():] if m else None

    def match(self, text):
        """Return (command, arg) for already-normalized text, or (None, None)."""
        for command in self.commands:
            m = command.regex.search(text)
            if m:
                arg = clean_arg(m.groupdict().get("arg"))
                return command, arg or None
        return None, None

    def handle(self, raw_text):
        text = self.normalize(raw_text)
        log.info("Input: %s", text)

        if not text:
            return Result(True, f"Yes {self.title}?")

        command, arg = self.match(text)
        if command is None:
            return self._fallback(raw_text, text)

        if arg is None and command.ask_text:
            arg = clean_arg(self.normalize(self.ask(command.ask_text) or ""))
            if not arg:
                return Result(False, "Okay, never mind.")

        if command.prepare:
            # Check the target first, so KOVA never asks "Close the?" or closes something unknown
            arg, problem = command.prepare(arg)
            if problem:
                log.info("Command %s not run: %s", command.name, problem.message)
                return problem

        if command.level != SAFE:
            question = command.confirm_text.format(arg=arg) if command.confirm_text else f"Should I {text}?"
            if not self.confirm(question):
                log.info("Cancelled %s %s", command.name, arg or "")
                return Result(False, "Okay, cancelled.")

        try:
            result = command.handler(arg)
        except Exception as e:
            log.exception("Command %s failed", command.name)
            return Result(False, f"Sorry {self.title}, something went wrong with {command.name}.", detail=str(e))

        log.info("Command %s(%s) -> %s", command.name, arg or "", "ok" if result.success else "failed")
        return result

    def _fallback(self, raw_text, text):
        if len(text.split()) < self.min_ai_words:
            return Result(False, "Sorry, I didn't catch that.")

        answer = ai_brain.ask(raw_text, self.config)
        if answer:
            log.info("Gemini answered: %s", answer[:200])
            return Result(True, answer)
        if not ai_brain.is_available(self.config):
            return Result(False, "I don't have a command for that, and Gemini isn't set up. "
                                 "Say help to hear what I can do.")
        return Result(False, f"Sorry {self.title}, I couldn't reach Gemini right now.")

    # ---------- handlers ----------

    def exit(self, arg):
        return Result(True, f"Goodbye {self.title}.", stop=True)

    def sleep(self, arg):
        return Result(True, f"Okay, going to sleep. Say {self.name} when you need me.", sleep=True)

    def help(self, arg):
        detail = "\n".join(f"  - {c.help}" for c in self.commands if c.help)
        return Result(True, "I can open, close and restart apps, play music, control the volume, check your system, "
                            "tell the time and date, and read the news. Anything else, I'll ask Gemini.",
                      detail=detail)

    def greet(self, arg):
        return Result(True, f"Hi {self.title}, how are you?")

    def play(self, arg):
        query = re.sub(r"^(the )?song ", "", arg)
        query = re.sub(r" (song|on youtube)$", "", query)

        song = best_match(query, music_library.library.keys())
        if song:
            system_control.open_url(music_library.library[song], self.config)
            return Result(True, f"Playing {song.split(' - ')[0]}", detail=song)

        system_control.open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}", self.config)
        return Result(True, f"{query} isn't in your library, so I searched YouTube for it.")

    def list_apps(self, arg):
        return system_control.running_apps()

    def open(self, arg):
        return system_control.open_target(arg, self.config)

    def close(self, arg):
        return system_control.close_app(arg)

    def restart(self, arg):
        return system_control.restart_app(arg, self.config)

    def date(self, arg):
        return Result(True, f"Today is {datetime.now().strftime('%A, %d %B %Y')}")

    def time(self, arg):
        now = datetime.now()
        hour = now.strftime("%I").lstrip("0")
        minute = now.strftime("%M")
        am_pm = now.strftime("%p")

        if minute == "00":
            return Result(True, f"It is {hour} o'clock {am_pm}")
        return Result(True, f"The time is {hour} {minute} {am_pm}", detail=now.strftime("%I:%M %p"))

    def news(self, arg):
        api_key = self.config.get("NEWS_API_KEY", "")
        if not api_key:
            return Result(False, "News API key is not configured.")

        import requests
        try:
            r = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={"country": self.config.get("NEWS_COUNTRY", "pk"), "apiKey": api_key},
                timeout=10
            )
            data = r.json()
        except Exception as e:
            log.error("News request failed: %s", e)
            return Result(False, f"Sorry {self.title}, I could not fetch the news right now.")

        if data.get("status") != "ok":
            log.error("News API error: %s", data.get("message"))
            return Result(False, f"Sorry {self.title}, the news service returned an error.")

        headlines = [a["title"] for a in data.get("articles", [])[:5] if a.get("title")]
        if not headlines:
            return Result(True, "There are no headlines right now.")

        lines = [f"Headline {i}: {h}" for i, h in enumerate(headlines, start=1)]
        return Result(True, "Here are the top headlines.\n" + "\n".join(lines))
