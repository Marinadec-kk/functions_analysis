"""
Utilities for reading and writing structured data files used across the analysis
tools. Logic placed here is intentionally GUI agnostic so that it can be reused by
different front-ends (CLI, Tkinter, tests).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd
from docx import Document

try:
    import win32com.client as win32  # type: ignore[attr-defined]

    WIN32_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    win32 = None
    WIN32_AVAILABLE = False


class UnsupportedFileFormatError(ValueError):
    """Raised when a helper cannot process the requested file format."""


def read_doc_paragraphs_win32(filepath: str) -> List[str]:
    """Read paragraphs from legacy ``.doc`` documents using the COM interface."""
    if not WIN32_AVAILABLE:
        raise RuntimeError("pywin32 is required to read .doc files on Windows.")

    word = doc = None
    try:
        word = win32.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(filepath, ReadOnly=True)
        return [p.Range.Text for p in doc.Paragraphs]
    finally:
        if doc:
            doc.Close(0)
        if word:
            word.Quit()


def read_doc_paragraphs(filepath: str) -> List[str]:
    """Read paragraphs from a Word document."""
    low = filepath.lower()
    if low.endswith(".docx"):
        document = Document(filepath)
        return [p.text for p in document.paragraphs]
    if low.endswith(".doc"):
        return read_doc_paragraphs_win32(filepath)
    raise UnsupportedFileFormatError(f"Unsupported Word format: {filepath}")


def read_text_file(path: str, encoding: str = "utf-8") -> str:
    """Read a plain text file."""
    with open(path, "r", encoding=encoding) as fh:
        return fh.read()


def write_text_file(path: str, content: str, encoding: str = "utf-8") -> None:
    """Write plain text content to the specified file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(content)


def read_json(path: str, encoding: str = "utf-8") -> dict:
    """Load JSON content from *path*."""
    text = read_text_file(path, encoding=encoding)
    return json.loads(text)


def write_json(path: str, payload: dict, encoding: str = "utf-8", indent: int = 2) -> None:
    """Persist *payload* to JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=indent)


def read_table_auto(fpath: str) -> pd.DataFrame:
    """
    Load a table-like file into a :class:`pandas.DataFrame`. Supports Excel and CSV
    files with multiple common encodings/separators.
    """
    low = fpath.lower()
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(fpath)
    if low.endswith(".csv"):
        last_err: Optional[Exception] = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            for sep in (",", ";", "\t", "|"):
                try:
                    return pd.read_csv(fpath, encoding=enc, sep=sep)
                except Exception as exc:  # pragma: no cover - loop attempts
                    last_err = exc
        if last_err:
            raise last_err
    raise UnsupportedFileFormatError(f"Unsupported tabular format: {fpath}")


def ensure_directory(path: str) -> Path:
    """Ensure that directory *path* (file or folder) exists and return it as Path."""
    resolved = Path(path)
    if resolved.suffix:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    else:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def write_dataframe(
    df: pd.DataFrame,
    output_path: str,
    index: bool = False,
    engine: Optional[str] = "openpyxl",
) -> None:
    """Persist a dataframe to Excel or CSV depending on the suffix."""
    ensure_directory(output_path)
    if output_path.lower().endswith((".xlsx", ".xls")):
        df.to_excel(output_path, index=index, engine=engine)
        return
    if output_path.lower().endswith(".csv"):
        df.to_csv(output_path, index=index, encoding="utf-8-sig")
        return
    raise UnsupportedFileFormatError(f"Unsupported export format: {output_path}")


def list_files(root: str, extensions: Iterable[str]) -> List[str]:
    """Return all files in *root* (recursively) that end with the provided extensions."""
    normalized = {ext.lower() for ext in extensions}
    result: List[str] = []
    for base, _, files in os.walk(root):
        for fname in files:
            if any(fname.lower().endswith(ext) for ext in normalized):
                result.append(os.path.join(base, fname))
    return result

