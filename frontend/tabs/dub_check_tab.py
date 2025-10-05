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
        "Входной файл для проверки дубликатов:",
        "dub_check_input_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_with_candidates.xlsx",
        ),
    )

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

    # Prompt Editor
    ttk.Label(frame, text="Промпт для проверки дубликатов:").pack(
        padx=5, pady=(10, 0), anchor="w"
    )
    prompt_var = tk.StringVar(value=current_config.get("dub_check_prompt", ""))
    entry_widgets["dub_check_prompt"] = prompt_var
    prompt_text = tk.Text(frame, height=10, width=80)
    prompt_text.pack(padx=5, pady=2, fill="both", expand=True)
    prompt_text.insert("1.0", prompt_var.get())
    prompt_text.bind(
        "<KeyRelease>",
        lambda event: prompt_var.set(event.widget.get("1.0", tk.END)),
    )

    return frame
