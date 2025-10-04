import os
import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_group_dub_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the grouping and duplicates tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="4. Группировка и дубликаты")

    create_input_field(
        frame,
        "Порог сходства для дубликатов (0.0-1.0):",
        "similarity_threshold",
        entry_widgets,
        default_value=str(current_config.get("similarity_threshold", 0.8)),
    )
    create_input_field(
        frame,
        "Выходной файл (после группировки и кандидатов):",
        "group_dub_output_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_with_candidates.xlsx",
        ),
    )
    return frame
