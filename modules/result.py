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
    # Set by actions that can be reversed: undo() -> Result, undo_text says what it will do
    undo: Callable = None
    undo_text: str = ""
