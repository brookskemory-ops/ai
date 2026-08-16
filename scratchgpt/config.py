"""Every knob of the model and the training run, in one place.

Nothing here is inherited from a pretrained checkpoint. These numbers, plus a
random seed, fully determine the model you get.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class ModelConfig:
    """Shape of the network. Changing any of these changes the parameter count."""

    vocab_size: int = 4096
    """Number of tokens the embedding table covers. Must match the tokenizer."""

    block_size: int = 256
    """Context length in tokens. Attention cost grows with the square of this."""

    n_layer: int = 6
    """Number of transformer blocks stacked on top of each other."""

    n_head: int = 6
    """Number of attention heads. Must divide n_embd evenly."""

    n_kv_head: int | None = None
    """Key/value heads for grouped-query attention. None means one per query head."""

    n_embd: int = 384
    """Width of the residual stream. The single biggest lever on capacity."""

    mlp_ratio: float = 8 / 3
    """Hidden width of each MLP as a multiple of n_embd. 8/3 keeps SwiGLU
    parameter-matched to a classic 4x GELU MLP."""

    dropout: float = 0.0
    """Applied to attention weights, MLP output, and the embedding sum."""

    bias: bool = False
    """Whether linear layers carry a bias term. Off is standard for modern LLMs."""

    rope_theta: float = 10000.0
    """Base frequency for rotary position embeddings. Raise it for long context."""

    tie_embeddings: bool = True
    """Share weights between the input embedding and the output head."""

    def __post_init__(self) -> None:
        if self.n_embd % self.n_head != 0:
            raise ValueError(
                f"n_embd ({self.n_embd}) must be divisible by n_head ({self.n_head})"
            )
        if self.n_kv_head is None:
            self.n_kv_head = self.n_head
        if self.n_head % self.n_kv_head != 0:
            raise ValueError(
                f"n_head ({self.n_head}) must be divisible by "
                f"n_kv_head ({self.n_kv_head})"
            )

    @property
    def head_dim(self) -> int:
        return self.n_embd // self.n_head

    @property
    def hidden_dim(self) -> int:
        """MLP hidden width, rounded to a multiple of 64 for efficient matmuls."""
        raw = int(self.n_embd * self.mlp_ratio)
        return 64 * ((raw + 63) // 64)


@dataclass
class TrainConfig:
    """How the weights get moved. None of this changes the model's shape."""

    # --- data ---
    data_dir: str = "data/tokens"
    """Directory holding train.bin / val.bin produced by prepare_data.py."""

    out_dir: str = "checkpoints/run1"
    """Where checkpoints and the resolved config get written."""

    # --- batching ---
    batch_size: int = 16
    """Sequences per forward pass. Lower this first if you run out of memory."""

    grad_accum_steps: int = 4
    """Micro-batches accumulated before each optimizer step. The effective batch
    is batch_size * grad_accum_steps * block_size tokens."""

    # --- optimization ---
    max_steps: int = 2000
    """Total optimizer steps (not micro-batches)."""

    learning_rate: float = 3e-4
    """Peak LR, reached at the end of warmup."""

    min_lr_ratio: float = 0.1
    """Final LR as a fraction of peak, under the cosine schedule."""

    warmup_steps: int = 100
    """Linear ramp from 0 to learning_rate over this many steps."""

    weight_decay: float = 0.1
    """Applied to matrices only — never to biases, norms, or embeddings."""

    beta1: float = 0.9
    beta2: float = 0.95
    grad_clip: float = 1.0
    """Global gradient-norm clip. 0 disables clipping."""

    # --- evaluation and logging ---
    eval_interval: int = 200
    eval_batches: int = 40
    """Batches averaged per validation estimate. More batches, less noise."""

    log_interval: int = 10
    checkpoint_interval: int = 500
    always_save_checkpoint: bool = False
    """If False, only overwrite the checkpoint when val loss improves."""

    # --- runtime ---
    device: str = "auto"
    """'auto', 'cpu', 'cuda', or 'mps'."""

    dtype: str = "auto"
    """'auto', 'float32', 'bfloat16', or 'float16'. CPU always uses float32."""

    compile_model: bool = False
    """torch.compile. Big speedup on CUDA, usually not worth it on CPU."""

    seed: int = 1337
    """Set this and everything above, and the run is reproducible."""


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        """Read a JSON config. Any key you omit keeps its default above."""
        raw = json.loads(Path(path).read_text())
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> Config:
        unknown = set(raw) - {"model", "train"}
        if unknown:
            raise ValueError(f"unknown top-level config keys: {sorted(unknown)}")
        return cls(
            model=_build(ModelConfig, raw.get("model", {})),
            train=_build(TrainConfig, raw.get("train", {})),
        )

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2) + "\n")

    def override(self, dotted: dict[str, object]) -> None:
        """Apply CLI-style overrides like {"train.batch_size": 8}, in place."""
        for key, value in dotted.items():
            section, _, name = key.partition(".")
            if not name or section not in ("model", "train"):
                raise ValueError(
                    f"override '{key}' must look like 'model.<field>' or 'train.<field>'"
                )
            target = getattr(self, section)
            if not hasattr(target, name):
                raise ValueError(f"{section} has no field '{name}'")
            setattr(target, name, _coerce(type(target), name, value))
        # Re-run validation now that fields have moved.
        self.model.__post_init__()


def _build(cls, raw: dict):
    valid = {f.name for f in fields(cls)}
    unknown = set(raw) - valid
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**{k: _coerce(cls, k, v) for k, v in raw.items()})


def _coerce(cls, name: str, value):
    """Turn a string from the CLI into the field's declared type."""
    if not isinstance(value, str):
        return value
    declared = {f.name: f.type for f in fields(cls)}[name]
    text = str(declared)
    if "bool" in text:
        return value.strip().lower() in ("1", "true", "yes", "on")
    if "int" in text and "float" not in text:
        return None if value.lower() == "none" else int(value)
    if "float" in text:
        return float(value)
    return value
