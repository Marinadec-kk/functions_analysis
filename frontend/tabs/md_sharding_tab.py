import os
import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_md_sharding_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the markdown sharding tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="7. Шардирование MD")

    create_input_field(
        frame,
        "Выходная папка для MD-отчетов:",
        "markdown_output_dir",
        entry_widgets,
        is_folder=True,
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""), "markdown_reports"
        ),
    )
    return frame
