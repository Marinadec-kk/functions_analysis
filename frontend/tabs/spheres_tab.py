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
        "Входной файл для классификации сфер:",
        "spheres_input_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_with_types.xlsx",
        ),
    )
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

    # Prompt Editor
    ttk.Label(frame, text="Промпт для классификации сфер:").pack(
        padx=5, pady=(10, 0), anchor="w"
    )
    prompt_var = tk.StringVar(value=current_config.get("spheres_prompt", ""))
    entry_widgets["spheres_prompt"] = prompt_var
    prompt_text = tk.Text(frame, height=10, width=80)
    prompt_text.pack(padx=5, pady=2, fill="both", expand=True)
    prompt_text.insert("1.0", prompt_var.get())
    prompt_text.bind(
        "<KeyRelease>",
        lambda event: prompt_var.set(event.widget.get("1.0", tk.END)),
    )

    return frame
