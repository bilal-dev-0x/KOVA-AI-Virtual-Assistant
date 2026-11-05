import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from modules.logger import get_logger

# Max's long-term memory: facts and preferences you told it, kept in one SQLite
# file on this PC (data/ is gitignored). Nothing here goes online.

BASE_DIR = Path(__file__).resolve().parent.parent
DB_FILE = BASE_DIR / "data" / "memory.db"

log = get_logger()

SECRET_MESSAGE = "I don't store passwords, PINs, keys or card numbers."

# Words that mean the fact is a secret, wherever they appear
SECRET_WORDS = re.compile(
    r"\b(pass ?words?|passcodes?|passphrases?|pin (code|number)s?|(my|the|atm|sim|phone|card|bank) pin|"
    r"otps?|one time (code|password)|cvv|cvc|"
    r"api ?keys?|secret (key|code)s?|private keys?|(access|auth|api|bearer|github|discord|bot) tokens?|"
    r"token is|seed phrase|recovery (phrase|code)s?|"
    r"(credit|debit|bank|atm) cards?|card numbers?|account numbers?|iban|routing number|"
    r"ssn|social security|cnic|passport number)\b",
    re.IGNORECASE,
)
# Values that look like a secret even without those words
SECRET_VALUES = [
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),            # Google API key
    re.compile(r"\b(sk|pk|rk|ghp|gho|xox[abp])[-_][0-9A-Za-z_\-]{16,}"),  # common key prefixes
    re.compile(r"(?<!\d)(\d[ \-]?){12,19}(?!\d)"),       # card, CNIC or account number
    re.compile(r"\b[0-9A-Za-z+/]{32,}={0,2}(?![0-9A-Za-z])"),  # long random-looking strings
]

# Facts that say what you like or how you want things are preferences
PREFERENCE = re.compile(r"^(i (really )?(like|love|prefer|hate|dislike|enjoy|want|always|never|usually)"
                        r"|i dont like|i do not like|my favou?rite)\b", re.IGNORECASE)
# "my project folder is X": the part before "is" names what the fact is about
TOPIC = re.compile(r"^(?P<topic>.+?) (is|are|=|equals) .+$", re.IGNORECASE)
WORD = re.compile(r"[a-z0-9]+")
STOP_WORDS = {"i", "my", "me", "is", "are", "the", "a", "an", "to", "of", "and", "in", "on", "at", "for",
              "it", "its", "that", "this", "what", "whats", "where", "wheres", "do", "you", "your", "when",
              "with", "be", "am", "was", "about", "know", "remember", "which", "who", "how", "does", "did",
              "can", "tell", "please", "there", "have", "has", "any", "anything"}


class SecretError(ValueError):
    """The text looks like a password, key or card number, so it was not saved."""


@dataclass
class Fact:
    id: int
    text: str
    kind: str        # "fact" or "preference"
    created: float
    updated: float
    uses: int = 0


def looks_secret(text):
    return bool(SECRET_WORDS.search(text)) or any(p.search(text) for p in SECRET_VALUES)


def kind_of(text):
    return "preference" if PREFERENCE.search(_key(text)) else "fact"


def _clean(text):
    text = " ".join(str(text).split()).strip(" .")
    return text[:1].upper() + text[1:]


def _key(text):
    return " ".join(WORD.findall(text.lower().replace("'", "").replace("’", "")))


def topic_of(text):
    """'My project folder is D:\\Code' -> 'my project folder'; None if the fact has no 'X is Y'."""
    match = TOPIC.match(_key(text))
    if not match:
        return None
    topic = match["topic"].strip()
    # "I am..." / "it is..." say too little to replace an older fact
    return topic if len(topic.split()) >= 2 or topic not in STOP_WORDS else None


VALUE = re.compile(r"\s(is|are|=|equals)\s+(?P<value>.+)$", re.IGNORECASE)


def value_of(text):
    """'My project folder is D:\\Code' -> 'D:\\Code'; None when the fact has no 'X is Y'."""
    if topic_of(text) is None:
        return None
    match = VALUE.search(text)
    return match["value"].strip(" .") if match else None


def keywords(text):
    return {w for w in WORD.findall(_key(text)) if w not in STOP_WORDS}


