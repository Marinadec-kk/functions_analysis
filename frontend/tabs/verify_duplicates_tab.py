import os
import tkinter as tk
from tkinter import ttk
from frontend.components.file_selector import create_file_selector


def create_verify_duplicates_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the duplicates check tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="5. Проверка дубликатов")

    create_file_selector(
        frame,
        "Выберите файл для анализа:",
        "input_file",
        entry_widgets,
        directory=os.path.join(os.getcwd(), "files"),
        file_extension=".xlsx",
    )

    # Prompt Editor
    ttk.Label(frame, text="Промпт для проверки дубликатов:").pack(
        padx=5, pady=(10, 0), anchor="w"
    )
    prompt_var = tk.StringVar(value=current_config.get("verify_duplicates_prompt", ""))
    entry_widgets["verify_duplicates_prompt"] = prompt_var
    prompt_text = tk.Text(frame, height=10, width=80)
    prompt_text.pack(padx=5, pady=2, fill="both", expand=True)
    prompt_text.insert("1.0", prompt_var.get())
    prompt_text.bind(
        "<KeyRelease>",
        lambda event: prompt_var.set(event.widget.get("1.0", tk.END)),
    )

    return frame
