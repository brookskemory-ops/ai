"""Model correctness.

The load-bearing test here is `test_causal_mask_blocks_the_future`: if position
t can see t+1, the loss drops beautifully and the model learns nothing, and no
other test catches it.
"""

import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scratchgpt.config import Config, ModelConfig
from scratchgpt.model import GPT, RMSNorm, RotaryEmbedding


@pytest.fixture
def config():
    return ModelConfig(
        vocab_size=97, block_size=16, n_layer=2, n_head=4, n_kv_head=2,
        n_embd=32, dropout=0.0,
    )


@pytest.fixture
def model(config):
    torch.manual_seed(0)
    return GPT(config).eval()


def shifted_batch(config, batch=2, seed=0):
    """Inputs and next-token targets, the way data.TokenDataset builds them.

    Passing the *unshifted* sequence as targets asks the model to predict token
    t from a window that already contains token t, which is free to solve and
    hides real bugs. Every loss test here uses the shifted pair.
    """
    torch.manual_seed(seed)
    ids = torch.randint(0, config.vocab_size, (batch, config.block_size + 1))
    return ids[:, :-1], ids[:, 1:]


def test_forward_shapes(model, config):
    x, y = shifted_batch(config, batch=3)
    logits, loss, _ = model(x, targets=y)
    assert logits.shape == (3, config.block_size, config.vocab_size)
    assert loss.shape == ()
    assert torch.isfinite(loss)


def test_initial_loss_is_near_uniform(config):
    """A fresh model should be about as good as guessing: loss ~= ln(vocab).

    Meaningfully lower means information is leaking from the targets; higher
    means the initialization scheme is producing overconfident logits.
    """
    import math

    torch.manual_seed(0)
    model = GPT(config).eval()
    x, y = shifted_batch(config, batch=16)
    _, loss, _ = model(x, targets=y)
    assert abs(loss.item() - math.log(config.vocab_size)) < 0.25


def test_causal_mask_blocks_the_future(model, config):
    """Changing token t must not move any logit at a position before t."""
    idx, targets = shifted_batch(config, batch=1, seed=1)
    logits_a, _, _ = model(idx, targets=targets)

    cut = config.block_size // 2
    idx_b = idx.clone()
    idx_b[0, cut] = (idx_b[0, cut] + 1) % config.vocab_size
    logits_b, _, _ = model(idx_b, targets=targets)

    assert torch.allclose(logits_a[:, :cut], logits_b[:, :cut], atol=1e-5)
    assert not torch.allclose(logits_a[:, cut], logits_b[:, cut], atol=1e-5)


def test_kv_cache_matches_full_forward(model, config):
    """Cached generation must produce the same logits as recomputing."""
    torch.manual_seed(2)
    idx = torch.randint(0, config.vocab_size, (1, 6))

    full, _, _ = model(idx)

    caches = [None] * config.n_layer
    incremental = None
    for t in range(idx.shape[1]):
        step_in = idx[:, : t + 1] if t == 0 else idx[:, t : t + 1]
        incremental, _, caches = model(step_in, kv_caches=caches)

    assert torch.allclose(full[:, -1], incremental[:, -1], atol=1e-4)


def test_generate_extends_by_requested_length(model, config):
    idx = torch.zeros((2, 3), dtype=torch.long)
    out = model.generate(idx, max_new_tokens=5, temperature=0.8)
    assert out.shape == (2, 8)
    assert (out[:, :3] == idx).all()
    assert out.max() < config.vocab_size


def test_generate_beyond_context_window(model, config):
    """Generating past block_size must slide the window, not crash."""
    idx = torch.zeros((1, config.block_size - 2), dtype=torch.long)
    out = model.generate(idx, max_new_tokens=8, temperature=0.8)
    assert out.shape == (1, config.block_size + 6)


def test_positions_keep_advancing_past_the_window(config):
    """Absolute position must not be inferred from cache length.

    Once the sliding window starts evicting entries the cache stops growing,
    so a length-derived offset hands every subsequent token the *same*
    position. Relative distances between recent tokens then collapse to zero
    and the model loses track of order -- with no crash and no loss signal,
    since this only happens during generation.
    """
    from scratchgpt.model import Block

    torch.manual_seed(0)
    model = GPT(config).eval()
    prompt_len = config.block_size - 2
    overrun = 8

    seen: list[int | None] = []
    original = Block.forward

    def spy(self, x, kv_cache=None, pos_offset=None):
        seen.append(pos_offset)
        return original(self, x, kv_cache, pos_offset)

    Block.forward = spy
    try:
        model.generate(
            torch.zeros((1, prompt_len), dtype=torch.long),
            max_new_tokens=overrun,
            temperature=0.0,
        )
    finally:
        Block.forward = original

    per_layer = seen[:: config.n_layer]
    # The first pass consumes the whole prompt; each later pass consumes one
    # token, and the final token is appended without another forward.
    assert per_layer == [0] + list(range(prompt_len, prompt_len + overrun - 1))
    # The last query sits past block_size, which is the whole point.
    assert per_layer[-1] > config.block_size


