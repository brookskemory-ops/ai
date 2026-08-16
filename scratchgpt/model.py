"""A decoder-only transformer, written out layer by layer.

PyTorch supplies tensors, autograd, and matmul kernels. Everything above that --
attention, rotary embeddings, normalization, the MLP, the residual stream, the
initialization scheme -- is spelled out here, so there is no pretrained anything
and no black box between the config and the parameters.

Architecture is the modern decoder stack: pre-norm residual blocks, RMSNorm,
rotary position embeddings, grouped-query attention, and a SwiGLU MLP.
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import ModelConfig


class RMSNorm(nn.Module):
    """Normalize by root-mean-square, then rescale per channel.

    Cheaper than LayerNorm -- no mean subtraction, no bias -- and works just as
    well in practice, which is why current LLMs use it.
    """

    def __init__(self, dim: int, eps: float = 1e-5) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Compute in float32 even under bf16 autocast: the sum of squares is
        # where low precision does real damage.
        dtype = x.dtype
        x = x.float()
        x = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)
        return (x * self.weight.float()).to(dtype)


class RotaryEmbedding(nn.Module):
    """Rotary position embeddings (RoPE).

    Instead of adding a position vector, each pair of channels in q and k is
    rotated by an angle proportional to its absolute position. The dot product
    between two rotated vectors then depends only on their *relative* distance,
    which is the property attention actually wants.
    """

    def __init__(self, head_dim: int, max_seq_len: int, theta: float = 10000.0) -> None:
        super().__init__()
        if head_dim % 2 != 0:
            raise ValueError(f"head_dim must be even for RoPE, got {head_dim}")
        self.head_dim = head_dim
        self.theta = theta
        self._build_cache(max_seq_len)

    def _build_cache(self, seq_len: int) -> None:
        # Double rather than grow to exactly what was asked for, so a long
        # generation rebuilds the table a handful of times instead of per token.
        seq_len = max(seq_len, 2 * getattr(self, "cached_len", 0))
        # Channel pair j rotates at frequency theta^(-2j/head_dim): the first
        # pairs spin fast and encode local order, the last barely move and carry
        # long-range position.
        inv_freq = 1.0 / (
            self.theta ** (torch.arange(0, self.head_dim, 2).float() / self.head_dim)
        )
        pos = torch.arange(seq_len).float()
        angles = torch.outer(pos, inv_freq)  # (seq_len, head_dim/2)
        self.register_buffer("cos_cached", angles.cos(), persistent=False)
        self.register_buffer("sin_cached", angles.sin(), persistent=False)
        self.cached_len = seq_len

    def forward(self, x: torch.Tensor, offset: int = 0) -> torch.Tensor:
        """Rotate `x` of shape (B, n_head, T, head_dim).

        `offset` is the absolute position of the first token, which matters when
        generating with a KV cache.
        """
        seq_len = x.shape[-2]
        if offset + seq_len > self.cached_len:
            self._build_cache(offset + seq_len)
            self.cos_cached = self.cos_cached.to(x.device)
            self.sin_cached = self.sin_cached.to(x.device)

        cos = self.cos_cached[offset : offset + seq_len].to(x.dtype)
        sin = self.sin_cached[offset : offset + seq_len].to(x.dtype)

        # Split channels into the two halves of each rotated pair.
        x1, x2 = x.float().chunk(2, dim=-1)
        cos, sin = cos.float(), sin.float()
        rotated = torch.cat([x1 * cos - x2 * sin, x1 * sin + x2 * cos], dim=-1)
        return rotated.to(x.dtype)


class CausalSelfAttention(nn.Module):
    """Multi-head causal attention with grouped key/value heads.

    Each token builds a query, and scores it against the keys of every token at
    or before it. The mask is what makes the model a language model: position t
    can never see t+1, so one forward pass supplies a training signal at every
    position at once.

    With n_kv_head < n_head, several query heads share one key/value head. That
    shrinks the KV cache during generation at almost no quality cost.
    """

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.n_head = config.n_head
        self.n_kv_head = config.n_kv_head
        self.head_dim = config.head_dim
        self.n_rep = self.n_head // self.n_kv_head
        self.dropout = config.dropout

        self.q_proj = nn.Linear(config.n_embd, self.n_head * self.head_dim, bias=config.bias)
        self.k_proj = nn.Linear(config.n_embd, self.n_kv_head * self.head_dim, bias=config.bias)
        self.v_proj = nn.Linear(config.n_embd, self.n_kv_head * self.head_dim, bias=config.bias)
        self.o_proj = nn.Linear(self.n_head * self.head_dim, config.n_embd, bias=config.bias)
        self.resid_dropout = nn.Dropout(config.dropout)

        self.rope = RotaryEmbedding(self.head_dim, config.block_size, config.rope_theta)

    def forward(
        self,
        x: torch.Tensor,
        kv_cache: tuple[torch.Tensor, torch.Tensor] | None = None,
        pos_offset: int | None = None,
    ) -> tuple[torch.Tensor, tuple[torch.Tensor, torch.Tensor] | None]:
        B, T, C = x.shape

        q = self.q_proj(x).view(B, T, self.n_head, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(B, T, self.n_kv_head, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(B, T, self.n_kv_head, self.head_dim).transpose(1, 2)

        # Past tokens were rotated by their own absolute positions already, so
        # the new tokens continue from there. The caller passes the absolute
        # position explicitly because it does not always equal the cache length:
        # once a sliding window starts dropping old entries, the cache stops
        # growing while positions keep advancing. Deriving the offset from the
        # cache length there would hand every new token the same position and
        # silently corrupt every relative distance.
        cached_len = kv_cache[0].shape[-2] if kv_cache is not None else 0
        offset = cached_len if pos_offset is None else pos_offset
        q = self.rope(q, offset)
        k = self.rope(k, offset)

        if kv_cache is not None:
            k = torch.cat([kv_cache[0], k], dim=-2)
            v = torch.cat([kv_cache[1], v], dim=-2)
        new_cache = (k, v) if kv_cache is not None or not self.training else None

        # Fan each shared kv head back out to its group of query heads.
        if self.n_rep > 1:
            k = k.repeat_interleave(self.n_rep, dim=1)
            v = v.repeat_interleave(self.n_rep, dim=1)

        # With a cache, the queries are the newest tokens and every cached key is
        # in the past, so the mask is only needed when T > 1.
        y = F.scaled_dot_product_attention(
            q, k, v,
            dropout_p=self.dropout if self.training else 0.0,
            is_causal=(cached_len == 0 and T > 1),
        )

        y = y.transpose(1, 2).contiguous().view(B, T, -1)
        return self.resid_dropout(self.o_proj(y)), new_cache


class SwiGLU(nn.Module):
    """Gated feed-forward network.

    Two projections go up: one is the content, the other is a SiLU-activated
    gate that decides how much of each channel survives. Multiplying them lets
    the layer suppress features conditionally, which a single activation cannot.
    """

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        hidden = config.hidden_dim
        self.gate_proj = nn.Linear(config.n_embd, hidden, bias=config.bias)
        self.up_proj = nn.Linear(config.n_embd, hidden, bias=config.bias)
        self.down_proj = nn.Linear(hidden, config.n_embd, bias=config.bias)
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dropout(self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x)))


class Block(nn.Module):
    """One transformer layer: attention, then MLP, each on a residual branch.

    Pre-norm -- normalize going *into* each sublayer, add the raw output back --
    leaves an unobstructed path from the embeddings to the loss, which is what
    lets deep stacks train without warmup tricks.
    """

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.attn_norm = RMSNorm(config.n_embd)
        self.attn = CausalSelfAttention(config)
        self.mlp_norm = RMSNorm(config.n_embd)
        self.mlp = SwiGLU(config)

    def forward(self, x, kv_cache=None, pos_offset=None):
        attn_out, new_cache = self.attn(self.attn_norm(x), kv_cache, pos_offset)
        x = x + attn_out
        x = x + self.mlp(self.mlp_norm(x))
        return x, new_cache


class GPT(nn.Module):
    """The whole model: embeddings, N blocks, a final norm, and a vocab head."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config

        self.token_embedding = nn.Embedding(config.vocab_size, config.n_embd)
        self.embed_dropout = nn.Dropout(config.dropout)
        self.blocks = nn.ModuleList(Block(config) for _ in range(config.n_layer))
        self.final_norm = RMSNorm(config.n_embd)
        self.lm_head = nn.Linear(config.n_embd, config.vocab_size, bias=False)

        if config.tie_embeddings:
            # One matrix, two jobs: it maps ids to vectors on the way in and
            # scores vectors against those same ids on the way out.
            self.lm_head.weight = self.token_embedding.weight

        self.apply(self._init_weights)

        # Every layer adds two branches into the residual stream, so without
        # this the stream's variance grows with depth. Scaling the output
        # projections by 1/sqrt(2 * n_layer) keeps it flat at initialization.
        scale = 1.0 / math.sqrt(2 * config.n_layer)
        for name, param in self.named_parameters():
            if name.endswith(("o_proj.weight", "down_proj.weight")):
                torch.nn.init.normal_(param, mean=0.0, std=0.02 * scale)

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def num_parameters(self, trainable_only: bool = True) -> int:
        params = self.parameters()
        if trainable_only:
            params = (p for p in params if p.requires_grad)
        # Tied weights appear twice in .parameters(); count each tensor once.
        return sum(p.numel() for p in {id(p): p for p in params}.values())

    def forward(
        self,
        idx: torch.Tensor,
        targets: torch.Tensor | None = None,
        kv_caches: list | None = None,
        pos_offset: int | None = None,
    ):
        """`idx` is (B, T) token ids. With `targets`, also returns the loss.

        `pos_offset` is the absolute position of the first token in `idx`. It
        defaults to the cache length, which is right until a sliding window
        starts evicting entries; `generate` tracks it separately for that reason.
        """
        _, T = idx.shape
        past = kv_caches[0][0].shape[-2] if kv_caches and kv_caches[0] is not None else 0
        # The bound is on how many keys attention sees at once, not on how far
        # the absolute position has advanced.
        if past + T > self.config.block_size:
            raise ValueError(
                f"sequence of {past + T} tokens exceeds block_size "
                f"{self.config.block_size}"
            )

        x = self.embed_dropout(self.token_embedding(idx))

        new_caches = []
        for i, block in enumerate(self.blocks):
            cache = kv_caches[i] if kv_caches is not None else None
            x, updated = block(x, cache, pos_offset)
            new_caches.append(updated)

        x = self.final_norm(x)

        if targets is None:
            # Generation only needs the last position, so skip the rest of the
            # vocab projection -- it is the single most expensive matmul here.
            logits = self.lm_head(x[:, [-1], :])
            return logits, None, new_caches

        logits = self.lm_head(x)
        loss = F.cross_entropy(
            logits.view(-1, logits.size(-1)),
            targets.reshape(-1),
            ignore_index=-1,
        )
        return logits, loss, new_caches

    def configure_optimizers(
        self,
        weight_decay: float,
        learning_rate: float,
        betas: tuple[float, float],
        device_type: str,
    ) -> torch.optim.Optimizer:
        """AdamW with decay on matrices only.

        Weight decay is a prior that a *direction* should stay small. That makes
        sense for a matrix mixing many inputs; applied to a norm gain or a bias
        it just fights the layer's job, so those go in an undecayed group.
        """
        decayed, undecayed = [], []
        for param in {id(p): p for p in self.parameters() if p.requires_grad}.values():
            (decayed if param.dim() >= 2 else undecayed).append(param)

        groups = [
            {"params": decayed, "weight_decay": weight_decay},
            {"params": undecayed, "weight_decay": 0.0},
        ]
        # The fused kernel is a large win on CUDA and unavailable elsewhere.
        fused = device_type == "cuda"
        return torch.optim.AdamW(groups, lr=learning_rate, betas=betas, fused=fused)

    @torch.no_grad()
    def generate(
        self,
        idx: torch.Tensor,
        max_new_tokens: int,
        temperature: float = 0.8,
        top_k: int | None = 40,
        top_p: float | None = 0.95,
        stop_token: int | None = None,
        use_cache: bool = True,
    ) -> torch.Tensor:
        """Extend `idx` one token at a time, sampling from the model.

        temperature < 1 sharpens the distribution, > 1 flattens it, and 0 is
        greedy. top_k and top_p trim the tail so a long generation does not get
        derailed by one unlucky draw from thousands of near-zero options.
        """
        self.eval()
        caches = [None] * len(self.blocks) if use_cache else None
        cursor = idx
        # Absolute position of the first token in `cursor`. Tracked separately
        # from the cache length, which stops growing once the window slides.
        pos = 0

        for _ in range(max_new_tokens):
            # Once the context is full, drop from the left. With a cache active
            # that means rebuilding it, so trim the cache instead of the input.
            if caches is not None and caches[0] is not None:
                past = caches[0][0].shape[-2]
                if past + cursor.shape[1] > self.config.block_size:
                    keep = self.config.block_size - cursor.shape[1]
                    caches = [(k[..., -keep:, :], v[..., -keep:, :]) for k, v in caches]
            elif caches is None and cursor.shape[1] > self.config.block_size:
                # No cache: the whole prefix is re-read each step, so the window
                # restarts at position 0. RoPE is relative, so the distances
                # inside the window are the same either way.
                cursor = cursor[:, -self.config.block_size :]

            logits, _, caches_out = self(cursor, kv_caches=caches, pos_offset=pos)
            if caches is not None:
                caches = caches_out
                pos += cursor.shape[1]

            logits = logits[:, -1, :]

            if temperature == 0.0:
                next_token = logits.argmax(dim=-1, keepdim=True)
            else:
                logits = logits / temperature
                if top_k is not None:
                    k = min(top_k, logits.size(-1))
                    threshold = torch.topk(logits, k, dim=-1).values[:, [-1]]
                    logits = logits.masked_fill(logits < threshold, float("-inf"))
                if top_p is not None and top_p < 1.0:
                    ordered, order = torch.sort(logits, descending=True, dim=-1)
                    cumulative = torch.softmax(ordered, dim=-1).cumsum(dim=-1)
                    # Shift right so the token that crosses the threshold stays.
                    drop = cumulative - torch.softmax(ordered, dim=-1) > top_p
                    ordered = ordered.masked_fill(drop, float("-inf"))
                    logits = torch.empty_like(logits).scatter_(-1, order, ordered)
                probs = torch.softmax(logits, dim=-1)
                next_token = torch.multinomial(probs, num_samples=1)

            idx = torch.cat([idx, next_token], dim=1)
            # With a cache the model has already seen everything but the newest
            # token; without one it must re-read the whole prefix each step.
            cursor = next_token if caches is not None else idx

            if stop_token is not None and (next_token == stop_token).all():
                break

        return idx
