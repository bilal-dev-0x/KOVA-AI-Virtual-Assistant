import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import brain
import music_library  # noqa: F401  (tests reach the library through here)
from modules import agent_loop, media_keys, reminders, system_control, tool_registry, tools  # noqa: F401
from modules.actions import ActionRunner
from modules.fuzzy import best_match
from modules.logger import get_logger
from modules.result import Result
from modules.tool_registry import CONTROLLED, CRITICAL, SAFE  # noqa: F401  (re-exported for callers)

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
    r"^(please|can you|could you|would you|will you|now|then|also|and|so|ok|okay|just|again|"
    r"i said|i mean|i am saying|im saying|i told you|i want you to|um|uh)\b ?"
)
ARTICLE = re.compile(r"^(the|my|a|an)( |$)")
# "close it" means the app the last action was about
PRONOUNS = {"it", "that", "this", "them", "that app", "this app"}
PRONOUN_WORDS = {"it", "that", "this", "them"}
# "can you search anything" asks what Max can do, it isn't a search for the word "anything"
VAGUE_TARGETS = {"anything", "something", "stuff", "things", "something for me", "anything for me"}

# Requests with several parts or conditions go to the agent when Gemini is set up:
# a local regex would only see one part ("close whatever uses ram except vs code" is not "ram")
JOINERS = re.compile(r"\b(and|then|so|after|before|except|but|if|unless|whatever|whichever|because|while|instead)\b")
MAX_LOCAL_WORDS = 8


def needs_agent(text):
    if JOINERS.search(text):
        return True
    # Long but plain reminders ("set a timer of for 30 minutes 30 seconds") parse fine locally
    return len(text.split()) > MAX_LOCAL_WORDS and parse_reminder(text) is None


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
    return _collapse_repeat(text.strip())


def _collapse_repeat(text):
    """'pause the song pause the song' -> 'pause the song'"""
    words = text.split()
    for size in range(1, len(words) // 2 + 1):
        if len(words) % size == 0 and words == words[:size] * (len(words) // size):
            return " ".join(words[:size])
    return text


# "use a male voice", "talk like a woman", "change your voice"
GENDER_WORDS = {"male": "male", "man": "male", "boy": "male", "guy": "male",
                "female": "female", "woman": "female", "girl": "female", "lady": "female"}
_GENDER = "male|man|boy|guy|female|woman|girl|lady"


def voice_args(word):
    """'man' -> male; no word means switch to the other voice."""
    from modules import voice_handler
    if word in GENDER_WORDS:
        return {"gender": GENDER_WORDS[word]}
    return {"gender": "male" if voice_handler.current_voice() == "female" else "female"}


# Answers to "Chrome has 2 windows: one, YouTube; two, Gmail. Which one?"
ORDINALS = {"one": 0, "1": 0, "first": 0, "two": 1, "2": 1, "second": 1, "three": 2, "3": 2, "third": 2,
            "four": 3, "4": 3, "fourth": 3, "five": 4, "5": 4, "fifth": 4, "last": -1}
ALL_WORDS = {"all", "both", "every", "everything", "all of them", "whole"}
MAX_WINDOWS_READ = 5


def pick_window(answer, titles):
    """The title the answer means, "" for all of them, or None if it's unclear."""
    words = answer.split()
    if not words:
        return None
    if ALL_WORDS & set(words) or answer in ALL_WORDS:
        return ""
    # A name first: "the one with youtube" must not pick window "one"
    stripped = re.sub(r"^(the |close )*(one )?(with |called |named )?", "", answer)
    stripped = re.sub(r" (one|window|tab)$", "", stripped)
    named = best_match(stripped, titles)
    if named:
        return named
    for word in words:
        if word in ORDINALS and ORDINALS[word] < len(titles):
            return titles[ORDINALS[word]]
    return None


# "can you download anything" is a question about Max: it gets an answer, however short
QUESTION = re.compile(r"^(can|could|will|would|do|does|are|is) you\b|^(what|whats|who|why|how|where|when|which)\b")


def _probably_not_an_app(target, problem):
    """'check all logs' isn't an app name; 'discord' or 'notepad' would be."""
    return bool(target) and problem.message.startswith("I don't know") and len(target.split()) >= 2


# "search x in brave browser", "search cats with chrome"
IN_BROWSER = re.compile(r" (in|on|with|using) (?P<browser>brave|chrome|google chrome|edge|microsoft edge|firefox)"
                        r"( browser)?$")

# "search resident evil in file explorer", "find my cv on my pc"
ON_PC = re.compile(r" (in|on) (a |the |my )?(file explorer|explorer|pc|computer|laptop|files|this pc|drive)$")


def search_args(query):
    """'cats on youtube' -> query cats, site youtube; 'youtube for cats' too."""
    match = (re.match(r"^(?P<site>youtube|google)( for)? (?P<q>.+)$", query)
             or re.match(r"^(?P<q>.+?) (on|in) (?P<site>youtube|google)$", query))
    if match:
        return {"query": match["q"], "site": match["site"]}
    return {"query": query}


def clean_arg(arg):
    """'the spotify' -> 'spotify'; 'the' -> ''"""
    return ARTICLE.sub("", (arg or "").strip()).strip()


# ---------- reminders said out loud ----------

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty five": 45, "fifty": 50, "sixty": 60, "half an": 0.5, "half a": 0.5,
}
_NUMBER = r"\d+(?: \d+)?(?:\.\d+)?|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_UNIT = r"seconds?|secs?|minutes?|mins?|hours?|hrs?"
# "30 minutes 30 seconds" and "1 hour and 15 minutes" have a second part
DELAY = rf"(?P<num>{_NUMBER}) ?(?P<unit>{_UNIT})(?:(?: and)? (?P<num2>{_NUMBER}) ?(?P<unit2>{_UNIT}))?"
CLOCK = r"(?P<hour>\d{1,2})(?: (?P<minute>\d{2}))? ?(?P<ampm>am|pm|a m|p m)?"
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}

