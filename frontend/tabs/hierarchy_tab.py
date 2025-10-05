import os
import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_hierarchy_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the hierarchy analysis tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="6. Иерархический анализ")

    create_input_field(
        frame,
        "Входной файл для иерархического анализа:",
        "hierarchy_input_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_verified_duplicates.xlsx",
        ),
    )

    create_input_field(
        frame,
        "Выходной файл (после иерархического анализа):",
        "hierarchy_output_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_hierarchical_analysis.xlsx",
        ),
    )
    return frame
