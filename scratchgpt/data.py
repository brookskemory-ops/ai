"""Turning a directory of source files into batches of token ids.

The pipeline is: gather files -> train a BPE tokenizer on them -> encode
everything into one flat array of ids on disk -> sample random windows from it.

Token files are read with memmap, so a corpus larger than RAM still trains.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .tokenizer import END_OF_TEXT, Tokenizer

# Extension -> language label. Restricting to a set you choose beats crawling a
# repo blindly: minified bundles and vendored dependencies are the fastest way
# to waste a small model's capacity.
CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".sh": "shell",
    ".sql": "sql",
    ".css": "css",
    ".html": "html",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
}

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", "dist", "build", "target", ".mypy_cache", ".pytest_cache",
    ".tox", "site-packages", "vendor", ".next", ".cache", "checkpoints",
}

MAX_FILE_BYTES = 512 * 1024
"""Anything larger is almost always generated, vendored, or minified."""


def collect_files(
    roots: list[str | Path],
    extensions: set[str] | None = None,
    max_bytes: int = MAX_FILE_BYTES,
) -> list[Path]:
    """Walk `roots` and return the source files worth training on."""
    exts = extensions if extensions is not None else set(CODE_EXTENSIONS)
    found: list[Path] = []
    for root in roots:
        root = Path(root).expanduser().resolve()
        if root.is_file():
            found.append(root)
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in exts:
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            found.append(path)
    # Sort so the same inputs always produce the same corpus and the same split.
    return sorted(set(found))


def read_documents(paths: list[Path], min_lines: int = 3) -> list[str]:
    """Read files as UTF-8 text, skipping binaries and near-empty stubs."""
    docs: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if text.count("\n") < min_lines or not text.strip():
            continue
        if "\x00" in text:
            continue
        docs.append(text)
    return docs


def build_corpus(docs: list[str]) -> str:
    """Join documents with an end-of-text marker between them.

    The separator is load-bearing: it is how the model learns that a file ends
    rather than running one file's tail into the next file's imports.
    """
    return END_OF_TEXT.join(docs) + END_OF_TEXT


def encode_to_disk(
    text: str,
    tokenizer: Tokenizer,
    out_dir: str | Path,
    val_fraction: float = 0.05,
) -> dict:
    """Tokenize `text`, split it, and write train.bin / val.bin / meta.json.

    The split is a tail slice rather than a shuffle, because shuffling token
    windows would put the first half of a function in train and the second half
    in validation, and the reported val loss would be meaningless.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    ids = tokenizer.encode(text)
    dtype = np.uint16 if tokenizer.vocab_size <= 65536 else np.uint32
    arr = np.array(ids, dtype=dtype)

    split = int(len(arr) * (1 - val_fraction))
    arr[:split].tofile(out / "train.bin")
    arr[split:].tofile(out / "val.bin")

    meta = {
        "vocab_size": tokenizer.vocab_size,
        "dtype": np.dtype(dtype).name,
        "train_tokens": int(split),
        "val_tokens": int(len(arr) - split),
        "total_tokens": int(len(arr)),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def load_meta(data_dir: str | Path) -> dict:
    path = Path(data_dir) / "meta.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found -- run scripts/prepare_data.py before training"
        )
    return json.loads(path.read_text())


class TokenDataset:
    """Random fixed-length windows over a flat token file.

    Each window is one training example: the inputs are tokens [i, i+T) and the
    targets are the same window shifted by one, so every position predicts its
    successor and a single sequence yields T supervised predictions.
    """

    def __init__(self, data_dir: str | Path, split: str, block_size: int) -> None:
        meta = load_meta(data_dir)
        path = Path(data_dir) / f"{split}.bin"
        if not path.exists():
            raise FileNotFoundError(f"missing token file: {path}")

        self.tokens = np.memmap(path, dtype=np.dtype(meta["dtype"]), mode="r")
        self.block_size = block_size
        self.vocab_size = meta["vocab_size"]

        if len(self.tokens) < block_size + 1:
            raise ValueError(
                f"{split}.bin holds {len(self.tokens)} tokens but block_size is "
                f"{block_size} -- use a larger corpus or a smaller block_size"
            )

    def __len__(self) -> int:
        return len(self.tokens) - self.block_size

    def get_batch(self, batch_size: int, device: str, generator=None):
        """Sample `batch_size` windows uniformly at random."""
        high = len(self)
        if generator is not None:
            import torch

            starts = torch.randint(high, (batch_size,), generator=generator).numpy()
        else:
            starts = np.random.randint(0, high, size=batch_size)

        import torch

        # astype(int64) copies out of the memmap, which is required: torch
        # cannot own memory backed by a mapped file.
        x = torch.from_numpy(
            np.stack([self.tokens[i : i + self.block_size] for i in starts]).astype(np.int64)
        )
        y = torch.from_numpy(
            np.stack(
                [self.tokens[i + 1 : i + 1 + self.block_size] for i in starts]
            ).astype(np.int64)
        )

        if device.startswith("cuda"):
            # Pinned memory lets the copy overlap with compute.
            x = x.pin_memory().to(device, non_blocking=True)
            y = y.pin_memory().to(device, non_blocking=True)
        else:
            x, y = x.to(device), y.to(device)
        return x, y
