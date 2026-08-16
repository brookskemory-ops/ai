"""A byte-level BPE tokenizer, trained from scratch on your own corpus.

No vendored vocabulary. You run `train()` on your code, and the merge list that
comes out is derived entirely from what you fed it.

Working at the byte level means every possible file encodes without an unknown
token, and the pre-tokenizer regex is tuned for source code: runs of indentation
stay together so the model spends one token on a nesting level instead of four.
"""

from __future__ import annotations

import json
import regex
from collections import defaultdict
from pathlib import Path

# Ordered alternatives, first match wins:
#   1. contractions, so "don't" splits the way English text expects
#   2. runs of spaces or tabs -- indentation, the dominant pattern in code
#   3. a letter run with an optional single leading space
#   4. a digit run, capped at 3 so numbers do not blow up the vocabulary
#   5. punctuation runs with an optional leading space
#   6. trailing whitespace, keeping newlines attached to what precedes them
SPLIT_PATTERN = regex.compile(
    r"""'(?:[sdmt]|ll|ve|re)|"""
    r""" ?\t+| {2,}|"""
    r""" ?\p{L}+|"""
    r""" ?\p{N}{1,3}|"""
    r""" ?[^\s\p{L}\p{N}]+|"""
    r"""\s+(?!\S)|\s+"""
)

END_OF_TEXT = "<|endoftext|>"
"""Written between documents so the model learns where a file ends."""


