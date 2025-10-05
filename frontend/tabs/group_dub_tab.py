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
        "Входной файл для группировки:",
        "group_dub_input_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=os.path.join(
            current_config.get("DEFAULT_OUTPUT_DIR", ""),
            "functions_with_spheres.xlsx",
        ),
    )

    ttk.Label(frame, text="Порог сходства для дубликатов (0.0-1.0):").pack(
        padx=5, pady=5, anchor="w"
    )
    entry_var_sim = tk.StringVar(
        value=str(current_config.get("similarity_threshold", 0.8))
    )
    entry_sim = ttk.Entry(frame, width=80, textvariable=entry_var_sim)
    entry_sim.pack(padx=5, pady=2, fill="x")
    entry_widgets["similarity_threshold"] = entry_var_sim

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

    # Prompt Editor
    ttk.Label(frame, text="Промпт для AI-верификации дубликатов:").pack(
        padx=5, pady=(10, 0), anchor="w"
    )
    prompt_var = tk.StringVar(value=current_config.get("group_dub_prompt", ""))
    entry_widgets["group_dub_prompt"] = prompt_var
    prompt_text = tk.Text(frame, height=10, width=80)
    prompt_text.pack(padx=5, pady=2, fill="both", expand=True)
    prompt_text.insert("1.0", prompt_var.get())
    prompt_text.bind(
        "<KeyRelease>",
        lambda event: prompt_var.set(event.widget.get("1.0", tk.END)),
    )

    return frame
