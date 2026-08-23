# scratchgpt

A code language model built from nothing. No pretrained weights, no downloaded
vocabulary, no fine-tuning of somebody else's checkpoint. You supply source code
and a set of numbers; out comes a model whose every parameter started as random
noise on your machine.

PyTorch provides tensors, autograd, and matmul kernels. Everything above that is
written out here: the BPE tokenizer, attention, rotary embeddings, RMSNorm, the
SwiGLU MLP, the initialization scheme, the optimizer grouping, the LR schedule,
and the sampler.

## Read this before you start

**A model you train yourself will learn the shape of code, not how to write it.**
It will produce syntactically plausible, correctly indented, confidently wrong
code. That is not a bug in this repo — it is what the scale buys you:

| Tokens trained on | Roughly what you get |
|---|---|
| ~50M (a few CPU hours) | Valid indentation, matched brackets, real-looking identifiers, no working logic |
| ~1B (a day on one GPU) | Short idiomatic functions, correct imports, occasionally correct simple bodies |
| ~100B (a GPU cluster, weeks) | A genuinely useful completion model |
| ~10T (industrial) | Something like a commercial coding assistant |

Each row is roughly 20x the compute of the one above. There is no configuration
in this repo that skips a row. What this repo *is* good for: understanding
exactly how these models work, since nothing is hidden, and having a real
training stack that scales unchanged to a rented GPU.

## Setup

```bash
pip install -r requirements.txt
```

## 1. Build a corpus and a tokenizer

Point it at code you want the model to imitate. Your own repositories are the
best choice — a small model can only absorb a narrow slice of style, and yours
is the slice worth having.

```bash
python scripts/prepare_data.py --source ~/code/my-project --source ~/code/other
```

With no `--source`, it falls back to the Python standard library on your
machine, which is a clean few-megabyte corpus for a first run.

This trains a byte-level BPE tokenizer on your text and writes
`data/tokens/{train,val}.bin` plus `data/tokenizer.json`. Useful flags:

- `--vocab-size 4096` — bigger vocab means fewer tokens per file but a larger
  embedding table. 4k suits small models; 16k+ suits real ones.
- `--extensions .py,.js` — restrict which languages go in.
- `--max-corpus-mb 64` — cap the corpus size.

The printed **characters per token** is your compression ratio. Around 3.5 is
normal for a 4k vocab on code; higher is better.

## 2. Train

```bash
python -m scratchgpt.train --config configs/tiny.json
```

Override anything from the command line without editing the file:

```bash
python -m scratchgpt.train --config configs/tiny.json \
    --set train.batch_size=8 --set train.max_steps=5000
```

Ctrl-C saves a checkpoint before exiting; `--resume` picks it back up.

Two configs are provided:

- **`configs/tiny.json`** — 11M parameters, 256-token context. Trains on a CPU.
  Expect ~2,800 tokens/sec on four cores, so a 3,000-step run is about five
  hours.
- **`configs/small.json`** — 124M parameters, 1024-token context. Needs a GPU
  with 16GB+. This is roughly GPT-2 scale.

## 3. Generate

```bash
python -m scratchgpt.sample --checkpoint checkpoints/tiny/checkpoint.pt \
    --prompt "def binary_search(arr, target):"

python -m scratchgpt.sample --checkpoint checkpoints/tiny/checkpoint.pt --interactive
```

`--temperature 0` is greedy and repetitive; `0.8` is a reasonable default;
above `1.2` it falls apart. `--top-k` and `--top-p` trim the tail of the
distribution so one unlucky sample doesn't derail the rest.

## The parameters you control

Everything lives in a JSON config, split into shape and training. Full
descriptions are in `scratchgpt/config.py`, one per field.

**Model shape** — these change the parameter count:

| Field | What it does |
|---|---|
| `n_embd` | Width of the residual stream. The biggest single lever on capacity. |
| `n_layer` | How many transformer blocks stack up. Depth over width buys reasoning; width buys memory. |
| `n_head` | Attention heads. Must divide `n_embd`. |
| `n_kv_head` | Key/value heads shared across query heads. Fewer means a smaller KV cache when generating. |
| `block_size` | Context length. Attention cost grows with its square. |
| `vocab_size` | Must equal what the tokenizer produced. Training refuses to start otherwise. |
| `mlp_ratio` | MLP hidden width as a multiple of `n_embd`. |
| `dropout` | Use it on a small corpus, turn it off on a large one. |
| `tie_embeddings` | Share the input embedding with the output head. Saves `vocab_size * n_embd` parameters. |

**Training** — these change how the weights move, not what they are:

