import importlib.util
import inspect
import os
from dataclasses import dataclass
from typing import Callable

from modules.logger import get_logger

# Every ability Max has is a tool: a plain function plus what the AI needs to
# know to call it safely. Local commands and Gemini run the same tools.
#
# Safety levels from the roadmap:
#   safe       - runs straight away
#   controlled - asks first (one approval covers a whole batch of steps)
#   critical   - asks again right before each step, never "always allowed"
SAFE, CONTROLLED, CRITICAL = "safe", "controlled", "critical"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(BASE_DIR, "plugins")
TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}

log = get_logger()
REGISTRY = {}
_plugins_loaded = False


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    params: dict             # name -> {"type", "description", "required", "enum"}
    level: str = SAFE
    confirm_text: str = ""   # how the action is described when asking, e.g. "close {name}", or a function of args
    check: Callable = None   # check(**args) -> Result explaining why it can't run, or None

    def schema(self):
        """JSON schema of the arguments, as AI function calling expects it."""
        properties = {}
        for name, p in self.params.items():
            properties[name] = {"type": p["type"], "description": p["description"]}
            if p["enum"]:
                properties[name]["enum"] = list(p["enum"])
        return {
            "type": "object",
            "properties": properties,
            "required": [name for name, p in self.params.items() if p["required"]],
        }

    def describe(self, args):
        """'close discord', used in approval questions and the trace."""
        if self.confirm_text:
            try:
                if callable(self.confirm_text):
                    # Some questions depend on which arguments were given (close a whole app or one window)
                    return self.confirm_text(args)
                return self.confirm_text.format(**args)
            except (KeyError, IndexError):
                pass
        shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{self.name.replace('_', ' ')}" + (f" ({shown})" if shown else "")

    def validate(self, args):
        """Check arguments that came from the AI. Returns (clean_args, error)."""
        args = dict(args or {})
        unknown = sorted(set(args) - set(self.params))
        if unknown:
            return None, f"unknown argument(s): {', '.join(unknown)}"

        clean = {}
        for name, p in self.params.items():
            if args.get(name) is None:
                if p["required"]:
                    return None, f"missing argument: {name}"
                continue
            value, error = _cast(args[name], p["type"])
            if error:
                return None, f"{name} {error}"
            if p["enum"] and value not in p["enum"]:
                return None, f"{name} must be one of: {', '.join(map(str, p['enum']))}"
            clean[name] = value
        return clean, None


def _cast(value, kind):
    if isinstance(value, (dict, list)):
        return None, f"must be a {kind}"
    if kind == "string":
        value = str(value).strip()
        return (value, None) if value else (None, "must not be empty")
    if kind == "boolean":
        return (value, None) if isinstance(value, bool) else (None, "must be true or false")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"must be a {kind}"
    if kind == "integer":
        if not number.is_integer():
            return None, "must be a whole number"
        return int(number), None
    return number, None


def tool(description, params=None, level=SAFE, confirm_text="", check=None, name=None):
    """Register a function as a tool.

    Argument types come from the type hints (str, int, float, bool); arguments
    without a default are required. params maps an argument to its description,
    or to (description, [allowed values]).
    """
    def register(func):
        spec = {}
        for pname, p in inspect.signature(func, eval_str=True).parameters.items():
            doc = (params or {}).get(pname, "")
            enum = None
            if isinstance(doc, tuple):
                doc, enum = doc
            annotation = str if p.annotation is inspect.Parameter.empty else p.annotation
            spec[pname] = {
                "type": TYPES[annotation],
                "description": doc,
                "required": p.default is inspect.Parameter.empty,
                "enum": enum,
            }
        t = Tool(name or func.__name__, description, func, spec, level, confirm_text, check)
        REGISTRY[t.name] = t
        func.tool = t
        return func
    return register


def get(name):
    return REGISTRY.get(name)


def all_tools():
    return list(REGISTRY.values())


def load_plugins(folder=None):
    """Load every .py file in plugins/; their @tool functions register themselves.

    A broken plugin is logged and skipped, so it can't stop Max from starting.
    """
    global _plugins_loaded
    folder = folder or PLUGIN_DIR
    loaded = []
    if not os.path.isdir(folder):
        return loaded

    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        path = os.path.join(folder, filename)
        try:
            spec = importlib.util.spec_from_file_location(f"kova_plugin_{filename[:-3]}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(filename)
        except Exception as e:
            log.error("Plugin %s failed to load: %s", filename, e)
            print(f"⚠️ Plugin {filename} failed to load: {e}")

    _plugins_loaded = True
    log.info("Loaded plugins: %s", ", ".join(loaded) or "none")
    return loaded


def load_plugins_once():
    if not _plugins_loaded:
        load_plugins()