def test_greedy_generation_is_deterministic(model, config):
    idx = torch.zeros((1, 4), dtype=torch.long)
    a = model.generate(idx, max_new_tokens=6, temperature=0.0)
    b = model.generate(idx, max_new_tokens=6, temperature=0.0)
    assert torch.equal(a, b)


def test_gradients_reach_every_parameter(config):
    """A parameter with no gradient is dead weight and a silent bug."""
    torch.manual_seed(0)
    model = GPT(config)
    x, y = shifted_batch(config)
    _, loss, _ = model(x, targets=y)
    loss.backward()
    missing = [
        name for name, p in model.named_parameters()
        if p.requires_grad and (p.grad is None or not torch.isfinite(p.grad).all())
    ]
    assert not missing, f"no finite gradient for: {missing}"


def test_model_can_memorize_a_sequence(config):
    """End-to-end sanity: the loop must be able to drive loss to near zero."""
    torch.manual_seed(0)
    model = GPT(config)
    optimizer = model.configure_optimizers(0.0, 3e-3, (0.9, 0.95), "cpu")
    x, y = shifted_batch(config, batch=1)

    for _ in range(400):
        _, loss, _ = model(x, targets=y)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()

    assert loss.item() < 0.1, f"failed to overfit one sequence: loss {loss.item():.3f}"


def test_weight_tying_shares_one_tensor(config):
    model = GPT(config)
    assert model.lm_head.weight is model.token_embedding.weight
    untied = GPT(ModelConfig(**{**vars(config), "tie_embeddings": False}))
    assert untied.lm_head.weight is not untied.token_embedding.weight
    assert untied.num_parameters() > model.num_parameters()


def test_optimizer_excludes_norms_from_decay(config):
    model = GPT(config)
    groups = model.configure_optimizers(0.1, 1e-3, (0.9, 0.95), "cpu").param_groups
    decayed, undecayed = groups[0], groups[1]
    assert decayed["weight_decay"] == 0.1
    assert undecayed["weight_decay"] == 0.0
    assert all(p.dim() >= 2 for p in decayed["params"])
    assert all(p.dim() < 2 for p in undecayed["params"])


def test_sequence_longer_than_context_is_rejected(model, config):
    idx = torch.zeros((1, config.block_size + 1), dtype=torch.long)
    with pytest.raises(ValueError, match="exceeds block_size"):
        model(idx)


def test_rmsnorm_normalizes(config):
    norm = RMSNorm(64)
    x = torch.randn(4, 10, 64) * 17.0
    rms = norm(x).pow(2).mean(-1).sqrt()
    assert torch.allclose(rms, torch.ones_like(rms), atol=1e-3)


def test_rope_preserves_norm_and_encodes_relative_position():
    rope = RotaryEmbedding(head_dim=16, max_seq_len=32)
    x = torch.randn(1, 1, 8, 16)
    rotated = rope(x)
    # A rotation changes direction, never length.
    assert torch.allclose(x.norm(dim=-1), rotated.norm(dim=-1), atol=1e-4)

    # Same gap, different absolute positions -> same dot product.
    q, k = torch.randn(1, 1, 1, 16), torch.randn(1, 1, 1, 16)
    near = (rope(q, offset=2) * rope(k, offset=0)).sum()
    far = (rope(q, offset=7) * rope(k, offset=5)).sum()
    assert torch.allclose(near, far, atol=1e-4)


def test_head_dim_validation():
    with pytest.raises(ValueError, match="divisible"):
        ModelConfig(n_embd=100, n_head=7)
    with pytest.raises(ValueError, match="divisible"):
        ModelConfig(n_embd=64, n_head=8, n_kv_head=3)


def test_config_json_roundtrip(tmp_path, config):
    original = Config(model=config)
    path = tmp_path / "cfg.json"
    original.save(path)
    assert Config.load(path) == original


def test_config_overrides_coerce_strings():
    cfg = Config()
    cfg.override({"train.batch_size": "8", "train.learning_rate": "1e-3",
                  "model.tie_embeddings": "false"})
    assert cfg.train.batch_size == 8
    assert cfg.train.learning_rate == 1e-3
    assert cfg.model.tie_embeddings is False


def test_bad_override_is_rejected():
    with pytest.raises(ValueError, match="no field"):
        Config().override({"train.nonexistent": "1"})
    with pytest.raises(ValueError, match="model.<field>"):
        Config().override({"batch_size": "1"})