class Tokenizer:
    """Byte-level BPE. Construct via `train()` or `load()`, not directly."""

    def __init__(
        self,
        merges: dict[tuple[int, int], int],
        special_tokens: dict[str, int] | None = None,
    ) -> None:
        self.merges = merges
        self.special_tokens = special_tokens or {}
        self._rebuild()

    def _rebuild(self) -> None:
        """Derive the id -> bytes table and the reverse lookups."""
        self.vocab: dict[int, bytes] = {i: bytes([i]) for i in range(256)}
        for (a, b), idx in sorted(self.merges.items(), key=lambda kv: kv[1]):
            self.vocab[idx] = self.vocab[a] + self.vocab[b]
        for text, idx in self.special_tokens.items():
            self.vocab[idx] = text.encode("utf-8")
        self._special_re = (
            regex.compile("(" + "|".join(regex.escape(s) for s in self.special_tokens) + ")")
            if self.special_tokens
            else None
        )

    @property
    def vocab_size(self) -> int:
        return len(self.vocab)

    # ------------------------------------------------------------------ train

    @classmethod
    def train(
        cls,
        text: str,
        vocab_size: int,
        special_tokens: tuple[str, ...] = (END_OF_TEXT,),
        verbose: bool = False,
    ) -> Tokenizer:
        """Learn up to `vocab_size - 256 - len(special_tokens)` merges from `text`.

        Repeatedly finds the most frequent adjacent pair of symbols and gives it
        a new id. Pair counts are maintained incrementally, so cost scales with
        how much each merge actually changes rather than with the corpus size.

        `vocab_size` is an upper bound, not a guarantee: on a small corpus the
        merges run out once no pair repeats, and the resulting vocabulary is
        smaller. Always read `vocab_size` back off the trained tokenizer rather
        than assuming you got what you asked for.
        """
        n_merges = vocab_size - 256 - len(special_tokens)
        if n_merges < 0:
            raise ValueError(
                f"vocab_size {vocab_size} is below the {256 + len(special_tokens)} "
                "bytes and special tokens that always exist"
            )

        # Pre-tokenize into chunks and collapse duplicates. Merges never cross a
        # chunk boundary, which is what stops "foo(" from becoming one token.
        counts: dict[bytes, int] = defaultdict(int)
        for chunk in SPLIT_PATTERN.findall(text):
            counts[chunk.encode("utf-8")] += 1

        words: list[list[int]] = [list(w) for w in counts]
        freqs: list[int] = list(counts.values())

        pair_counts: dict[tuple[int, int], int] = defaultdict(int)
        pair_where: dict[tuple[int, int], set[int]] = defaultdict(set)
        for i, symbols in enumerate(words):
            for pair in zip(symbols, symbols[1:]):
                pair_counts[pair] += freqs[i]
                pair_where[pair].add(i)

        merges: dict[tuple[int, int], int] = {}
        for step in range(n_merges):
            if not pair_counts:
                if verbose:
                    print(f"corpus exhausted after {step} merges")
                break
            best = max(pair_counts, key=lambda p: (pair_counts[p], p))
            if pair_counts[best] < 2:
                if verbose:
                    print(f"no pair repeats after {step} merges; stopping")
                break

            new_id = 256 + step
            merges[best] = new_id
            if verbose and step % 250 == 0:
                shown = _printable(bytes_for(best, merges))
                print(f"  merge {step:5d} -> id {new_id:5d}  {shown!r}  "
                      f"({pair_counts[best]} occurrences)")

            for i in list(pair_where[best]):
                old = words[i]
                new = _apply_merge(old, best, new_id)
                if new == old:
                    continue
                freq = freqs[i]
                _unindex(old, i, freq, pair_counts, pair_where)
                _index(new, i, freq, pair_counts, pair_where)
                words[i] = new

            pair_counts.pop(best, None)
            pair_where.pop(best, None)

        specials = {tok: 256 + len(merges) + i for i, tok in enumerate(special_tokens)}
        return cls(merges, specials)

    # ----------------------------------------------------------------- encode

    def encode(self, text: str, allowed_special: bool = True) -> list[int]:
        """Text -> token ids. Round-trips exactly through `decode`."""
        if not allowed_special or self._special_re is None:
            return self._encode_ordinary(text)
        out: list[int] = []
        for piece in self._special_re.split(text):
            if piece in self.special_tokens:
                out.append(self.special_tokens[piece])
            elif piece:
                out.extend(self._encode_ordinary(piece))
        return out

    def _encode_ordinary(self, text: str) -> list[int]:
        out: list[int] = []
        for chunk in SPLIT_PATTERN.findall(text):
            out.extend(self._encode_chunk(chunk.encode("utf-8")))
        return out

    def _encode_chunk(self, raw: bytes) -> list[int]:
        """Apply merges to one chunk, always taking the earliest-learned pair."""
        ids = list(raw)
        while len(ids) >= 2:
            pair = min(
                zip(ids, ids[1:]),
                key=lambda p: self.merges.get(p, float("inf")),
            )
            if pair not in self.merges:
                break
            ids = _apply_merge(ids, pair, self.merges[pair])
        return ids

    def decode(self, ids: list[int], errors: str = "replace") -> str:
        """Token ids -> text. Invalid UTF-8 from a truncated token is replaced."""
        try:
            raw = b"".join(self.vocab[i] for i in ids)
        except KeyError as exc:
            raise ValueError(f"token id {exc.args[0]} is outside this vocabulary") from exc
        return raw.decode("utf-8", errors=errors)

    # ------------------------------------------------------------- persistence

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "version": 1,
                    "merges": [[a, b, idx] for (a, b), idx in self.merges.items()],
                    "special_tokens": self.special_tokens,
                },
                indent=None,
            )
        )

    @classmethod
    def load(cls, path: str | Path) -> Tokenizer:
        raw = json.loads(Path(path).read_text())
        merges = {(a, b): idx for a, b, idx in raw["merges"]}
        return cls(merges, raw.get("special_tokens", {}))


def _apply_merge(ids: list[int], pair: tuple[int, int], new_id: int) -> list[int]:
    out: list[int] = []
    i = 0
    while i < len(ids):
        if i < len(ids) - 1 and ids[i] == pair[0] and ids[i + 1] == pair[1]:
            out.append(new_id)
            i += 2
        else:
            out.append(ids[i])
            i += 1
    return out


def _index(symbols, word_idx, freq, pair_counts, pair_where) -> None:
    for pair in zip(symbols, symbols[1:]):
        pair_counts[pair] += freq
        pair_where[pair].add(word_idx)


def _unindex(symbols, word_idx, freq, pair_counts, pair_where) -> None:
    for pair in zip(symbols, symbols[1:]):
        pair_counts[pair] -= freq
        if pair_counts[pair] <= 0:
            pair_counts.pop(pair, None)
            pair_where.pop(pair, None)
        else:
            pair_where[pair].discard(word_idx)


def bytes_for(pair: tuple[int, int], merges: dict[tuple[int, int], int]) -> bytes:
    """Expand a symbol pair back to the raw bytes it stands for (for logging)."""
    table: dict[int, bytes] = {i: bytes([i]) for i in range(256)}
    for (a, b), idx in sorted(merges.items(), key=lambda kv: kv[1]):
        table[idx] = table[a] + table[b]
    return table[pair[0]] + table[pair[1]]


def _printable(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")