| Field | What it does |
|---|---|
| `learning_rate` | Peak LR after warmup. The first thing to lower if loss goes to NaN. |
| `batch_size` × `grad_accum_steps` | Effective batch. Lower `batch_size` for memory, raise accumulation to compensate. |
| `max_steps` | Total optimizer steps. Sets the whole cosine schedule, so changing it mid-run changes the LR curve. |
| `warmup_steps` | Linear ramp. Without it, early Adam steps on a garbage variance estimate can diverge the run. |
| `weight_decay` | Applied to matrices only, never to norms or biases. |
| `grad_clip` | Global gradient-norm clip. Leave at 1.0. |
| `seed` | Set this and everything above, and the run reproduces exactly. |

### Sizing a model to your data

The useful rule of thumb is roughly **20 tokens of training data per
parameter**. Below that the model memorizes instead of generalizing — you'll
see training loss fall while validation loss flattens or climbs. With a 3M-token
corpus, a model of a few million parameters is the honest size. Reach for more
data before more parameters.

## Reading the training output

```
step    150/700  loss 5.7719  lr 1.00e-03  |grad| 0.42  2,796 tok/s  eta 53.1m
  eval @ 150: train 5.4090  val 5.4451  (val ppl 231.7)
```

- **loss** is cross-entropy in nats. A fresh model starts at `ln(vocab_size)` —
  8.32 for a 4k vocab — because it is guessing uniformly. If step 0 isn't close
  to that, something is wrong before training even begins.
- **val ppl** is `exp(loss)`: roughly how many tokens the model is effectively
  choosing between at each position. 1391 → 200 means it went from "no idea" to
  narrowing the field considerably.
- **|grad|** is the pre-clip gradient norm. Spikes mean instability; lower the
  learning rate.
- **val diverging from train** is overfitting: get more data, raise `dropout`,
  or shrink the model.

## What a real run actually looks like

A measured run, so the table above isn't just a claim. `configs/tiny.json`,
11M parameters, 700 steps on the Python standard library (2.9M training
tokens), 70 minutes on four CPU cores:

| Step | Train loss | Val loss | Val ppl | Gap |
|---|---|---|---|---|
| 0 | 8.39 | — | — | — |
| 150 | 3.52 | 4.37 | 79.0 | 0.85 |
| 300 | 2.85 | 3.92 | 50.2 | 1.07 |
| 450 | 2.23 | 3.63 | 37.8 | 1.40 |
| 700 | 2.07 | 3.40 | 29.9 | 1.32 |

Step 0 lands on `ln(4096) = 8.32` as it must — a fresh model guessing
uniformly. Validation improved throughout, so the run had not yet saturated,
but train loss fell about twice as fast and the gap settled above 1.3. That
gap is the signature of a model too large for its corpus: 11M parameters
against 2.9M tokens is roughly 75x past the ~20-tokens-per-parameter rule, so
it is memorizing the stdlib rather than generalizing from it.

Prompted with `def binary_search(arr, target):`, the finished model produces:

```python
def binary_search(arr, target):
    """Get the current path to the file.  After the path is true.

    If the path is the directory (including the path
    is relative to the path that will be used.
    """
    if path is None:
        path = path.split('/')
    else:
        return path
```

This is the expected result, not a failure. Valid syntax, correct indentation,
a plausible docstring, real control flow — and no relationship whatsoever
between the function's name and its body. Syntax is what the first few million
tokens buy. Semantics is what the next several orders of magnitude buy.

Degenerate repetition is also normal at this scale:

```python
class Stack:
    def __init__(self):
        self.args = self.args
        self.args = []
        self.args = [self.args]
```

The fix is more data, not more steps. If you see this, check the gap column
first.

## Layout

```
scratchgpt/
  config.py      every hyperparameter, documented in place
  tokenizer.py   byte-level BPE, trained from scratch on your corpus
  model.py       the transformer: RMSNorm, RoPE, GQA, SwiGLU
  data.py        corpus collection, encoding, memmapped batch sampling
  train.py       the training loop
  sample.py      generation
scripts/
  prepare_data.py
configs/
  tiny.json      11M params, CPU-trainable
  small.json     124M params, GPU
tests/
```

## Tests

```bash
python -m pytest tests/ -q
```

The one that matters most is `test_causal_mask_blocks_the_future`. If position
`t` can see `t+1`, the loss curve looks *better* while the model learns nothing,
and no other signal catches it.

## Scaling up

The code is written to move to real hardware unchanged. On a GPU:

```bash
python -m scratchgpt.train --config configs/small.json
```

`bfloat16` autocast and `torch.compile` switch on automatically or via config.
For multi-GPU, wrap the model in `DistributedDataParallel` in `train.py` and
divide `grad_accum_steps` by the world size — the rest of the loop is unchanged.

## Also in this repository

`roblox-gacha/` is unrelated to the language model: a server-authoritative
gacha system for Roblox, with pity, rate-up banners, and an odds panel
generated from the same numbers the server rolls against. Its probability model
is pure Luau and runs under the standalone interpreter, so `luau tests/run.luau`
in that folder checks the drop rates without opening Studio. See
[roblox-gacha/README.md](roblox-gacha/README.md).
