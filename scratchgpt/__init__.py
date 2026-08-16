"""scratchgpt -- a code language model built and trained from nothing."""

from .config import Config, ModelConfig, TrainConfig
from .model import GPT
from .tokenizer import Tokenizer

__version__ = "0.1.0"
__all__ = ["Config", "ModelConfig", "TrainConfig", "GPT", "Tokenizer"]
