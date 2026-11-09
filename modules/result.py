from dataclasses import dataclass
from typing import Callable


@dataclass
class Result:
    success: bool
    message: str
    detail: str = ""
    stop: bool = False
    sleep: bool = False
    restart: bool = False  # with stop: start again with config.json read fresh
    halt: bool = False     # something looks wrong (the window changed): the agent's whole plan stops here
    # Set by actions that can be reversed: undo() -> Result, undo_text says what it will do
    undo: Callable = None
    undo_text: str = ""
    choices: tuple = ()    # concrete options when a tool asks the user to disambiguate
    resume: dict = None    # optional tool call that can continue when the user picks an option