class Memory:
    """The fact store. Opens the database on first use, so startup stays fast."""

    def __init__(self, path=None):
        self.path = Path(path or DB_FILE)
        self._conn = None
        self._lock = threading.Lock()

    def _db(self):
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS facts ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " text TEXT NOT NULL,"
                " key TEXT NOT NULL,"
                " topic TEXT,"
                " kind TEXT NOT NULL DEFAULT 'fact',"
                " created REAL NOT NULL,"
                " updated REAL NOT NULL,"
                " uses INTEGER NOT NULL DEFAULT 0)")
            self._conn.commit()
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _fact(row):
        return Fact(*row)

    def _select(self, where="", params=()):
        rows = self._db().execute(
            f"SELECT id, text, kind, created, updated, uses FROM facts {where}", params).fetchall()
        return [self._fact(r) for r in rows]

    def remember(self, text, kind=None, now=None):
        """Save a fact. Returns (fact, replaced): replaced is the older fact it updated, or None.

        The same fact said again, or a new value for the same topic ("my project folder is ..."),
        updates the old one instead of adding a copy. Secrets raise SecretError.
        """
        text = _clean(text)
        if not text:
            raise ValueError("There's nothing to remember.")
        if looks_secret(text):
            log.info("Memory refused a secret-looking fact")
            raise SecretError(SECRET_MESSAGE)

        now = time.time() if now is None else now
        key, topic, kind = _key(text), topic_of(text), kind or kind_of(text)
        with self._lock:
            old = self._select("WHERE key = ? OR (topic IS NOT NULL AND topic = ?) ORDER BY updated DESC LIMIT 1",
                               (key, topic))
            db = self._db()
            if old:
                replaced = old[0]
                db.execute("UPDATE facts SET text = ?, key = ?, topic = ?, kind = ?, updated = ? WHERE id = ?",
                           (text, key, topic, kind, now, replaced.id))
                fact_id = replaced.id
            else:
                replaced = None
                fact_id = db.execute(
                    "INSERT INTO facts (text, key, topic, kind, created, updated) VALUES (?, ?, ?, ?, ?, ?)",
                    (text, key, topic, kind, now, now)).lastrowid
            db.commit()
            fact = self._select("WHERE id = ?", (fact_id,))[0]
        log.info("Memory %s fact %s", "updated" if replaced else "saved", fact.id)
        return fact, replaced

    def all(self):
        """Every fact, newest first."""
        with self._lock:
            return self._select("ORDER BY updated DESC, id DESC")

    def get(self, fact_id):
        with self._lock:
            found = self._select("WHERE id = ?", (fact_id,))
        return found[0] if found else None

    def count(self):
        with self._lock:
            return self._db().execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    def find(self, query, limit=3, min_score=0.34):
        """Facts sharing words with the query, best first, as [(score, fact)].

        Score = the share of the query's keywords found in the fact. Step 3 of Day 3
        adds fuzzy and meaning-based matching on top of this.
        """
        wanted = keywords(query)
        if not wanted:
            return []
        scored = []
        for fact in self.all():
            score = len(wanted & keywords(fact.text)) / len(wanted)
            if score >= min_score:
                scored.append((round(score, 3), fact))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].updated))
        return scored[:limit]

    def used(self, facts):
        """Count a recall, so often-used facts can be preferred later."""
        ids = [(f.id,) for f in facts]
        if not ids:
            return
        with self._lock:
            self._db().executemany("UPDATE facts SET uses = uses + 1 WHERE id = ?", ids)
            self._db().commit()

    def forget(self, fact_id):
        """Delete one fact. Returns it, or None if there was no such fact."""
        with self._lock:
            found = self._select("WHERE id = ?", (fact_id,))
            if not found:
                return None
            self._db().execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self._db().commit()
        log.info("Memory forgot fact %s", fact_id)
        return found[0]

    def forget_all(self):
        """Delete every fact. Returns how many there were."""
        with self._lock:
            count = self._db().execute("DELETE FROM facts").rowcount
            self._db().commit()
        log.info("Memory forgot all %s facts", count)
        return count


    def restore(self, fact):
        """Put a forgotten fact back as it was (used by undo)."""
        with self._lock:
            self._db().execute(
                "INSERT OR REPLACE INTO facts (id, text, key, topic, kind, created, updated, uses)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (fact.id, fact.text, _key(fact.text), topic_of(fact.text), fact.kind,
                 fact.created, fact.updated, fact.uses))
            self._db().commit()
        return fact


# How Max says your facts back to you: "I like lo-fi" -> "you like lo-fi"
_YOU = {"i": "you", "im": "you're", "i'm": "you're", "me": "you", "my": "your", "mine": "yours",
        "myself": "yourself", "ive": "you've", "i've": "you've", "id": "you'd", "i'd": "you'd",
        "ill": "you'll", "i'll": "you'll"}


def as_you(text):
    """'I am happy with my setup' -> 'you are happy with your setup'."""
    words = text.split(" ")
    out = []
    for i, word in enumerate(words):
        core = word.rstrip(".,!?;:")
        tail = word[len(core):]
        low = core.lower().replace("’", "'")
        if low in _YOU and (low != "id" or core == "I'd" or core == "Id"):
            core = _YOU[low]
        elif low == "am" and i and words[i - 1].lower() == "i":
            core = "are"
        elif low == "was" and i and words[i - 1].lower() == "i":
            core = "were"
        out.append(core + tail)
    said = " ".join(out)
    # "The router is..." reads as "...that the router is"; names like "Bilal" keep their capital
    first = said.split(" ")[0]
    return said[:1].lower() + said[1:] if first.lower() in _LOWER_FIRST else said


_LOWER_FIRST = {"the", "a", "an", "it", "its", "this", "that", "these", "those", "our", "we", "there", "when",
                "every", "on", "in", "at", "for"}


_memory = None


def get_memory():
    """The shared store for this run of Max."""
    global _memory
    if _memory is None or _memory.path != Path(DB_FILE):
        _memory = Memory(DB_FILE)
    return _memory
