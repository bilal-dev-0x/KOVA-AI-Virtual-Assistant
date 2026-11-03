from difflib import SequenceMatcher


def _score(query, choice):
    choice = choice.lower()
    # The part before " - " is the main name, e.g. "Pasoori" in "Pasoori - Ali Sethi x Shae Gill"
    main = choice.split(" - ")[0].strip()

    if query == choice or query == main:
        return 1.0
    if len(query) >= 3 and (main.startswith(query) or query in choice):
        return 0.9

    return max(
        SequenceMatcher(None, query, choice).ratio(),
        SequenceMatcher(None, query, main).ratio()
    )


def best_match(query, choices, cutoff=0.6):
    """Return the choice that best matches query, or None if nothing is close enough.

    Forgives small speech-recognition mistakes: "spotifi" finds "spotify",
    "pasoori" finds "Pasoori - Ali Sethi x Shae Gill".
    """
    query = query.lower().strip()
    if not query:
        return None

    best, best_score = None, 0.0
    for choice in choices:
        score = _score(query, choice)
        if score > best_score:
            best, best_score = choice, score

    return best if best_score >= cutoff else None
