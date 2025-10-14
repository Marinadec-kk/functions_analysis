"""
Embedding helpers shared by the analysis tools.
"""

from __future__ import annotations

import time
from typing import Iterable, List, Optional

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore[assignment]


def get_embeddings_with_retry(
    client: OpenAI,
    model: str,
    texts: Iterable[str],
    retries: int = 3,
    delay: float = 1.0,
) -> Optional[List[List[float]]]:
    """
    Retrieve embeddings for ``texts`` using ``client`` with basic retry logic.
    Returns ``None`` if all attempts fail.
    """
    texts = list(texts)
    if not texts:
        return []

    for attempt in range(retries):
        try:
            response = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in response.data]
        except Exception:  # pragma: no cover - robustness
            if attempt + 1 >= retries:
                return None
            time.sleep(delay)
            delay *= 2
    return None

