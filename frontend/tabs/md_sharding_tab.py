import os
import tkinter as tk
from tkinter import ttk
from frontend.components.file_selector import create_file_selector


def create_md_sharding_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the markdown sharding tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="7. Шардирование MD")

    create_file_selector(
        frame,
        "Выберите файл для анализа:",
        "input_file",
        entry_widgets,
        directory=os.path.join(os.getcwd(), "files"),
        file_extension=".xlsx",
    )

    return frame
