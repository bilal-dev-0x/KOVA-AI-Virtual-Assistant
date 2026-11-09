import base64
import json
import time
import urllib.error
import urllib.request

from brain.base import LOOK_PROMPT, BrainError, Provider, Reply, Session, ToolCall
from modules.logger import get_logger

# OpenRouter: one key, many cloud models behind the OpenAI chat format. KOVA uses it as the
# second brain, after Gemini and before the KOVA server: when Gemini's free requests run out,
# OpenRouter answers in seconds instead of the server's half a minute.
#
# The default models are free ones that call tools and see pictures. Free models come and go,
# so OPENROUTER_MODELS in config.json replaces the list, and a model that is busy, gone or out
# of free requests hands the request to the next one. The key lives only in config.json: it is
# sent in the Authorization header and never logged.

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = ["google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-super-vl:free"]
ANSWER_SECONDS = 60      # a free model that was asleep took 41 s for its first answer (live, 9 Nov)
MAX_MESSAGES = 20
COOLDOWN_SECONDS = 120
SKIP_CODES = {402, 404, 408, 429, 500, 502, 503, 504}   # try the next model

log = get_logger()

PLACEHOLDER_KEYS = {"", "PASTE_YOUR_OPENROUTER_API_KEY_HERE"}


def is_available(config):
    return str(config.get("OPENROUTER_API_KEY", "") or "").strip() not in PLACEHOLDER_KEYS


class OpenRouterProvider(Provider):
    name = "openrouter"
    can_look = True

    def __init__(self, config, opener=None):
        self.key = str(config.get("OPENROUTER_API_KEY", "")).strip()
        models = config.get("OPENROUTER_MODELS") or ([config["OPENROUTER_MODEL"]] if config.get("OPENROUTER_MODEL")
                                                     else []) + DEFAULT_MODELS
        self.models = list(dict.fromkeys(models))
        self._open = opener or urllib.request.urlopen
        self._cooldown = {}

    def session(self, system_prompt, tools):
        return OpenRouterSession(self, system_prompt, tools)

    def chat(self, messages, tools=()):
        """One answer from the first model that takes it. Returns the assistant message dict."""
        now = time.time()
        tried = []
        for model in self.models:
            if self._cooldown.get(model, 0) > now:
                continue
            tried.append(model)
            body = {"model": model, "messages": messages}
            if tools:
                body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                   "parameters": t.schema()}} for t in tools]
            request = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.key}",
                "HTTP-Referer": "https://github.com/bilal-dev-0x/KOVA-AI-Virtual-Assistant", "X-Title": "KOVA"})
            started = time.time()
            try:
                with self._open(request, timeout=ANSWER_SECONDS) as response:
                    data = json.loads(response.read())
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise BrainError("OpenRouter refused the API key. Check it in Settings.") from e
                log.warning("OpenRouter %s answered %s: %s", model, e.code, _short(e))
                if e.code in SKIP_CODES:
                    self._cooldown[model] = time.time() + COOLDOWN_SECONDS
                    continue
                raise BrainError("OpenRouter couldn't answer that.") from e
            except (OSError, ValueError) as e:
                log.warning("OpenRouter %s failed: %s", model, e)
                self._cooldown[model] = time.time() + COOLDOWN_SECONDS
                continue
            if data.get("error"):
                log.warning("OpenRouter %s error: %s", model, str(data["error"])[:200])
                self._cooldown[model] = time.time() + COOLDOWN_SECONDS
                continue
            choices = data.get("choices") or []
            if not choices:
                self._cooldown[model] = time.time() + COOLDOWN_SECONDS
                continue
            log.info("OpenRouter (%s) answered in %.1fs", model, time.time() - started)
            return choices[0].get("message") or {}
        if not tried:
            raise BrainError("OpenRouter's models are resting for a minute. Try again shortly.")
        raise BrainError("OpenRouter can't answer right now.")

    def look(self, image, question, context="", read=None):
        picture = "data:image/jpeg;base64," + base64.b64encode(image).decode()
        text = (f"{context}\n" if context else "") + question
        message = self.chat([{"role": "system", "content": LOOK_PROMPT},
                             {"role": "user", "content": [{"type": "text", "text": text},
                                                          {"type": "image_url", "image_url": {"url": picture}}]}])
        answer = str(message.get("content") or "").strip()
        if not answer:
            raise BrainError("OpenRouter couldn't read the screen.")
        return answer


def _short(error):
    try:
        return error.read()[:200]
    except Exception:
        return ""


def _reply(message):
    calls = []
    for i, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        args = function.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {}
        if function.get("name"):
            calls.append(ToolCall(function["name"], dict(args) if isinstance(args, dict) else {},
                                  call.get("id") or f"call{i}"))
    return Reply(text=str(message.get("content") or "").strip(), calls=calls)


class OpenRouterSession(Session):
    def __init__(self, provider, system_prompt, tools):
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools
        self.messages = []

    def _ask(self):
        message = self.provider.chat([{"role": "system", "content": self.system_prompt}] + self.messages,
                                     self.tools)
        kept = {"role": "assistant", "content": message.get("content") or ""}
        if message.get("tool_calls"):
            kept["tool_calls"] = message["tool_calls"]
        self.messages.append(kept)
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
            self.messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                  "content": json.dumps(result, ensure_ascii=False)})
        return self._ask()

    def reset(self):
        self.messages = []
