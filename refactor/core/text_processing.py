"""
Shared text processing helpers extracted from legacy scripts. These utilities do
not depend on any GUI components and can therefore be reused in tests or other
front-ends.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

ABBR_STOPWORDS = {
    "и",
    "по",
    "о",
    "в",
    "на",
    "об",
    "с",
    "при",
    "для",
    "над",
    "под",
    "из",
    "во",
    "со",
    "республика",
    "республики",
    "казахстан",
    "казахстана",
    "государственного",
    "учреждения",
}


def clean_control_characters(text: str) -> str:
    """Remove control characters that break formatting."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def fast_clean_and_format_text(text: str) -> str:
    """Normalize legal function entries."""
    if not text:
        return ""
    text = clean_control_characters(text)
    patterns_to_remove = [
        r"\s*сноска\s*\..*",
        r"\s*примечание\s*ИЗПИ!.*",
        r"\s*\(?вводится в действие.*?(\(|$)",
        r"\(порядок введения в действие см\. п\. \d+\)",
        r"\s*искл[ю]?че?н[оа]?.*",
        r"\s*действовал[аи]? до \d{2}\.\d{2}\.\d{4}.*",
        r";\s*от\s+\d{2}\.\d{2}\.\d{4}.*",
        r"\s*см\. п\. \d+",
    ]
    for pat in patterns_to_remove:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*\d+(?:[-.][\w]+)*\)\s*", "", text).strip()
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip().rstrip(";,").strip()


def create_abbreviation(name: str) -> str:
    """Create an abbreviation from *name* skipping known stop words."""
    if not isinstance(name, str) or not name.strip():
        return "GO"
    tokens = re.split(r"[\s\-]+", " ".join(name.split()))
    if all(tok.isupper() and len(tok) > 1 for tok in tokens):
        return tokens[0]
    letters = [
        w[0].upper() for w in tokens if w and w.lower() not in ABBR_STOPWORDS
    ]
    if letters:
        return "".join(letters)
    return tokens[0][:3].upper()


def normalize_whitespace(text: Optional[str]) -> str:
    """Collapse consecutive whitespace and return stripped text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()

