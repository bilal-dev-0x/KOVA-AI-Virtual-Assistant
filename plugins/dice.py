"""Example plugin. Any .py file in plugins/ is loaded at startup, and each
@tool function in it becomes something Max (through Gemini) can do.
Files starting with _ are skipped."""
import random

from modules.result import Result
from modules.tool_registry import tool


@tool("Flip a coin.")
def flip_coin():
    return Result(True, f"It's {random.choice(['heads', 'tails'])}.")


@tool("Roll dice and add them up.", params={"sides": "Sides per die (default 6)", "count": "How many dice (1-10)"})
def roll_dice(sides: int = 6, count: int = 1):
    sides, count = max(2, min(sides, 1000)), max(1, min(count, 10))
    rolls = [random.randint(1, sides) for _ in range(count)]
    if count == 1:
        return Result(True, f"You rolled {rolls[0]}.")
    return Result(True, f"You rolled {', '.join(map(str, rolls))}, total {sum(rolls)}.")
