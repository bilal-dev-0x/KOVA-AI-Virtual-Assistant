import time

from brain.base import LOOK_PROMPT, BrainError, Provider, Reply, Session, ToolCall
from brain.budget import Budget
from modules.logger import get_logger

# The google-genai library is imported on the first request, never at startup.

ATTEMPTS = 2
MAX_TURNS = 30  # start a fresh conversation after this many requests, to keep requests small
COOLDOWN_SECONDS = 120  # a busy or rate-limited model is skipped this long
# The free tier gives each model its own small daily quota (gemini-3.6-flash: 20 requests),
# so when one runs out Max moves on to the next. GEMINI_MODELS in config.json overrides this.
FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash",
                   "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]

log = get_logger()


def _quota_error(error):
    """Why a model can't take the request: 'gone' (retired), 'day' (daily quota used up),
    'busy' (overloaded or per-minute limit), or None for any other error."""
    text = str(error)
    if "404" in text and "NOT_FOUND" in text:
        return "gone"
    if any(code in text for code in ("503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED")):
        return "busy"
    if "429" not in text and "RESOURCE_EXHAUSTED" not in text:
        return None
    return "day" if ("per day" in text.lower() or "PerDay" in text) else "busy"


class GeminiProvider(Provider):
    name = "gemini"
    can_look = True

    def __init__(self, config):
        self.config = config
        models = config.get("GEMINI_MODELS") or [config.get("GEMINI_MODEL", "gemini-3.6-flash")] + FALLBACK_MODELS
        self.models = list(dict.fromkeys(models))  # drop duplicates, keep the order
        self.budget = Budget(config.get("GEMINI_DAILY_LIMIT", 100))
        self._client = None  # must stay referenced, or the SDK closes its connection
        self.search_available = True
        # Gemini 3 thinks hard by default, which made simple answers take 20 seconds.
        # A desktop assistant needs fast short plans more than deep reasoning.
        self.thinking = config.get("GEMINI_THINKING", "low")
        self._cooldown = {}  # model -> time it may be tried again

    def client(self):
        if self._client is None:
            from google import genai
            from google.genai import types
            self._client = genai.Client(
                api_key=self.config["GEMINI_API_KEY"],
                http_options=types.HttpOptions(timeout=60_000)
            )
        return self._client

    def current_model(self, skip=()):
        now = time.time()
        cooling = {m for m, until in self._cooldown.items() if until > now}
        for model in self.models:
            if model not in skip and model not in cooling and not self.budget.is_exhausted(model):
                return model
        if skip or cooling:
            raise BrainError("Gemini is busy right now. Give me a minute and try again.")
        raise BrainError("Every Gemini model I can use has run out of free requests for today. "
                         "Local commands still work.")

    def request(self, call):
        """Run call(model) with the budget check and one retry. A model that is used up
        for today, retired, overloaded or rate-limited hands the request to the next one."""
        self.budget.spend()
        attempt = 1
        busy = set()
        while True:
            model = self.current_model(skip=busy)
            try:
                return call(model)
            except BrainError:
                raise
            except Exception as e:
                quota = _quota_error(e)
                if quota in ("day", "gone"):
                    self.budget.mark_exhausted(model)
                    log.warning("%s %s, trying the next model", model,
                                "used up its daily quota" if quota == "day" else "is no longer available")
                    if quota == "day":
                        print(f"⚠️ {model} has no free requests left today, switching model.")
                    continue
                if quota == "busy":
                    log.warning("%s is busy or rate-limited, trying the next model", model)
                    busy.add(model)
                    self._cooldown[model] = time.time() + COOLDOWN_SECONDS
                    continue
                log.error("Gemini request failed (attempt %s/%s): %s", attempt, ATTEMPTS, e)
                if attempt == ATTEMPTS:
                    raise BrainError("I couldn't reach Gemini right now.") from e
                attempt += 1
                time.sleep(1)

    def thinking_config(self):
        from google.genai import types
        return types.ThinkingConfig(thinking_level=self.thinking) if self.thinking else None

    def session(self, system_prompt, tools):
        return GeminiSession(self, system_prompt, tools)

    def search(self, query):
        """Answer with Google Search grounding, for facts newer than the model.

        Returns None when grounding isn't available: the free tier gives it a quota of 0,
        so after the first refusal Max stops trying for the rest of the run.
        """
        if not self.search_available:
            return None
        from google.genai import types
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            system_instruction="Answer with current facts from the search, briefly, as 2-4 plain "
                               "spoken sentences without markdown or links.",
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=self.thinking_config(),
        )
        try:
            response = self.request(lambda model: self.client().models.generate_content(
                model=model, contents=query, config=config))
        except BrainError as e:
            log.warning("Google Search grounding unavailable: %s", e)
            self.search_available = False
            return None
        return _text(response) or None

    def look(self, image, question, context="", read=None):
        """One request with the picture and the question; costs one request of the daily quota."""
        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=LOOK_PROMPT,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=self.thinking_config(),
        )
        text = f"Earlier about this picture:\n{context}\n\nNow: {question}" if context else question
        contents = [types.Part.from_bytes(data=image, mime_type="image/jpeg"), text]
        response = self.request(lambda model: self.client().models.generate_content(
            model=model, contents=contents, config=config))
        return _text(response)


def _text(response):
    """The answer text, skipping thoughts and function calls (response.text warns about those)."""
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return ""
    return "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)).strip()


class GeminiSession(Session):
    def __init__(self, provider, system_prompt, tools):
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = tools
        self._chat = None
        self._chat_model = None
        self.turns = 0

    def _get_chat(self, model):
        if self._chat is not None and self._chat_model == model:
            return self._chat

        from google.genai import types
        # After a model switch the conversation so far moves to the new model
        history = self._chat.get_history() if self._chat is not None else None
        declarations = [
            types.FunctionDeclaration(name=t.name, description=t.description,
                                      parameters_json_schema=t.schema())
            for t in self.tools
        ]
        self._chat = self.provider.client().chats.create(
            model=model,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=[types.Tool(function_declarations=declarations)],
                # KOVA runs the tools itself, after checking permissions
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=self.provider.thinking_config(),
            ),
            history=history,
        )
        self._chat_model = model
        return self._chat

    def _send(self, message):
        response = self.provider.request(lambda model: self._get_chat(model).send_message(message))
        calls = [ToolCall(fc.name, dict(fc.args or {}), fc.id) for fc in (response.function_calls or [])]
        return Reply(text=_text(response), calls=calls)

    def send(self, text):
        if self.turns >= MAX_TURNS:
            self.reset()
        self.turns += 1
        return self._send(text)

    def send_results(self, results):
        from google.genai import types
        parts = [
            types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=result))
            for call, result in results
        ]
        return self._send(parts)

    def reset(self):
        self._chat = None
        self._chat_model = None
        self.turns = 0
