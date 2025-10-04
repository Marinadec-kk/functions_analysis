import os
import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_types_tab(notebook: ttk.Notebook, entry_widgets: dict, current_config: dict):
    """
    Creates the types classification tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="2. Классификация типов")

    create_input_field(
        frame,
        "Выходной файл (после классификации типов):",
        "types_output_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_with_types.xlsx",
        ),
    )
    return frame
