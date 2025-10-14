"""
Utilities for creating AI/LLM clients with consistent configuration. The logic in
the original scripts repeated URL normalization and client creation; consolidating
it here ensures a single source of truth.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

try:
    from openai import OpenAI
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore[assignment]
    httpx = None  # type: ignore[assignment]


def prepare_api_base_url(url: str) -> str:
    """Return a normalized OpenAI-compatible base URL."""
    if not url:
        return ""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


@dataclass
class AIClientConfig:
    base_url: str
    api_key: str = ""
    timeout: float = 60.0


def create_openai_client(config: AIClientConfig) -> OpenAI:
    """Instantiate an :class:`openai.OpenAI` client based on ``config``."""
    if OpenAI is None or httpx is None:
        raise RuntimeError(
            "openai/httpx are not installed. Install via 'pip install openai httpx'."
        )
    return OpenAI(
        base_url=prepare_api_base_url(config.base_url),
        api_key=config.api_key or "not-needed",
        http_client=httpx.Client(timeout=config.timeout),
    )


def build_client_pool(
    configs: Iterable[AIClientConfig],
) -> List[OpenAI]:
    """Create a list of clients, one per configuration entry."""
    return [create_openai_client(cfg) for cfg in configs]


def backoff_delays(initial_delay: float = 1.0, multiplier: float = 2.0, max_retries: int = 3) -> List[float]:
    """Return a list of sleep durations for exponential backoff."""
    delays = []
    delay = initial_delay
    for _ in range(max_retries):
        delays.append(delay)
        delay *= multiplier
    return delays

