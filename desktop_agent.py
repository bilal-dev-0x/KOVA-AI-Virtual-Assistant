import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import brain
import music_library  # noqa: F401  (tests reach the library through here)
from modules import (agent_loop, alerts, media_keys, memory_engine, reminders, routines, system_control,  # noqa: F401
                     tool_registry, tools)
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
    r"fine|alright|all right|right(?! click)|"
    r"i said|i mean|i am saying|im saying|i told you|i want you to|um|uh)\b ?"
)
ARTICLE = re.compile(r"^(the|my|a|an)( |$)")
# "close it" means the app the last action was about
PRONOUNS = {"it", "that", "this", "them", "that app", "this app"}
PRONOUN_WORDS = {"it", "that", "this", "them"}
# "can you search anything" asks what Max can do, it isn't a search for the word "anything"
VAGUE_TARGETS = {"anything", "something", "stuff", "things", "something for me", "anything for me"}

# Memory commands stay local even when long: "remember I like lo-fi when coding and jazz when gaming".
# "remember to call mom" is a reminder, not a fact, so it is left to the reminder or the agent.
MEMORY_COMMAND = re.compile(r"^(remember|dont forget|keep in mind|forget)\b(?! to\b)")
# The fact is taken from what was said, before normalize() lowercased it and dropped "D:\\"
REMEMBER_RAW = re.compile(r"\b(remember|don'?t forget|keep in mind)(\s+that)?[\s,:]+(?P<fact>.+)$",
                          re.IGNORECASE | re.DOTALL)
# Commands whose "it"/"that" is not the last app ("forget that" = the last fact,
# "this week" is a period)
OWN_PRONOUNS = {"forget", "activity", "activity apps", "activity songs"}
# Their replies hold your facts, so the agent only hears that one ran
MEMORY_COMMANDS = {"remember", "remember to", "list memories", "forget all", "forget", "recall"}
# Memories added to an agent request: at most this many, and only good matches
MEMORY_NOTE_FACTS = 3
MEMORY_NOTE_SCORE = 0.6

# Requests with several parts or conditions go to the agent when Gemini is set up:
# a local regex would only see one part ("close whatever uses ram except vs code" is not "ram")
JOINERS = re.compile(r"\b(and|then|so(?! (slow|bad|hot|loud|quiet|laggy|much|many|long|fast|high|low|full|busy)\b)|after|before|except|but|if|unless|whatever|whichever|because|while|instead)\b")
MAX_LOCAL_WORDS = 8
# "how is my cpu and ram" has an "and" but only asks for the system status
STATUS_ONLY = re.compile(
    r"^((how much|how|what)( is| are|s)?( my| the)? |(check|show|tell|give)( me)?( my| the)? |(my|the) )?"
    r"((cpu|processor) and (ram|memory)|(ram|memory) and (cpu|processor))"
    r"( usage| use| status| doing| looking| like| right now| now| am i using| is being used)*$")
# Answers that drop the question: "Close what?" -> "never mind" / "ok bye"
NEVER_MIND = re.compile(r"^(no|nope|nah|none|nothing|cancel|stop|never ?mind|nevermind|forget it|leave it|"
                        r"bye|goodbye|good bye|bye bye|neither|no thanks)( then| thanks| thank you| bye)?$")


# What Google hears instead of a short command, seen in the logs. Only a whole-sentence match
# is replaced, so "the opposite of" in a longer request is left alone.
MISHEARD = {"opposite": "pause it", "positive": "pause it", "a pause it": "pause it", "pozit": "pause it",
            "pass it": "pause it", "boss it": "pause it", "paws it": "pause it", "pause pause it": "pause it"}
MISHEARD.update({"are alert song": "are alerts on", "are alerts song": "are alerts on", "rms on": "are alerts on",
                 "alerts song": "are alerts on", "do them": "undo", "undo them": "undo"})
MISHEARD_START = [(re.compile(r"^forgot\b"), "forget")]
# Inside a sentence: "switch to ip performance", "what proteins do i have"
MISHEARD_WORDS = [(re.compile(r"\b(ip|hi|hai) performance\b"), "high performance"),
                  (re.compile(r"\b(proteins|routings)\b"), "routines")]


def fix_misheard(text):
    text = MISHEARD.get(text, text)
    for pattern, said in MISHEARD_START + MISHEARD_WORDS:
        text = pattern.sub(said, text)
    return text