REMINDER_PATTERNS = [
    rf"^remind me (?:in|after) {DELAY} (?:to |that |about )?(?P<msg>.+)$",
    rf"^remind me (?:to |that |about )?(?P<msg>.+?) (?:in|after) {DELAY}$",
    rf"^remind me (?:at|by) {CLOCK} (?:to |that |about )?(?P<msg>.+)$",
    rf"^remind me (?:to |that |about )?(?P<msg>.+?) (?:at|by) {CLOCK}$",
    rf"^(?:set |start )?(?:a |an )?(?:timer|alarm|reminder) (?:(?:for|of|in|after) )*{DELAY}$",
    rf"^set (?:a |an )?reminder (?:(?:for|of|in|after) )*{DELAY} (?:to |that |about )(?P<msg>.+)$",
    rf"^set (?:a |an )?reminder (?:to |that |about )(?P<msg>.+?) (?:in|after|for) {DELAY}$",
    rf"^(?:set |start )?(?:a |an )?{DELAY} (?:timer|alarm)$",
]


def _delay_seconds(num, unit):
    num = NUMBER_WORDS.get(num, None) or float(num.replace(" ", "."))
    return num * UNIT_SECONDS[unit[0]]


def _clock_seconds(hour, minute, ampm, now=None):
    now = now or datetime.now()
    hour, minute = int(hour), int(minute or 0)
    if hour > 23 or minute > 59:
        return None
    if ampm:
        ampm = ampm.replace(" ", "")
        if hour > 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
        candidates = [hour]
    else:
        # "at 5" means whichever 5 o'clock comes next
        candidates = [hour] if hour >= 12 else [hour, hour + 12]

    def until(h):
        due = now.replace(hour=h, minute=minute, second=0, microsecond=0)
        return (due if due > now else due + timedelta(days=1)) - now
    return min(until(h) for h in candidates).total_seconds()


