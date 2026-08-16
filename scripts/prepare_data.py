#!/usr/bin/env python3
"""Build a training corpus and a tokenizer from directories of source code.

    python scripts/prepare_data.py --source ~/my-projects --vocab-size 4096

Point --source at whatever code you want the model to learn from -- your own
repos are the best choice, since a small model can only memorize a narrow slice
of style and yours is the slice worth having. Pass --source more than once to
combine several trees.

With no --source, it falls back to the Python standard library installed on this
machine, which is a clean, consistent, few-megabyte corpus for a first run.
"""

from __future__ import annotations

import argparse
import sys
import sysconfig
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scratchgpt.data import (  # noqa: E402
    CODE_EXTENSIONS,
    build_corpus,
    collect_files,
    encode_to_disk,
    read_documents,
)
from scratchgpt.tokenizer import Tokenizer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append", default=[],
                        help="directory or file to include; repeatable")
    parser.add_argument("--out-dir", default="data/tokens",
                        help="where train.bin, val.bin, and meta.json land")
    parser.add_argument("--tokenizer-out", default="data/tokenizer.json")
    parser.add_argument("--vocab-size", type=int, default=4096,
                        help="total tokens including the 256 raw bytes")
    parser.add_argument("--val-fraction", type=float, default=0.05)
    parser.add_argument("--extensions", default=None,
                        help="comma-separated list, e.g. '.py,.js'; default is every "
                             "supported code extension")
    parser.add_argument("--max-corpus-mb", type=float, default=64.0,
                        help="cap on corpus size; tokenizer training is the slow part")
    args = parser.parse_args()

    sources = args.source or [sysconfig.get_paths()["stdlib"]]
    if not args.source:
        print(f"No --source given; using the Python standard library at {sources[0]}")

    extensions = (
        {e if e.startswith(".") else f".{e}" for e in args.extensions.split(",")}
        if args.extensions
        else set(CODE_EXTENSIONS)
    )

    print(f"Scanning {len(sources)} source location(s)...")
    paths = collect_files(sources, extensions)
    if not paths:
        print("No matching source files found. Check --source and --extensions.", file=sys.stderr)
        return 1
    print(f"  {len(paths)} candidate files")

    docs = read_documents(paths)
    print(f"  {len(docs)} readable text documents")
    if not docs:
        print("Every candidate file failed to decode as UTF-8.", file=sys.stderr)
        return 1

    corpus = build_corpus(docs)
    limit = int(args.max_corpus_mb * 1024 * 1024)
    if len(corpus) > limit:
        print(f"  corpus is {len(corpus) / 1e6:.1f} MB, truncating to {args.max_corpus_mb} MB")
        corpus = corpus[:limit]
    print(f"  {len(corpus) / 1e6:.2f} MB of text")

    print(f"\nTraining a byte-level BPE tokenizer to {args.vocab_size} tokens...")
    start = time.time()
    tokenizer = Tokenizer.train(corpus, vocab_size=args.vocab_size, verbose=True)
    tokenizer.save(args.tokenizer_out)
    print(f"  done in {time.time() - start:.1f}s -> {args.tokenizer_out}")
    if tokenizer.vocab_size < args.vocab_size:
        print(
            f"  note: got {tokenizer.vocab_size} tokens, not {args.vocab_size} -- "
            f"the corpus ran out of repeating pairs. Use more code for a larger "
            f"vocabulary."
        )

    print("\nEncoding the corpus...")
    start = time.time()
    meta = encode_to_disk(corpus, tokenizer, args.out_dir, args.val_fraction)
    print(f"  done in {time.time() - start:.1f}s")

    ratio = len(corpus) / max(meta["total_tokens"], 1)
    print(
        f"\n{meta['total_tokens']:,} tokens "
        f"({meta['train_tokens']:,} train / {meta['val_tokens']:,} val)\n"
        f"{ratio:.2f} characters per token\n"
        f"Written to {args.out_dir}/\n\n"
        f"Set model.vocab_size to {meta['vocab_size']} in your config, then:\n"
        f"  python -m scratchgpt.train --config configs/tiny.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
