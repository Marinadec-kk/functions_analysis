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
        "Входной файл для классификации типов:",
        "types_input_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""), "parsed_functions.xlsx"
        ),
    )

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

    # Prompt Editor
    ttk.Label(frame, text="Промпт для классификации типов:").pack(
        padx=5, pady=(10, 0), anchor="w"
    )
    prompt_var = tk.StringVar(value=current_config.get("types_prompt", ""))
    entry_widgets["types_prompt"] = prompt_var
    prompt_text = tk.Text(frame, height=10, width=80)
    prompt_text.pack(padx=5, pady=2, fill="both", expand=True)
    prompt_text.insert("1.0", prompt_var.get())
    prompt_text.bind(
        "<KeyRelease>",
        lambda event: prompt_var.set(event.widget.get("1.0", tk.END)),
    )

    return frame