# Power plans as people say them -> the names system_info.PLANS knows
PLAN_NAMES = {"high performance": "high performance", "performance": "high performance",
              "balanced": "balanced", "normal": "balanced",
              "power saver": "power saver", "power saving": "power saver", "battery saver": "power saver",
              "battery saving": "power saver", "saver": "power saver", "ultimate performance": "ultimate performance"}
_PLANS = "|".join(sorted(PLAN_NAMES, key=len, reverse=True))
_PERIOD = "today|yesterday|this week|last week|last 7 days"


def needs_agent(text):
    if STATUS_ONLY.match(text):
        return False
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
    """'pause the song pause the song' -> 'pause the song'; 'pause the song pause' too."""
    words = text.split()
    if len(words) >= 3 and words[-1] == words[0]:
        words = words[:-1]
        text = " ".join(words)
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
        # Routines run their steps through the same runner and ask through the same confirm
        tools._context["runner"] = self.runner
        tools._context["confirm"] = lambda question: self.confirm(question)
        self.session = None
        self.recent_local = []  # local commands since the last agent request, for context
        self.raw_text = ""      # what was said, before normalize(): memory keeps its capitals and paths
        self.last_fact = None   # the memory "forget that" means
        self.closing_title = None  # (app, window title) when "close lofi music" named a window, not an app
        self.last_played = None    # what "close the song" means
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
            Command("undo", r"^(undo|undo (that|it|this|them|the last (thing|action|one))|take (that|it) back|reverse that)$"
                            r"|^undo (the |my )?(alerts?|power plan|music|song|voice|volume|reminder|memory|change)$",
                    lambda arg: self.runner.undo_last(), help="undo"),
            Command("remember", r"^(remember|dont forget|keep in mind)( that)? (?!to )(?P<arg>.+)$",
                    self.remember, help="remember <something about you>"),
            # "remember to call mom" is a task, not a fact about you
            Command("remember to", r"^(remember|dont forget) to (?P<arg>.+)$", self.remember_to),
            Command("list memories", r"^(what|tell me what|tell me everything)( else)?( do)? you (remember|know)"
                                     r"( about me)?$|^what have i told you( about me)?$|^who am i$"
                                     r"|^(list|show)( me)?( all)?( my| your)? (memories|memory|facts)$"
                                     r"|^(whats|what is) in your memory$|^do you (remember|know) (anything|something)"
                                     r" about me$",
                    self.list_memories, help="what do you know about me"),
            Command("forget all", r"^(forget|erase|wipe) (everything|all)\b.*$"
                                  r"|^(delete|erase|clear|wipe|reset)( all)?( of)?( my| your| the)? (memories|memory)$",
                    self.forget_all, help="forget everything"),
            Command("forget", r"^(forget|delete the memory|remove the memory)( about(?= )| that(?= ))?( (?P<arg>.+))?$",
                    self.forget, help="forget <something> / forget that", ask_text="Forget what?"),
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
                                  r"|^(stop|pause|end) (the |this )?(music|song|video)( now)?$"
                                  r"|^(pause|resume|stop|unpause|play) (it|this|that)( again| now| for (a|one) (sec|second|minute))?$",
                    lambda arg: tool("play_pause"), help="pause / resume"),
            # "close the song": the YouTube tab it plays in, not just a pause
            Command("close song", r"^(close|quit|exit|kill|shut) (the |this |that )?(music|song|video)( tab| now)?$",
                    self.close_song, help="close the song"),
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
            Command("play", r"^play (?P<arg>.+)$", self.play, help="play <song>"),
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
            Command("open folder", r"^(open|show)( me)?( my| the)? (?P<arg>.+? (folder|directory))$"
                                   r"|^open (?P<arg2>[a-z] drive\b.*)$",
                    self.open_folder, help="open my project folder / open D drive coding arc"),
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
                    confirm_text="Close {arg}?", ask_text="Close what?", prepare=self._prepare_close),
            Command("restart", r"^(restart|reopen|relaunch)( (?P<arg>.+))?$",
                    lambda arg: tool("restart_app", name=arg), level=CONTROLLED, help="restart <app>",
                    confirm_text="Restart {arg}?", ask_text="Restart what?",
                    prepare=lambda arg: system_control.check_app(arg)),
            # ---------- Day 3: system intelligence, alerts, routines, activity ----------
            Command("health report", r"^(how is|hows|how s|how are)( my| the)? (pc|computer|laptop|system|machine)"
                                     r"( doing| running| looking| health| holding up)?$"
                                     r"|\b(health report|health check|pc health|computer health|laptop health)\b"
                                     r"|^(health|check up|checkup)$|\bwhy is (my |the )?(pc|computer|laptop) (so )?slow\b",
                    lambda arg: tool("health_report"), help="how's my PC (health report)"),
            Command("uptime", r"\buptime\b|^how long\b.*\b(pc|computer|laptop|system|windows)\b.*\b(on|running|up)$|\bwhen did i (start|turn on|boot) (my |the )?(pc|computer|laptop)\b",
                    lambda arg: tool("uptime"), help="uptime"),
            Command("startup apps", r"\b(startup|start up) (apps|programs|items|list)\b"
                                    r"|\bwhat (starts|runs|opens|launches|loads) (with|when|on) windows\b"
                                    r"|\bwhat (starts|runs|opens) (at|on) (startup|start up|boot)\b",
                    lambda arg: tool("startup_apps"), help="what starts with Windows"),
            Command("set power plan", rf"^(switch|change|set|put|go)( it| my pc| the pc| my laptop| the power plan| power plan"
                                      rf"| the power mode| power mode)?( to| on| into)? (?P<arg>{_PLANS})( mode| plan| power plan)?$"
                                      rf"|^(?P<arg2>{_PLANS}) (mode|plan|power plan)( please| now)?$",
                    lambda arg: tool("set_power_plan", plan=arg), level=CONTROLLED,
                    help="switch to high performance / balanced / power saver",
                    confirm_text="Switch to the {arg} power plan?",
                    prepare=lambda arg: (PLAN_NAMES.get(arg, arg), None)),
            Command("power plan", r"\b(what|which)( is)?( my| the)? (power plan|power mode)\b|^(power plan|power mode)$"
                                  r"|\b(my|current) (power plan|power mode)\b",
                    lambda arg: tool("power_plan"), help="which power plan am I on"),
            Command("alerts", r"^(turn|switch|set) (?P<arg>on|off) (the |my |pc |system )?alerts$"
                              r"|^(turn|switch) (the |my )?alerts (?P<arg2>on|off)$"
                              r"|^(?P<arg3>enable|disable|start|stop) (the |my |pc )?alerts$",
                    self.alerts, help="turn on / off alerts"),
            Command("alerts status", r"^(are|is) (the |my )?alerts (on|off|enabled|running|working)$",
                    lambda arg: Result(True, f"Alerts are {'on' if alerts.running() else 'off'}.")),
            Command("list routines", r"^(what|which) routines (do i have|are there|have i got)$"
                                     r"|^(list|show|tell me)( me)?( all)?( my| the)? routines$|^(my )?routines$",
                    lambda arg: tool("list_routines"), help="what routines do I have"),
            Command("activity apps", rf"^(which|what) apps (did i|have i|i did|i have) (open|opened|use|used)( the)? most( (?P<arg>{_PERIOD}))?$"
                                     rf"|^(what|which) (apps|programs) did i (open|use)( (?P<arg2>{_PERIOD}))?$",
                    lambda arg: tool("activity_summary", period=arg or "this week", focus="apps"),
                    help="which apps did I open most this week"),
            Command("activity songs", rf"^what (songs|music) (did|have) (i|you) (play|played|listen to|listened to)"
                                      rf"( (?P<arg>{_PERIOD}))?$",
                    lambda arg: tool("activity_summary", period=arg or "today", focus="songs")),
            Command("activity", rf"^what (did|have) (i|we|you) (do|done|did)( for me)?( (?P<arg>{_PERIOD}))?$"
                                rf"|^what (did|have) you done for me( (?P<arg2>{_PERIOD}))?$",
                    lambda arg: tool("activity_summary", period=arg or "today"), help="what did I do yesterday"),
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
            # Last, so "what is my cpu usage" and "whats my battery" stay system commands
            Command("recall", r"^(?P<arg4>((what|where|which|who|when|how)( kind of| sort of)?( \w+)? |\w+ )?"
                              r"(do|did|was|am|have) i (keep|put|like|love|prefer|enjoy|listen to|born|live|work|study"
                              r"|learn|use|play|code)\b.*)$"
                              r"|^(what|where|which|who|when)( is| are|s| was| were)? my (?P<arg>.+)$"
                              r"|^do you (remember|know) (what|where|which|who|when) my (?P<arg2>.+?)( is| are)?$"
                              r"|^do you (remember|know) my (?P<arg3>.+)$",
                    self.recall, help="what's my <something>"),
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
                arg = clean_arg(groups.get("arg") or groups.get("arg2") or groups.get("arg3") or groups.get("arg4"))
                return command, arg or None
        return None, None

    def handle(self, raw_text):
        self.raw_text = raw_text
        text = fix_misheard(self.normalize(raw_text))
        log.info("Input: %s", text)

        if not text:
            return Result(True, f"Yes {self.title}?")

        # "coding mode": your own routine phrases come before every built-in command
        routine = routines.find(routines.cached()[0], text)
        if routine:
            return self.run_routine(routine, text)

        # Hybrid routing: simple commands stay local (instant, free, offline),
        # requests with several parts go to the agent
        if self.provider and needs_agent(text) and not MEMORY_COMMAND.match(text):
            return self._ask_agent(raw_text)

        if text.startswith("google ") and system_control.is_app_alias(text):
            # "google chrome" is the app, not a Google search for "chrome"
            text = f"open {text}"

        command, arg = self.match(text)
        if command is None:
            return self._fallback(raw_text, text)

        if command.name in OWN_PRONOUNS:
            pass
        elif arg in PRONOUNS or (arg and arg.split()[0] in PRONOUN_WORDS):
            # "close it" and "close it so i can have my ram" both mean the last app
            arg = self.runner.last_target

        if arg in VAGUE_TARGETS:
            arg = None
        if arg is None and command.ask_text:
            answer = self.ask(command.ask_text) or ""
            arg = self._answer(answer)
            again, again_arg = self.match(arg or "")
            if again is command and again_arg:
                # "Open what?" -> "can you open emu 8086": the answer repeated the command
                arg = again_arg
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
        if command.name == "close" and arg == "terminal":
            if not self.confirm("Close the terminal windows? Not the one I'm running in."):
                return Result(False, "Okay, cancelled.")
            return self._tool("close_terminals")
        if command.name == "close" and self.closing_title:
            return self._close_titled(*self.closing_title)
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
            # So the agent understands "actually close it" after a local "open discord".
            # Memory commands say only what kind they were: the facts themselves reach the
            # brain only through the matched note in _run_agent
            said = f"(a memory command: {command.name})" if command.name in MEMORY_COMMANDS \
                else f"'{text}' -> {result.message}"
            self.recent_local = (self.recent_local + [said])[-5:]
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
        known = self._known_facts(raw_text)
        if known:
            message = f"(Known about the user, in their words: {known})\n" + message
        if self.recent_local:
            message = ("(Commands the user ran without you since your last reply: "
                       + "; ".join(self.recent_local) + ")\n" + message)
            self.recent_local = []

        print("Thinking.....")
        return agent_loop.run(self.session, message, self.runner, self.confirm, trace=self.trace)

    def _answer(self, answer):
        """An answer to a question, cleaned: 'the calculator' -> 'calculator'.
        None when it drops the question ("never mind", "ok bye", silence)."""
        text = self.normalize(answer or "")
        if not text or NEVER_MIND.match(text):
            return None
        return clean_arg(text) or None

    def _known_facts(self, request):
        """The few memories that match this request, for the brain; "" when none match,
        so most requests carry nothing extra."""
        if not self.config.get("MEMORY_IN_AI_REQUESTS", True):
            return ""
        try:
            found = memory_engine.get_memory().related(request, limit=MEMORY_NOTE_FACTS, min_score=MEMORY_NOTE_SCORE)
        except Exception:
            log.exception("Memory lookup for an agent request failed")
            return ""
        return "; ".join(fact.text for _, fact in found)

    # ---------- handlers ----------

    def _prepare_close(self, arg):
        """Like check_app, but "close lofi music" can also mean the window showing it."""
        self.closing_title = None
        if system_control._resolve_app(arg)[0] == "terminal":
            return "terminal", None  # closed window by window in handle(), never Max's own
        key, problem = system_control.check_app(arg, must_be_running=True)
        if problem and (problem.message.startswith("I don't know") or " is a website" in problem.message):
            found = system_control.window_titled(arg)
            if found:
                log.info("Close %r means the %s window %r", arg, *found)
                self.closing_title = found
                return found[0], None
        return key, problem

    def open_folder(self, said):
        """'project folder' -> the folder your memory says; 'd drive coding arc' -> D:\\Coding Arc."""
        path = system_control.spoken_path(said)
        if path is None:
            found = memory_engine.get_memory().find(said, limit=1)
            value = memory_engine.value_of(found[0][1].text) if found else None
            path = system_control.spoken_path(value) if value else None
            if found and path is None:
                return Result(False, f"I remember {memory_engine.as_you(found[0][1].text)}, "
                                     "but I can't find that folder on this PC.")
            if not found:
                if self.provider:
                    return self._ask_agent(self.raw_text)
                return Result(False, f"I don't know where your {said} is. "
                                     f"Say remember my {said} is, then where it is.")
            self.last_fact = found[0][1]
        return self._tool("open_folder", path=path)

    def run_routine(self, routine, text=""):
        """One question for the whole routine (if any step needs one), then every step in order."""
        if routines.needs_approval(routine, self.runner.permissions):
            if not self.confirm(f"Should I run {routine.name}: {routine.describe()}?"):
                return Result(False, "Okay, I didn't run it.")
        result = self._tool("run_routine", name=routine.name)
        if self.provider:
            self.recent_local = (self.recent_local + [f"'{text or routine.name}' -> {result.message}"])[-5:]
        return result

    def alerts(self, word):
        on = word in ("on", "enable", "start")
        return self._tool("set_alerts", on=on)

    def play(self, query):
        result = self._tool("play_music", query=query)
        if result.success:
            self.last_played = query
        return result

    def close_song(self, arg):
        """Close the tab the song plays in: the one named like what Max last played, else any
        YouTube tab. With no such tab (a local file in VLC), pause instead."""
        found = (self.last_played and system_control.window_titled(self.last_played)) \
            or system_control.window_titled("youtube")
        if found and system_control.is_browser(found[0]):
            return self._close_titled(*found)
        return self._tool("play_pause")

    def _close_titled(self, app, title):
        """Close the window found by its title: one tab in a browser, the window anywhere else."""
        self.closing_title = None
        shown = system_control.spoken_title(title)
        browser = system_control.is_browser(app)
        question = f"Close the {shown} tab in {app}?" if browser else f"Close the {app} window {shown}?"
        if not self.confirm(question):
            return Result(False, "Okay, cancelled.")
        if browser:
            return self._tool("close_tab", name=app, title=title)
        return self._tool("close_app", name=app, window=title)

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
        if not answer or NEVER_MIND.match(answer):
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
                query = self._answer(self.ask("What should I look for on your PC?"))
                if not query:
                    return Result(False, "Okay, never mind.")
            return self._tool("search_files", query=query)
        return self._tool("open_web_search", **search_args(query))

    # ---------- memory ----------

    def remember(self, arg):
        found = REMEMBER_RAW.search(self.raw_text)
        fact = (found["fact"] if found else arg).strip()
        fact = re.sub(r"[\s,]+please[.!]*$", "", fact, flags=re.IGNORECASE).strip(" .!")
        if not memory_engine.keywords(fact):
            # "remember my" cut off by the mic saved a fact that just said "My"
            return Result(False, "I didn't catch what to remember. Say remember, then the whole thing.")
        result = self._tool("remember", fact=fact)
        if result.success:
            self.last_fact = memory_engine.get_memory().all()[0]
        return result

    def list_memories(self, arg):
        facts = memory_engine.get_memory().all()
        # After hearing several, "forget that" could mean any of them: Max asks which
        self.last_fact = facts[0] if len(facts) == 1 else None
        return self._tool("list_memories")

    def remember_to(self, task):
        if self.provider:
            return self._ask_agent(self.raw_text)
        return Result(False, f"Do you want a reminder? Say remind me in 10 minutes to {task}.")

    def recall(self, query):
        found = memory_engine.get_memory().find(query, limit=1)
        if not found and self.provider:
            # "what's my ip address" isn't a memory; the agent has tools for it
            return self._ask_agent(self.raw_text)
        result = self._tool("recall", query=query)
        if found:
            self.last_fact = found[0][1]
        return result

    def forget(self, what, asked=False):
        store = memory_engine.get_memory()
        if what in PRONOUNS:
            fact = self.last_fact and store.get(self.last_fact.id)
            if fact:
                return self._forget_facts([fact])
            facts = store.all()
            if not facts:
                return Result(True, "I don't have any memories about you to forget.")
            if len(facts) == 1:
                return self._forget_facts(facts)
            if asked:
                return Result(False, "I'm not sure which memory you mean. Say forget, then what it's about.")
            # "forget that" with nothing said about a memory this run: ask instead of guessing
            answer = self._answer(self.ask("Which one should I forget?"))
            if not answer:
                return Result(False, "Okay, never mind.")
            if re.match(r"^(yes )?(forget )?(all|everything|both)( of (it|them)| of my memories)?$", answer):
                return self.forget_all(None)
            again, again_arg = self.match(answer)
            if again is not None and again.name == "forget" and again_arg:
                answer = again_arg  # "forget my project folder" said as the answer
            return self.forget(answer, asked=True)

        number = re.match(r"^(memory |number |memory number |fact )?(?P<id>\d+)$", what)
        if number:
            # "forget memory 15": the numbers "what do you know about me" prints
            fact = store.get(int(number["id"]))
            if fact is None:
                return Result(False, f"I don't have a memory number {number['id']}.")
            return self._forget_facts([fact])

        facts, missing = self._find_facts(what)
        if not facts:
            return Result(False, f"I don't have anything saved about {what}.")
        result = self._forget_facts(facts)
        if missing and result.success:
            result.message += " I had nothing saved about " + " or ".join(memory_engine.as_you(m) for m in missing) + "."
        return result

    @staticmethod
    def _find_facts(what):
        """The facts 'forget X and Y' means: one per part when the parts name different facts,
        else the one fact the whole sentence matches ("forget that I like rock and roll").
        Returns (facts, parts that matched nothing)."""
        store = memory_engine.get_memory()
        parts = [p.strip() for p in re.split(r"\b(?:and also|and|also)\b", what) if p.strip()]
        if len(parts) > 1:
            picked, missing = [], []
            for part in parts:
                hit = store.find(part, limit=1)
                if not hit:
                    missing.append(part)
                elif hit[0][1].id not in {f.id for f in picked}:
                    picked.append(hit[0][1])
            if len(picked) >= 2:
                return picked, missing
        found = store.find(what, limit=1)
        return ([found[0][1]] if found else []), []

    def _forget_facts(self, facts):
        said = [memory_engine.as_you(f.text) for f in facts]
        if not self.confirm("Should I forget that " + ", and that ".join(said) + "?"):
            return Result(False, "Okay, I'll keep it." if len(facts) == 1 else "Okay, I'll keep them.")
        results = [self._tool("forget_memory", fact_id=f.id) for f in facts]
        self.last_fact = None
        if len(results) == 1:
            return results[0]
        done = [s for s, r in zip(said, results) if r.success]
        if len(done) > 1:
            # One "undo" brings all of them back, not just the last one
            stack = self.runner.undo_stack
            undos = [undo for _, undo in stack[-len(done):]]
            del stack[-len(done):]
            def undo_all():
                for undo in undos:
                    undo()
                return Result(True, f"Okay, I remember those {len(undos)} things again.")
            stack.append(("remember those again", undo_all))
        if not done:
            return results[0]
        return Result(len(done) == len(results), "Okay, I forgot that " + ", and that ".join(done) + ".")

    def forget_all(self, arg):
        count = memory_engine.get_memory().count()
        if not count:
            return Result(True, "I don't have any memories about you to forget.")
        things = f"{count} thing{'s' if count > 1 else ''}"
        if not self.confirm(f"Should I forget everything I know about you? That's {things}."):
            return Result(False, "Okay, I'll keep them.")
        # Critical: asked twice, because it can't be undone
        if not self.confirm("Are you sure? This can't be undone."):
            return Result(False, "Okay, I'll keep them.")
        self.last_fact = None
        return self._tool("forget_all_memories")

    def exit(self, arg):
        return Result(True, f"Goodbye {self.title}.", stop=True)

    def sleep(self, arg):
        return Result(True, f"Okay, going to sleep. Say {self.name} when you need me.", sleep=True)

    def help(self, arg):
        detail = "\n".join(f"  - {c.help}" for c in self.commands if c.help)
        message = ("I can open, close and restart apps, play music, control the volume, check your PC's health, "
                   "remember things about you, run your routines, warn you with alerts, tell you what I did today, "
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
