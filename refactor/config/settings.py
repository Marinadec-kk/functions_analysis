"""
Global configuration defaults used across the refactored project.

The original scripts relied heavily on module-level constants; collecting them in a
single place keeps configuration discoverable and simplifies customisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class AIProfile:
    model: str
    max_tokens: int
    temperature: float = 1.0
    concurrency: int = 1
    retries: int = 3


DEFAULT_OPENAI_PROFILE = AIProfile(
    model="gpt-4o-mini",
    max_tokens=150,
    temperature=1.0,
    concurrency=30,
    retries=5,
)

DEFAULT_LOCAL_PROFILE = AIProfile(
    model="local-model",
    max_tokens=256,
    temperature=1.0,
    concurrency=4,
    retries=3,
)

DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B-GGUF"
DEFAULT_EMBEDDING_SERVER = "http://localhost:1234"
DEFAULT_EMBEDDING_BATCH = 64


def get_default_profiles() -> Dict[str, AIProfile]:
    return {
        "online": DEFAULT_OPENAI_PROFILE,
        "local": DEFAULT_LOCAL_PROFILE,
    }

