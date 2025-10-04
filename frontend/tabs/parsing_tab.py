import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_parsing_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the parsing tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="1. Парсинг")

    create_input_field(
        frame,
        "Входная папка с документами:",
        "parsing_input_folder",
        entry_widgets,
        is_folder=True,
        default_value=current_config.get("DEFAULT_INPUT_DOCS_DIR", ""),
    )
    return frame
