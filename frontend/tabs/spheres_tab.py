import os
import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_spheres_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the spheres classification tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="3. Классификация сфер")

    create_input_field(
        frame,
        "Файл с определениями сфер (Excel/CSV):",
        "spheres_definitions_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_INPUT_DOCS_DIR", ""), "spheres.xlsx"
        ),
    )
    create_input_field(
        frame,
        "Выходной файл (после классификации сфер):",
        "spheres_output_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_with_spheres.xlsx",
        ),
    )
    return frame
