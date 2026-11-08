# The small helpers both dashboards (classic and the PyWebView page) share,
# so the tests use them without ever opening a window.

CHAT_KEEP = 200       # chat bubbles kept before the oldest are dropped
TRACE_KEEP = 500      # trace lines kept


def answer_lines(result):
    """What the chat shows for one answer: the message, then any detail."""
    lines = [result.message] if result.message else []
    lines += [line for line in (result.detail or "").splitlines() if line.strip()]
    return lines


def percent(text):
    """'71%' or '100% charging' -> 0.71 / 1.0 for a progress bar; None when unknown."""
    try:
        return max(0.0, min(1.0, float(str(text).split("%")[0]) / 100))
    except (TypeError, ValueError):
        return None
