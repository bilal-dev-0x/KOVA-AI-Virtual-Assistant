from modules import memory_engine
from modules.logger import get_logger
from modules.result import Result

# The Memory tab's logic, kept out of the widgets so it can be tested without a
# window. Deleting goes through the existing forget_memory tool — the same
# permission check, the same approval pop-up and the same line in
# logs/actions.jsonl as saying "forget memory number 3" out loud. The database
# itself is never edited here.

log = get_logger()


def rows():
    """Every fact as (id, text) for the list; [] when nothing is remembered yet."""
    return [(fact.id, fact.text) for fact in memory_engine.get_memory().all()]


def line(fact_id, text):
    """One row: '#3  I like lo-fi when coding'."""
    return f"#{fact_id}  {text}"


def delete(agent, fact_id):
    """Delete one memory the safe way. Runs on the worker thread, because the
    approval pop-up may have to be shown first."""
    tool, clean, problem = agent.runner.prepare("forget_memory", {"fact_id": fact_id})
    if problem:
        return problem
    if agent.runner.permissions.needs_approval(tool):
        if not agent.confirm(f"Should I {tool.describe(clean)}?"):
            log.info("Memory %s kept: not approved", fact_id)
            return Result(False, "Okay, cancelled.")
    return agent.runner.run(tool, clean, "confirmed", source="local")
