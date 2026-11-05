import pytest

from modules import memory_engine
from modules.memory_engine import Memory, SecretError, kind_of, looks_secret, topic_of


@pytest.fixture
def memory(tmp_path):
    store = Memory(tmp_path / "memory.db")
    yield store
    store.close()


def test_database_is_not_created_until_first_use(tmp_path):
    store = Memory(tmp_path / "data" / "memory.db")
    assert not (tmp_path / "data").exists()
    assert store.all() == []
    assert (tmp_path / "data" / "memory.db").exists()
    store.close()


def test_remember_and_list(memory):
    fact, replaced = memory.remember("  i like lo-fi when coding. ")
    assert replaced is None
    assert fact.text == "I like lo-fi when coding"
    assert fact.kind == "preference"
    memory.remember("My project folder is D drive coding arc", now=fact.updated + 5)
    assert [f.text for f in memory.all()] == ["My project folder is D drive coding arc", "I like lo-fi when coding"]
    assert memory.count() == 2


def test_facts_survive_a_restart(tmp_path):
    first = Memory(tmp_path / "memory.db")
    first.remember("I like lo-fi when coding")
    first.close()
    second = Memory(tmp_path / "memory.db")
    assert [f.text for f in second.all()] == ["I like lo-fi when coding"]
    second.close()


def test_saying_the_same_fact_again_does_not_copy_it(memory):
    first, _ = memory.remember("I like lo-fi when coding", now=100)
    again, replaced = memory.remember("i like Lo-Fi when coding!", now=200)
    assert replaced.id == first.id and again.id == first.id
    assert memory.count() == 1
    assert again.updated == 200 and again.created == 100


def test_a_new_value_for_the_same_topic_replaces_the_old_one(memory):
    old, _ = memory.remember("My project folder is E:\\old")
    new, replaced = memory.remember("my project folder is D:\\Coding Arc")
    assert replaced.text == "My project folder is E:\\old"
    assert new.id == old.id
    assert [f.text for f in memory.all()] == ["My project folder is D:\\Coding Arc"]


def test_different_topics_are_kept_apart(memory):
    memory.remember("My project folder is D drive")
    memory.remember("My music folder is E drive")
    memory.remember("I like lo-fi")
    memory.remember("I like rock")
    assert memory.count() == 4


@pytest.mark.parametrize("text, topic", [
    ("My project folder is D:\\Code", "my project folder"),
    ("My name's Bilal", None),
    ("My name is Bilal", "my name"),
    ("I am a student", None),
    ("I like lo-fi when coding", None),
])
def test_topic_of(text, topic):
    assert topic_of(text) == topic


@pytest.mark.parametrize("text, kind", [
    ("I like lo-fi when coding", "preference"),
    ("I don't like loud alarms", "preference"),
    ("My favourite game is Valorant", "preference"),
    ("I always want the dark theme", "preference"),
    ("My project folder is D drive", "fact"),
    ("My sister lives in Lahore", "fact"),
])
def test_kind_of(text, kind):
    assert kind_of(text) == kind


@pytest.mark.parametrize("text", [
    "my password is hunter2",
    "my wifi pass word is cats",
    "my ATM pin is 4321",
    "my pin code is 1234",
    "my wifi pin is 4455",
    "the door pin: 9921",
    "the OTP was 552211",
    "my api key is abc",
    "my github token is xyz",
    "card number 4111 1111 1111 1111",
    "my cnic is 35202-1234567-1",
    "AIzaSyA1234567890abcdefghijklmnopqrstu",
    "use sk-abcdefghijklmnop1234567890",
    "my bank account number is 0012",
    "my seed phrase is apple banana",
])
def test_secrets_are_refused_and_never_written(memory, text):
    assert looks_secret(text)
    with pytest.raises(SecretError, match="don't store"):
        memory.remember(text)
    assert memory.count() == 0


@pytest.mark.parametrize("text", [
    "I like lo-fi when coding",
    "My project folder is D:\\Coding Arc\\GitHub Repos",
    "I pin my favourite tabs in Chrome",
    "My phone number is 0300 1234567",
    "My phone number is +92 300 1234567",
    "I play with game tokens on weekends",
    "My birthday is 12 March 2001",
])
def test_ordinary_facts_are_not_mistaken_for_secrets(text):
    assert not looks_secret(text)


def test_empty_text_is_refused(memory):
    with pytest.raises(ValueError):
        memory.remember("  . ")


def test_find_by_shared_words(memory):
    memory.remember("My project folder is D drive coding arc")
    memory.remember("I like lo-fi when coding")
    memory.remember("My sister lives in Lahore")
    found = memory.find("what's my project folder?")
    assert [f.text for _, f in found] == ["My project folder is D drive coding arc"]
    assert found[0][0] == 1.0
    assert [(s, f.text) for s, f in memory.find("which city is my sister in")] == [(0.5, "My sister lives in Lahore")]
    assert memory.find("what's the weather") == []
    assert memory.find("what is my") == []


def test_used_counts_recalls(memory):
    fact, _ = memory.remember("I like lo-fi")
    memory.used([fact])
    memory.used([fact])
    assert memory.get(fact.id).uses == 2


def test_forget_one(memory):
    keep, _ = memory.remember("I like lo-fi")
    drop, _ = memory.remember("My project folder is D drive")
    assert memory.forget(drop.id).text == "My project folder is D drive"
    assert memory.forget(drop.id) is None
    assert [f.id for f in memory.all()] == [keep.id]


def test_forget_all(memory):
    memory.remember("I like lo-fi")
    memory.remember("My project folder is D drive")
    assert memory.forget_all() == 2
    assert memory.all() == []


def test_shared_store_follows_db_file(tmp_path, monkeypatch):
    # conftest points DB_FILE at a temp file, so tests never touch data/memory.db
    assert memory_engine.DB_FILE.parent == tmp_path
    store = memory_engine.get_memory()
    assert store is memory_engine.get_memory()
    store.remember("I like lo-fi")
    assert (tmp_path / "memory.db").exists()