def parse_reminder(text, now=None):
    """'remind me in 20 minutes to stretch' -> ('stretch', 1200.0), or None."""
    for pattern in REMINDER_PATTERNS:
        m = re.match(pattern, text)
        if not m:
            continue
        groups = m.groupdict()
        if groups.get("unit"):
            seconds = _delay_seconds(groups["num"], groups["unit"])
            if groups.get("unit2"):
                seconds += _delay_seconds(groups["num2"], groups["unit2"])
        else:
            seconds = _clock_seconds(groups["hour"], groups["minute"], groups["ampm"], now)
        if not seconds:
            return None
        message = groups.get("msg") or "timer"
        return message, seconds
    return None


class Agent:
    def __init__(self, config=None, confirm=None, provider="auto"):
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

        # The AI brain is optional: without it every local command still works
        self.provider = brain.get_provider(self.config) if provider == "auto" else provider
        tools.setup(self.config, self.provider)
        tool_registry.load_plugins_once()
        self.runner = ActionRunner()
        self.session = None
        self.recent_local = []  # local commands since the last agent request, for context
        self.trace = print if self.config.get("AGENT_TRACE", True) else (lambda line: None)
        self.commands = self._build_commands()

    # ---------- command table (first match wins, so specific before general) ----------

    def _build_commands(self):
        wake = wake_pattern(self.wake_words)
        tool = self._tool
        return [
            # "close yourself" anywhere exits: "close yourself close yourself" was "close an app called yourself..."
            Command("exit", rf"^(exit|quit|goodbye|bye|good bye|(stop|shut down|close|quit|exit) ({wake}))$"
                            r"|\b(close|quit|exit|stop|shut down|turn off) yourself\b|\b(shut|turn) yourself (down|off)\b",
                    self.exit, help="exit / goodbye"),
            Command("restart yourself", rf"\b(restart|reboot|reload|refresh) (yourself|{wake})\b",
                    self.restart, help="restart yourself (reads config.json again)"),
            Command("sleep", r"^(sleep|go to sleep|thats all|that is all|stop listening|thats it|thats all for now)$",
                    self.sleep, help="go to sleep (voice mode)"),
            Command("help", r"^(help|what can you do|list commands|show commands|commands)$",
                    self.help, help="help"),
            Command("greet", r"^(hello|hi|hey|good (morning|afternoon|evening))( there)?$",
                    self.greet),
            Command("undo", r"^(undo|undo (that|it|the last (thing|action|one))|take (that|it) back|reverse that)$",
                    lambda arg: self.runner.undo_last(), help="undo"),
            Command("list reminders", r"^((list|show)( my| the)? (reminders|timers)|(what|which) (reminders|timers) do i have)$",
                    lambda arg: tool("list_reminders"), help="list reminders"),
            Command("cancel reminders", r"^(cancel|clear|stop|delete)( all)?( my| the)? (reminders?|timers?|alarms?)$",
                    lambda arg: tool("cancel_reminder"), help="cancel reminders"),
            Command("reminder", r"^(?P<arg>(remind me|(set |start )?(a |an )?(timer|alarm)|set (a |an )?reminder)\b.*)$",
                    self.reminder, help="remind me in 10 minutes to <something> / set a timer for 5 minutes"),
            # Media keys come before "play <song>" so "play next song" skips a track
            Command("next track", r"^(play )?(next|skip)( (song|track|video))?$",
                    lambda arg: tool("next_track"), help="next / skip"),
            Command("previous track", r"^(play )?(the )?(previous|last)( (song|track|video))?$|^go back$",
                    lambda arg: tool("previous_track"), help="previous"),
            Command("play pause", r"^(play|pause|resume|continue|play pause)( (the )?(music|song|video))?$"
                                  r"|^(stop|close|pause|end) (the |this )?(music|song|video)( now)?$",
                    lambda arg: tool("play_pause"), help="pause / resume"),
            Command("volume up", r"\b(volume up|turn (it |the volume )?up|increase (the )?volume|raise (the )?volume|louder)\b",
                    lambda arg: tool("volume_up"), help="volume up / down"),
            Command("volume down", r"\b(volume down|turn (it |the volume )?down|decrease (the )?volume|lower (the )?volume|quieter|softer)\b",
                    lambda arg: tool("volume_down")),
            Command("mute", r"^(mute|unmute)( (the )?(volume|sound|audio))?$",
                    lambda arg: tool("mute"), help="mute / unmute"),
            Command("voice", rf"\b(?P<arg>{_GENDER})( sounding)? voice\b"
                             rf"|\bvoice (to|into) (a )?(?P<arg2>{_GENDER})\b"
                             rf"|^(talk|speak|sound) like a (?P<arg3>{_GENDER})$"
                             r"|^(change|switch) (your |the )?voice$",
                    lambda arg: tool("set_voice", **voice_args(arg)), help="use a male / female voice"),
            Command("play", r"^play (?P<arg>.+)$",
                    lambda arg: tool("play_music", query=arg), help="play <song>"),
            Command("list apps", r"\b((list|show)( the| my)?( running| open)? apps?\b|(what|which) apps are (running|open))\b",
                    lambda arg: tool("list_running_apps"), help="list apps"),
            Command("top cpu", r"\b(top|heaviest|biggest) (processes|programs|apps) by cpu\b|\bwhat is using (my |the )?(cpu|processor)\b",
                    lambda arg: tool("top_processes", sort_by="cpu")),
            Command("top processes", r"\b(top|heaviest|biggest) (processes|programs|apps)\b|\bwhat is using (my |the )?(ram|memory)\b",
                    lambda arg: tool("top_processes"), help="top processes / what is using my ram"),
            Command("new tab", r"^(open )?(a )?(new )?tab( in (google|chrome|the browser|browser))?$",
                    lambda arg: tool("open_app", name="google"), help="open a new tab"),
            Command("check logs", r"^(check|read|show|tell me)( me)?( the| my| your)? "
                                 r"(logs?|locks?|blogs?|errors?|problems?)$",
                    lambda arg: tool("recent_problems"), help="check logs"),
            # Task Manager can't be opened on a chosen tab from outside, so Max says where to click
            Command("task manager tab", r"\b(?P<arg>performance|processes|startup|app history|users|details|services)"
                                        r"( tab)?\b.*\btask manager\b"
                                        r"|\btask manager\b.*\b(?P<arg2>performance|processes|startup|app history"
                                        r"|users|details|services)\b",
                    self.task_manager_tab),
            Command("start menu", r"\b(open|show|press|search in|search on)( the)? start menu\b|^start menu$",
                    lambda arg: tool("open_start_menu"), help="open start menu"),
            # "windows?" so "open new windows" isn't new_window(name="s")
            Command("new window", r"^(open )?(a |one )?(new|another|more|one more|second|extra) windows?"
                                  r"( (of|in|for))?( (?P<arg>.+))?$|^open (?P<arg2>.+?) in a new window$",
                    lambda arg: tool("new_window", name=arg), help="new window of <app>",
                    ask_text="New window of what?"),
            Command("open", r"^(open|launch|start)( (?P<arg>.+))?$",
                    lambda arg: tool("open_app", name=arg), help="open <app or website>", ask_text="Open what?"),
            # "force close calculator", "close it anyway", "force it" after a close failed
            Command("force close", r"^force (close|quit|kill|stop|end)( (?P<arg>.+))?$|^force (?P<arg2>it)$"
                                   r"|^(close|quit|kill|end|stop) (?P<arg3>.+?) (forcefully|by force|anyway|forcibly)$",
                    lambda arg: tool("force_close", name=arg), level=CRITICAL, help="force close <app>",
                    confirm_text="Force close {arg}? Anything unsaved in it will be lost.", ask_text="Force close what?",
                    prepare=lambda arg: system_control.check_force(arg)),
            Command("close", r"^(close|quit|kill|exit)( (?P<arg>.+))?$",
                    lambda arg: tool("close_app", name=arg), level=CONTROLLED, help="close <app>",
                    confirm_text="Close {arg}?", ask_text="Close what?",
                    prepare=lambda arg: system_control.check_app(arg, must_be_running=True)),
            Command("restart", r"^(restart|reopen|relaunch)( (?P<arg>.+))?$",
                    lambda arg: tool("restart_app", name=arg), level=CONTROLLED, help="restart <app>",
                    confirm_text="Restart {arg}?", ask_text="Restart what?",
                    prepare=lambda arg: system_control.check_app(arg)),
            Command("system status", r"^(status|health|report)$|\b(system (status|report|health)|(pc|computer|laptop) (status|health))\b"
                                     r"|^check( my| the)? (system|pc|computer|laptop)$",
                    lambda arg: tool("system_status"), help="system status"),
            # "how much is chrome using my ram" is about one program, not the whole PC
            Command("program memory", r"^how much (ram|memory) (is|does) (?P<arg>.+?) (using|use|taking|take|consuming)$"
                                      r"|^how much (?P<arg2>.+?) (is )?(using|consuming|taking|zooming|eating)( up)? "
                                      r"(my |the )?(ram|memory)$",
                    lambda arg: tool("program_memory", name=arg), help="how much ram is <app> using"),
            Command("cpu and ram", r"\b(cpu|processor)\b.*\b(ram|memory)\b|\b(ram|memory)\b.*\b(cpu|processor)\b",
                    lambda arg: tool("system_status")),
            Command("cpu", r"\b(cpu|processor)\b",
                    lambda arg: tool("cpu_usage"), help="cpu usage"),
            Command("ram", r"\b(ram|memory usage)\b",
                    lambda arg: tool("ram_usage"), help="ram usage"),
            Command("disk", r"\b(disk|disks|storage|drive space|free space)\b",
                    lambda arg: tool("disk_space"), help="disk space"),
            Command("battery", r"\bbattery\b",
                    lambda arg: tool("battery_status"), help="battery"),
            Command("date", r"\b(todays? date|what is the date|whats the date|what date is it|date today|what day is it)\b",
                    self.date, help="what's the date"),
            Command("time", r"\b(current time|what time is it|whats the time|what is the time|time now|tell me the time)\b",
                    self.time, help="what time is it"),
            Command("search", r"^(search|google|look up|find)( for| about)?( (?P<arg>.+))?$",
                    self.search, help="search <something> / search <file> on my pc",
                    ask_text="What should I search for?"),
            Command("gpu", r"\b(gpu|graphics card|video card)\b",
                    lambda arg: tool("gpu_usage"), help="gpu usage"),
            Command("news", r"^((tell me|read|get|show) )?(me )?(the )?((latest|top|global|todays|daily) )?(news|headlines)( today)?$",
                    self.news, help="news"),
        ]

    def _tool(self, tool_name, /, **args):
        """Run a tool for a local command. Approval was already asked for in handle().

        Goes through runner.prepare() so a local command obeys permissions.json
        (blocked) and the tool's own argument checks exactly like an AI call does.
        """
        tool, clean, problem = self.runner.prepare(tool_name, args)
        if problem:
            log.info("Local tool %s not run: %s", tool_name, problem.message)
            return problem
        approval = "confirmed" if tool.level != SAFE else "not needed"
        return self.runner.run(tool, clean, approval, source="local")

    # ---------- routing ----------

    def normalize(self, text):
        return normalize(text, self.wake_words)

    def normalize_wake(self, text):
        """Like normalize, but keeps "can you": 'max can you download anything'."""
        text = re.sub(r"[^\w\s]", " ", text.lower().replace("'", ""))
        wake = re.compile(rf"^\s*((hey|ok|okay|hi) )?({wake_pattern(self.wake_words)})?\b\s*")
        return wake.sub("", text).strip()

    def find_wake_word(self, heard):
        """Return the text after the wake word, or None if the wake word wasn't said."""
        m = re.search(rf"\b({wake_pattern(self.wake_words)})\b", heard.lower())
        return heard[m.end():] if m else None

    def match(self, text):
        """Return (command, arg) for already-normalized text, or (None, None)."""
        for command in self.commands:
            m = command.regex.search(text)
            if m:
                groups = m.groupdict()
                arg = clean_arg(groups.get("arg") or groups.get("arg2") or groups.get("arg3"))
                return command, arg or None
        return None, None

    def handle(self, raw_text):
        text = self.normalize(raw_text)
        log.info("Input: %s", text)

        if not text:
            return Result(True, f"Yes {self.title}?")

        # Hybrid routing: simple commands stay local (instant, free, offline),
        # requests with several parts go to the agent
        if self.provider and needs_agent(text):
            return self._ask_agent(raw_text)

        if text.startswith("google ") and system_control.is_app_alias(text):
            # "google chrome" is the app, not a Google search for "chrome"
            text = f"open {text}"

        command, arg = self.match(text)
        if command is None:
            return self._fallback(raw_text, text)

        if arg in PRONOUNS or (arg and arg.split()[0] in PRONOUN_WORDS):
            # "close it" and "close it so i can have my ram" both mean the last app
            arg = self.runner.last_target

        if arg in VAGUE_TARGETS:
            arg = None
        if arg is None and command.ask_text:
            answer = self.ask(command.ask_text) or ""
            arg = clean_arg(self.normalize(answer))
            if not arg:
                return Result(False, "Okay, never mind.")
            raw_text = f"{raw_text} {answer}"
            joined = f"{text} {arg}"
            if self.match(joined)[0] not in (None, command):
                return self.handle(joined)

        if command.prepare:
            # Check the target first, so Max never asks "Close the?" or closes something unknown
            target = arg
            arg, problem = command.prepare(arg)
            if problem:
                log.info("Command %s not run: %s", command.name, problem.message)
                if self.provider and _probably_not_an_app(target, problem):
                    # Google hears "ok Claude, check the logs" as "ok close check the logs"
                    return self._ask_agent(raw_text)
                return problem

        window = None
        if command.name == "close":
            # Several windows: say which one ("Chrome has 2 windows... Which one?")
            window = self._ask_which_window(arg)
            if isinstance(window, Result):
                return window

        # Picking a window was already the answer to a question, so it isn't asked twice
        if command.level != SAFE and window is None:
            question = command.confirm_text.format(arg=arg) if command.confirm_text else f"Should I {text}?"
            if not self.confirm(question):
                log.info("Cancelled %s %s", command.name, arg or "")
                return Result(False, "Okay, cancelled.")

        try:
            result = self._tool("close_app", name=arg, window=window) if window else command.handler(arg)
        except Exception as e:
            log.exception("Command %s failed", command.name)
            return Result(False, f"Sorry {self.title}, something went wrong with {command.name}.", detail=str(e))

        log.info("Command %s(%s) -> %s", command.name, arg or "", "ok" if result.success else "failed")
        if self.provider and command.name not in ("exit", "sleep", "help", "greet"):
            # So the agent understands "actually close it" after a local "open discord"
            self.recent_local = (self.recent_local + [f"'{text}' -> {result.message}"])[-5:]
        return result

    def _fallback(self, raw_text, text):
        if len(text.split()) < self.min_ai_words and not QUESTION.match(self.normalize_wake(raw_text)):
            return Result(False, "Sorry, I didn't catch that.")
        if not self.provider:
            return Result(False, "I don't have a command for that, and Gemini isn't set up. "
                                 "Say help to hear what I can do.")
        return self._ask_agent(raw_text)

    def _ask_agent(self, raw_text):
        """Same guard the local commands get: a broken reply must not kill the loop.

        agent_loop already turns BrainError and Ctrl+C into a Result; anything else
        (a malformed relay payload, an SDK surprise) is caught here instead of
        escaping handle() and taking the whole assistant down with it.
        """
        try:
            return self._run_agent(raw_text)
        except Exception:
            log.exception("Agent request failed")
            if self.session is not None:
                self.session.reset()
            return Result(False, f"Sorry {self.title}, I couldn't work that out just now. "
                                 "Please try again in a moment.")

    def _run_agent(self, raw_text):
        if self.session is None:
            self.session = self.provider.session(agent_loop.system_prompt(self.config), tool_registry.all_tools())

        message = raw_text
        if self.recent_local:
            message = ("(Commands the user ran without you since your last reply: "
                       + "; ".join(self.recent_local) + ")\n" + raw_text)
            self.recent_local = []

        print("Thinking.....")
        return agent_loop.run(self.session, message, self.runner, self.confirm, trace=self.trace)

    # ---------- handlers ----------

    def _ask_which_window(self, app):
        """None: one window or none, ask as usual. A title or "" (all): the user picked.
        A Result: the user didn't pick, nothing is closed."""
        _, entry = system_control._resolve_app(app)
        titles = list(dict.fromkeys(
            system_control.short_title(t) for _, t in system_control.app_windows((entry or {}).get("process", []))))
        if len(titles) < 2:
            return None
        if len(titles) > MAX_WINDOWS_READ:
            question = f"{app} has {len(titles)} windows. Say all, or part of one window's name."
        else:
            listed = "; ".join(f"{word}, {title}" for word, title in zip(("one", "two", "three", "four", "five"), titles))
            question = f"{app} has {len(titles)} windows: {listed}. Which one should I close, or all of them?"
        answer = self.normalize(self.ask(question) or "")
        if not answer or answer in {"none", "no", "nothing", "cancel", "neither", "never mind"}:
            return Result(False, "Okay, I didn't close anything.")
        picked = pick_window(answer, titles)
        if picked is None:
            return Result(False, f"I couldn't tell which {app} window you meant, so I didn't close anything.")
        log.info("Window picked for %s: %r", app, picked or "all")
        return picked

    def task_manager_tab(self, tab):
        result = self._tool("open_app", name="task manager")
        if result.success:
            result.message = f"Opening Task Manager. Click the {tab.title()} tab on the left."
        return result

    def restart(self, arg):
        return Result(True, "Restarting.", stop=True, restart=True)

    def search(self, query):
        browser = IN_BROWSER.search(query)
        if browser:
            query = IN_BROWSER.sub("", query).strip()
            website = system_control.website_for(query)
            if website:
                return system_control.open_in_browser(browser["browser"], website[1])
            return self._tool("open_web_search", **search_args(query), browser=browser["browser"])
        if system_control.website_for(query):
            return self._tool("open_app", name=query)
        if ON_PC.search(query):
            query = ON_PC.sub("", query).strip()
            if not query or query in VAGUE_TARGETS:
                query = clean_arg(self.normalize(self.ask("What should I look for on your PC?") or ""))
                if not query:
                    return Result(False, "Okay, never mind.")
            return self._tool("search_files", query=query)
        return self._tool("open_web_search", **search_args(query))

    def exit(self, arg):
        return Result(True, f"Goodbye {self.title}.", stop=True)

    def sleep(self, arg):
        return Result(True, f"Okay, going to sleep. Say {self.name} when you need me.", sleep=True)

    def help(self, arg):
        detail = "\n".join(f"  - {c.help}" for c in self.commands if c.help)
        message = ("I can open, close and restart apps, play music, control the volume, check your system, "
                   "set reminders, undo my last action, and tell the time and date.")
        if self.provider:
            message += " For anything bigger, just ask in your own words and I'll plan it with Gemini."
        return Result(True, message, detail=detail)

    def greet(self, arg):
        return Result(True, f"Hi {self.title}, how are you?")

    def reminder(self, text):
        parsed = parse_reminder(text)
        if parsed:
            message, seconds = parsed
            return self._tool("set_reminder", message=message, minutes=seconds / 60)
        if self.provider:
            return self._ask_agent(text)
        return Result(False, "Tell me when, like: remind me in 10 minutes to stretch.")

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
        country = self.config.get("NEWS_COUNTRY", "pk")
        if self.provider:
            # Google Search grounding: live headlines without a separate news API key
            result = self._tool("web_search", query=f"Top 5 news headlines today in country code {country}, "
                                                    "one short sentence each.")
            if result.success:
                return result

        api_key = self.config.get("NEWS_API_KEY", "")
        if not api_key:
            return Result(False, "News needs Gemini or a News API key, and neither is set up.")

        import requests
        try:
            r = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={"country": country, "apiKey": api_key},
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
