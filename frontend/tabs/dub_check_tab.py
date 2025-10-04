import os
import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_dub_check_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the duplicates check tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="5. Проверка дубликатов")

    create_input_field(
        frame,
        "Выходной файл (после проверки дубликатов):",
        "duplicates_verified_output_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_verified_duplicates.xlsx",
        ),
    )
    return frame
