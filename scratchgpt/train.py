"""Training loop: random init to trained checkpoint.

    python -m scratchgpt.train --config configs/tiny.json
    python -m scratchgpt.train --config configs/tiny.json --set train.batch_size=8
    python -m scratchgpt.train --config configs/tiny.json --resume

Interrupting with Ctrl-C saves a checkpoint before exiting, so a run is never
lost to a change of mind.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from contextlib import nullcontext
from pathlib import Path

import torch

from .config import Config
from .data import TokenDataset, load_meta
from .model import GPT


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_dtype(requested: str, device: str) -> torch.dtype:
    """Pick a compute dtype. Only CUDA reliably gains from reduced precision."""
    if requested == "auto":
        if device.startswith("cuda") and torch.cuda.is_bf16_supported():
            return torch.bfloat16
        return torch.float32
    return {"float32": torch.float32, "bfloat16": torch.bfloat16, "float16": torch.float16}[requested]


def lr_at(step: int, cfg) -> float:
    """Linear warmup, then cosine decay to min_lr_ratio * peak.

    Warmup exists because Adam's second-moment estimate is garbage for the first
    few steps; taking full-size steps on that estimate is how runs diverge in
    the first hundred iterations.
    """
    if step < cfg.warmup_steps:
        return cfg.learning_rate * (step + 1) / max(cfg.warmup_steps, 1)
    if step >= cfg.max_steps:
        return cfg.learning_rate * cfg.min_lr_ratio
    progress = (step - cfg.warmup_steps) / max(cfg.max_steps - cfg.warmup_steps, 1)
    cosine = 0.5 * (1 + math.cos(math.pi * progress))
    return cfg.learning_rate * (cfg.min_lr_ratio + (1 - cfg.min_lr_ratio) * cosine)


@torch.no_grad()
def evaluate(model, datasets, cfg, device, autocast) -> dict[str, float]:
    """Average loss over a fixed number of batches from each split."""
    model.eval()
    out = {}
    for split, dataset in datasets.items():
        losses = torch.zeros(cfg.eval_batches)
        for i in range(cfg.eval_batches):
            x, y = dataset.get_batch(cfg.batch_size, device)
            with autocast:
                _, loss, _ = model(x, y)
            losses[i] = loss.item()
        out[split] = losses.mean().item()
    model.train()
    return out


def save_checkpoint(path: Path, model, optimizer, config, step, best_val) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "model_config": vars(config.model),
            "step": step,
            "best_val_loss": best_val,
        },
        path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="path to a JSON config")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a config field, e.g. --set train.max_steps=500")
    parser.add_argument("--resume", action="store_true",
                        help="continue from the checkpoint in out_dir")
    args = parser.parse_args()

    config = Config.load(args.config)
    if args.set:
        overrides = {}
        for item in args.set:
            key, sep, value = item.partition("=")
            if not sep:
                parser.error(f"--set expects KEY=VALUE, got '{item}'")
            overrides[key.strip()] = value.strip()
        config.override(overrides)

    tcfg, mcfg = config.train, config.model

    device = resolve_device(tcfg.device)
    device_type = "cuda" if device.startswith("cuda") else device
    dtype = resolve_dtype(tcfg.dtype, device)
    autocast = (
        torch.autocast(device_type=device_type, dtype=dtype)
        if device_type == "cuda" and dtype != torch.float32
        else nullcontext()
    )

    torch.manual_seed(tcfg.seed)
    if device_type == "cuda":
        torch.cuda.manual_seed(tcfg.seed)
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

    # The tokenizer decides the vocabulary; a mismatch here means every id is
    # pointing at the wrong embedding row, so fail loudly instead of training
    # something meaningless.
    meta = load_meta(tcfg.data_dir)
    if meta["vocab_size"] != mcfg.vocab_size:
        raise ValueError(
            f"config model.vocab_size is {mcfg.vocab_size} but the tokenized data in "
            f"{tcfg.data_dir} was built with {meta['vocab_size']}. Set them equal."
        )

    datasets = {
        "train": TokenDataset(tcfg.data_dir, "train", mcfg.block_size),
        "val": TokenDataset(tcfg.data_dir, "val", mcfg.block_size),
    }

    model = GPT(mcfg).to(device)
    optimizer = model.configure_optimizers(
        tcfg.weight_decay, tcfg.learning_rate, (tcfg.beta1, tcfg.beta2), device_type
    )

    out_dir = Path(tcfg.out_dir)
    ckpt_path = out_dir / "checkpoint.pt"
    start_step, best_val = 0, float("inf")

    if args.resume:
        if not ckpt_path.exists():
            raise FileNotFoundError(f"--resume given but {ckpt_path} does not exist")
        ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model"])
        optimizer.load_state_dict(ckpt["optimizer"])
        start_step = ckpt["step"]
        best_val = ckpt.get("best_val_loss", float("inf"))
        print(f"Resumed from step {start_step} (best val loss {best_val:.4f})")

    config.save(out_dir / "config.json")

    if tcfg.compile_model:
        print("Compiling the model (first step will be slow)...")
        model = torch.compile(model)

    tokens_per_step = tcfg.batch_size * tcfg.grad_accum_steps * mcfg.block_size
    print(
        f"\nModel: {model.num_parameters() / 1e6:.2f}M parameters\n"
        f"  {mcfg.n_layer} layers, {mcfg.n_head} heads, width {mcfg.n_embd}, "
        f"context {mcfg.block_size}, vocab {mcfg.vocab_size}\n"
        f"Device: {device} ({dtype}) | Data: {meta['train_tokens']:,} train tokens\n"
        f"Batch: {tokens_per_step:,} tokens/step x {tcfg.max_steps} steps "
        f"= {tokens_per_step * tcfg.max_steps / 1e6:.1f}M tokens seen\n"
        f"Output: {out_dir}/\n"
    )

    history: list[dict] = []
    model.train()
    t0 = time.time()
    interrupted = False

    try:
        for step in range(start_step, tcfg.max_steps):
            lr = lr_at(step, tcfg)
            for group in optimizer.param_groups:
                group["lr"] = lr

            # Accumulate over micro-batches so the effective batch can exceed
            # what fits in memory at once.
            optimizer.zero_grad(set_to_none=True)
            total_loss = 0.0
            for _ in range(tcfg.grad_accum_steps):
                x, y = datasets["train"].get_batch(tcfg.batch_size, device)
                with autocast:
                    _, loss, _ = model(x, y)
                    # Scale so the accumulated gradient is the mean over the
                    # full effective batch, not the sum.
                    loss = loss / tcfg.grad_accum_steps
                loss.backward()
                total_loss += loss.item()

            grad_norm = float("nan")
            if tcfg.grad_clip > 0:
                grad_norm = torch.nn.utils.clip_grad_norm_(
                    model.parameters(), tcfg.grad_clip
                ).item()
            optimizer.step()

            if step % tcfg.log_interval == 0:
                elapsed = time.time() - t0
                done = step - start_step + 1
                rate = tokens_per_step * done / max(elapsed, 1e-9)
                remaining = (tcfg.max_steps - step - 1) * elapsed / done
                print(
                    f"step {step:6d}/{tcfg.max_steps}  loss {total_loss:.4f}  "
                    f"lr {lr:.2e}  |grad| {grad_norm:.2f}  "
                    f"{rate:,.0f} tok/s  eta {remaining / 60:.1f}m"
                )

            is_last = step == tcfg.max_steps - 1
            if (step > 0 and step % tcfg.eval_interval == 0) or is_last:
                losses = evaluate(model, datasets, tcfg, device, autocast)
                # exp(loss) is perplexity: roughly how many tokens the model is
                # effectively choosing between at each position.
                print(
                    f"  eval @ {step}: train {losses['train']:.4f}  "
                    f"val {losses['val']:.4f}  (val ppl {math.exp(min(losses['val'], 20)):.1f})"
                )
                history.append({"step": step, **losses})
                (out_dir / "history.json").write_text(json.dumps(history, indent=2) + "\n")

                if losses["val"] < best_val:
                    best_val = losses["val"]
                    save_checkpoint(ckpt_path, model, optimizer, config, step, best_val)
                    print(f"  new best val loss -> saved {ckpt_path}")
                elif tcfg.always_save_checkpoint:
                    save_checkpoint(ckpt_path, model, optimizer, config, step, best_val)

            if tcfg.checkpoint_interval and step > 0 and step % tcfg.checkpoint_interval == 0:
                save_checkpoint(out_dir / "latest.pt", model, optimizer, config, step, best_val)

    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted -- saving before exit...")
        save_checkpoint(out_dir / "latest.pt", model, optimizer, config, step, best_val)
        print(f"Saved {out_dir / 'latest.pt'} at step {step}. Rerun with --resume to continue.")

    if not interrupted:
        save_checkpoint(out_dir / "latest.pt", model, optimizer, config, tcfg.max_steps, best_val)
        print(
            f"\nDone in {(time.time() - t0) / 60:.1f} minutes. "
            f"Best val loss {best_val:.4f}.\n"
            f"Sample from it:\n"
            f"  python -m scratchgpt.sample --checkpoint {ckpt_path} "
            f"--prompt 'def '"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
