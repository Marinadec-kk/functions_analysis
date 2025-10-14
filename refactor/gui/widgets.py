"""
Reusable Tkinter widget helpers shared by GUI applications.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Optional


def create_file_row(
    parent: tk.Widget,
    label: str,
    textvariable: tk.StringVar,
    select_command: Callable[[], None],
    button_text: str = "...",
    entry_kwargs: Optional[dict] = None,
) -> ttk.Frame:
    """Create a labelled entry with a button that triggers ``select_command``."""
    entry_kwargs = entry_kwargs or {}
    row = ttk.Frame(parent)
    row.pack(fill=tk.X, pady=5, padx=5)
    ttk.Label(row, text=label, width=25).pack(side=tk.LEFT)
    ttk.Entry(row, textvariable=textvariable, **entry_kwargs).pack(
        side=tk.LEFT, expand=True, fill=tk.X, padx=5
    )
    ttk.Button(row, text=button_text, command=select_command, width=4).pack(side=tk.LEFT)
    return row


def create_entry_row(
    parent: tk.Widget,
    label: str,
    textvariable: tk.StringVar,
    width: int = 25,
    **entry_kwargs,
) -> ttk.Frame:
    """Create a label + entry row in ``parent`` and return the created frame."""
    row = ttk.Frame(parent)
    row.pack(fill=tk.X, pady=3, padx=5)
    ttk.Label(row, text=label, width=width).pack(side=tk.LEFT)
    ttk.Entry(row, textvariable=textvariable, **entry_kwargs).pack(
        side=tk.LEFT, expand=True, fill=tk.X, padx=5
    )
    return row


def ask_directory(title: str) -> str:
    """Wrapper over :func:`filedialog.askdirectory`."""
    return filedialog.askdirectory(title=title)


def ask_open_file(title: str, **kwargs) -> str:
    return filedialog.askopenfilename(title=title, **kwargs)


def ask_save_file(title: str, **kwargs) -> str:
    return filedialog.asksaveasfilename(title=title, **kwargs)

