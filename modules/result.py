from dataclasses import dataclass


@dataclass
class Result:
    """What every command handler returns.

    message: short text KOVA says out loud (and prints)
    detail:  optional longer text that is only printed
    stop:    True when KOVA should shut down
    sleep:   True when voice mode should go back to waiting for the wake word
    """
    success: bool
    message: str
    detail: str = ""
    stop: bool = False
    sleep: bool = False
