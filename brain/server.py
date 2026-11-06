import json
import time
import urllib.error
import urllib.request

from brain.base import BrainError, Provider, Reply, Session, ToolCall
from modules.logger import get_logger

# The KOVA server: the old laptop (server-realm) running Ollama with the kova-cmd model
# (qwen3 4B instruct, made for tool calls). Free and without a daily limit, but slower than
# Gemini: about 45 tokens a second to read and 6 to write. So it gets a short list of tools,
# and Ollama keeps the start of the conversation (system prompt + tools) read between requests.
#
# Reached over the Ethernet cable, with Tailscale as the way round when the cable is out.
# It can't look at pictures: the screen stays with Gemini.

DEFAULT_URLS = ["http://192.168.50.2:11434", "http://100.109.31.1:11434"]  # cable, then Tailscale
DEFAULT_MODEL = "kova-cmd"
CONNECT_SECONDS = 3
ANSWER_SECONDS = 240     # the first answer after the model loads can take a minute or more
MAX_MESSAGES = 10        # older turns are dropped: requests stay small, and a small model
                         # stops copying an old answer ("I can't type" said once was repeated)
# The tools the server is told about (SERVER_TOOLS in config.json replaces this list). Every tool
# description is read with every request, so fewer tools = faster answers.
DEFAULT_TOOLS = ["open_app", "close_app", "close_window", "close_tab", "open_folder", "system_status",
                 "top_processes", "health_report", "set_reminder", "play_music", "play_pause", "volume_up",
                 "volume_down", "get_datetime", "remember", "recall", "find_files", "open_file", "create_item",
                 "rename_item", "delete_items", "move_window", "ui_read", "ui_click", "ui_type", "ui_press_keys",
                 "list_games", "launch_game", "open_project", "git_status",
                 # missing in the voice test: "which apps are open", "what reminders do I have", "close powershell"
                 "list_running_apps", "open_windows", "list_reminders", "cancel_reminder", "side_by_side",
                 "close_terminals"]

# The small model made things up in live tests ("I see the Valorant window", "I'll set a reminder"
# with no tool, "I can't move windows" while it could). These rules are added to its system prompt.
SERVER_RULES = (
    " Rules for you: you cannot see the screen or any window; never describe one unless a tool told you "
    "what is in it. To do something, call its tool now, in this reply; never answer 'I will do it' or "
    "'done' without the tool. You can open, close, move and list apps and windows, set and list reminders, "
    "find files and type in the window in front: use those tools instead of saying you can't. If no tool "
    "fits, say in one short sentence that you can't do that yet."
)

log = get_logger()


class ServerProvider(Provider):
    name = "server"
    can_look = False

    def __init__(self, config):
        self.urls = config.get("SERVER_URLS") or DEFAULT_URLS
        self.model = config.get("SERVER_MODEL") or DEFAULT_MODEL
        self.tool_names = config.get("SERVER_TOOLS") or DEFAULT_TOOLS
        self._url = None  # the address that answered last

    def session(self, system_prompt, tools):
        wanted = set(self.tool_names)
        return ServerSession(self, system_prompt + SERVER_RULES, [t for t in tools if t.name in wanted])

    def warm_up(self, system_prompt, tools):
        """Load the model and have it read the system prompt and tools, writing a single token.
        Ollama keeps what it read, so the next real request starts from there."""
        session = self.session(system_prompt, tools)
        self.chat([{"role": "system", "content": session.system_prompt}], session.tools, options={"num_predict": 1})

    def chat(self, messages, tools, options=None):
        """One /api/chat request. Tries the address that worked last, then the others."""
        body = json.dumps({
            "model": self.model, "messages": messages, "stream": False,
            "think": False,  # qwen3 could think for minutes; a desktop assistant needs the answer
            "tools": [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                        "parameters": t.schema()}} for t in tools],
            **({"options": options} if options else {}),
        }).encode()
        urls = ([self._url] if self._url else []) + [u for u in self.urls if u != self._url]
        for url in urls:
            if not _reachable(url):
                continue
            request = urllib.request.Request(f"{url}/api/chat", data=body, headers={"Content-Type": "application/json"})
            started = time.time()
            try:
                with urllib.request.urlopen(request, timeout=ANSWER_SECONDS) as response:
                    data = json.loads(response.read())
            except urllib.error.HTTPError as e:
                log.error("KOVA server error %s: %s", e.code, e.read()[:200])
                raise BrainError("The KOVA server couldn't answer that.") from e
            except (OSError, ValueError) as e:
                log.warning("KOVA server at %s failed: %s", url, e)
                continue
            self._url = url
            log.info("KOVA server answered in %.1fs (%s tokens read, %s written)", time.time() - started,
                     data.get("prompt_eval_count"), data.get("eval_count"))
            return data.get("message") or {}
        raise BrainError("I can't reach the KOVA server. Is the old laptop on and the cable plugged in?")


def _reachable(url):
    """A quick check that something answers at url, so a dead address costs 3 seconds, not 4 minutes."""
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=CONNECT_SECONDS):
            return True
    except OSError:
        return False


def _reply(message):
    calls = []
    for i, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        args = function.get("arguments") or {}
        if isinstance(args, str):  # some models send the arguments as JSON text
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if function.get("name"):
            calls.append(ToolCall(function["name"], dict(args), call.get("id") or f"call{i}"))
    return Reply(text=str(message.get("content") or "").strip(), calls=calls)


class ServerSession(Session):
    def __init__(self, provider, system_prompt, tools):
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools
        self.messages = []

    def _ask(self):
        # The system prompt always leads, so Ollama can reuse what it already read
        message = self.provider.chat([{"role": "system", "content": self.system_prompt}] + self.messages,
                                     self.tools)
        self.messages.append({"role": "assistant", "content": message.get("content") or "",
                              **({"tool_calls": message["tool_calls"]} if message.get("tool_calls") else {})})
        return _reply(message)

    def _trim(self):
        """Keep the last MAX_MESSAGES, starting at a user message (never half a tool exchange)."""
        if len(self.messages) > MAX_MESSAGES:
            cut = len(self.messages) - MAX_MESSAGES
            while cut < len(self.messages) and self.messages[cut]["role"] != "user":
                cut += 1
            self.messages = self.messages[cut:]

    def send(self, text):
        self.messages.append({"role": "user", "content": text})
        self._trim()
        return self._ask()

    def send_results(self, results):
        for call, result in results:
            self.messages.append({"role": "tool", "tool_name": call.name,
                                  "content": json.dumps(result, ensure_ascii=False)})
        return self._ask()

    def reset(self):
        self.messages = []
