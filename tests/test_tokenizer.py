"""Tokenizer correctness. The one non-negotiable property is exact round-trip:
if encode/decode loses a byte, every downstream loss number is measuring the
wrong thing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scratchgpt.tokenizer import END_OF_TEXT, Tokenizer

SAMPLE = '''
def fib(n):
    """Return the nth Fibonacci number."""
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


class Counter:
    def __init__(self):
        self.count = 0

    def increment(self, by=1):
        self.count += by
        return self.count
''' * 20


@pytest.fixture(scope="module")
def tokenizer():
    return Tokenizer.train(SAMPLE, vocab_size=400)


def test_roundtrip_training_text(tokenizer):
    assert tokenizer.decode(tokenizer.encode(SAMPLE)) == SAMPLE


@pytest.mark.parametrize(
    "text",
    [
        "",
        "a",
        "def ",
        "x = 1\n",
        "    nested = {'a': [1, 2, 3]}\n",
        "unseen_identifier_qqq(zzz)",
        "emoji \U0001f600 and accents: café naïve",
        "\t\ttabs\r\nwindows line ending",
        "\x00\x01 raw control bytes",
    ],
)
def test_roundtrip_arbitrary_text(tokenizer, text):
    assert tokenizer.decode(tokenizer.encode(text)) == text


def test_special_token_is_atomic(tokenizer):
    ids = tokenizer.encode(f"a{END_OF_TEXT}b")
    assert tokenizer.special_tokens[END_OF_TEXT] in ids
    assert tokenizer.decode(ids) == f"a{END_OF_TEXT}b"


def test_special_token_can_be_disabled(tokenizer):
    ids = tokenizer.encode(END_OF_TEXT, allowed_special=False)
    assert tokenizer.special_tokens[END_OF_TEXT] not in ids
    assert tokenizer.decode(ids) == END_OF_TEXT


def test_vocab_size_is_an_upper_bound(tokenizer):
    """Training stops early once no pair repeats, so a small corpus yields a
    smaller vocabulary than requested. Ids must still stay inside the cap."""
    assert tokenizer.vocab_size <= 400
    assert max(tokenizer.encode(SAMPLE)) < tokenizer.vocab_size


def test_vocab_size_reached_exactly_on_a_rich_corpus():
    """Repeating one snippet does not help -- merges are capped by *distinct*
    content, since pre-tokenization collapses duplicate chunks. Real variety
    does, so train on this project's own source."""
    src = Path(__file__).resolve().parent.parent / "scratchgpt"
    corpus = "\n".join(p.read_text() for p in sorted(src.glob("*.py")))
    tok = Tokenizer.train(corpus, vocab_size=1024)
    assert tok.vocab_size == 1024


def test_ids_are_contiguous(tokenizer):
    """No gaps between byte ids, merge ids, and special ids -- an unreachable
    id would waste an embedding row and silently inflate the parameter count."""
    assert sorted(tokenizer.vocab) == list(range(tokenizer.vocab_size))


def test_merges_actually_compress(tokenizer):
    """A trained tokenizer must beat raw bytes on its own training data."""
    raw_bytes = len(SAMPLE.encode("utf-8"))
    assert len(tokenizer.encode(SAMPLE)) < raw_bytes / 2


def test_save_and_load_roundtrip(tokenizer, tmp_path):
    path = tmp_path / "tok.json"
    tokenizer.save(path)
    reloaded = Tokenizer.load(path)
    assert reloaded.vocab_size == tokenizer.vocab_size
    assert reloaded.encode(SAMPLE) == tokenizer.encode(SAMPLE)


def test_vocab_size_below_byte_floor_is_rejected():
    with pytest.raises(ValueError, match="below"):
        Tokenizer.train("hello", vocab_size=100)


def test_training_is_deterministic():
    a = Tokenizer.train(SAMPLE, vocab_size=400)
    b = Tokenizer.train(SAMPLE, vocab_size=400)
    assert a.merges == b.merges


def test_decode_rejects_unknown_id(tokenizer):
    with pytest.raises(ValueError, match="outside this vocabulary"):
        tokenizer.decode([99999])


def test_indentation_becomes_single_tokens():
    """Code-specific: a four-space indent should not cost four tokens."""
    text = ("    x = 1\n" * 50) + ("        y = 2\n" * 50)
    tok = Tokenizer.train(text, vocab_size=320)
    assert len(tok.encode("    ")) == 1
