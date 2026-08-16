"""Generate code from a trained checkpoint.

    python -m scratchgpt.sample --checkpoint checkpoints/run1/checkpoint.pt \
        --prompt "def binary_search(arr, target):"

Add --interactive for a prompt loop that keeps the model resident.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

from .config import ModelConfig
from .model import GPT
from .tokenizer import END_OF_TEXT, Tokenizer
from .train import resolve_device


def load_model(checkpoint: str | Path, device: str) -> tuple[GPT, ModelConfig]:
    """Rebuild the exact architecture the checkpoint was trained with."""
    ckpt = torch.load(checkpoint, map_location=device, weights_only=False)
    saved = dict(ckpt["model_config"])
    # n_kv_head is derived in __post_init__ but stored resolved; both work.
    config = ModelConfig(**saved)
    model = GPT(config)
    state = ckpt["model"]
    # Strip the prefix torch.compile adds, so compiled and eager checkpoints
    # are interchangeable.
    state = {k.removeprefix("_orig_mod."): v for k, v in state.items()}
    model.load_state_dict(state)
    model.to(device).eval()
    print(
        f"Loaded step {ckpt.get('step', '?')} "
        f"({model.num_parameters() / 1e6:.2f}M params, "
        f"val loss {ckpt.get('best_val_loss', float('nan')):.4f})",
        file=sys.stderr,
    )
    return model, config


def generate_text(model, tokenizer, prompt, device, args) -> str:
    ids = tokenizer.encode(prompt) if prompt else [tokenizer.special_tokens[END_OF_TEXT]]
    idx = torch.tensor([ids], dtype=torch.long, device=device)

    out = model.generate(
        idx,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_k=args.top_k if args.top_k > 0 else None,
        top_p=args.top_p if args.top_p < 1.0 else None,
        stop_token=tokenizer.special_tokens.get(END_OF_TEXT),
    )
    text = tokenizer.decode(out[0].tolist())
    # The end-of-text marker is a document boundary, not output. Search from the
    # end of the prompt so a marker the user typed themselves is left alone.
    cut = text.find(END_OF_TEXT, len(prompt))
    return text if cut == -1 else text[:cut]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", default="checkpoints/run1/checkpoint.pt")
    parser.add_argument("--tokenizer", default="data/tokenizer.json")
    parser.add_argument("--prompt", default="def ")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.8,
                        help="0 is greedy; higher is more varied and less correct")
    parser.add_argument("--top-k", type=int, default=40, help="0 disables")
    parser.add_argument("--top-p", type=float, default=0.95, help="1.0 disables")
    parser.add_argument("--num-samples", type=int, default=1)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    if not Path(args.checkpoint).exists():
        print(f"No checkpoint at {args.checkpoint}. Train one first.", file=sys.stderr)
        return 1
    if not Path(args.tokenizer).exists():
        print(f"No tokenizer at {args.tokenizer}. Run scripts/prepare_data.py.", file=sys.stderr)
        return 1

    if args.seed is not None:
        torch.manual_seed(args.seed)

    device = resolve_device(args.device)
    tokenizer = Tokenizer.load(args.tokenizer)
    model, config = load_model(args.checkpoint, device)

    if tokenizer.vocab_size != config.vocab_size:
        print(
            f"Tokenizer has {tokenizer.vocab_size} tokens but the model expects "
            f"{config.vocab_size}. They must come from the same prepare_data run.",
            file=sys.stderr,
        )
        return 1

    if args.interactive:
        print("Enter a prompt, or Ctrl-D to quit.\n", file=sys.stderr)
        while True:
            try:
                prompt = input(">>> ")
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                return 0
            print(generate_text(model, tokenizer, prompt, device, args))
            print()

    for i in range(args.num_samples):
        if args.num_samples > 1:
            print(f"--- sample {i + 1} ---", file=sys.stderr)
        print(generate_text(model, tokenizer, args.prompt, device, args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
