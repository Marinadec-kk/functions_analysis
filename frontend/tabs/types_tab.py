import os
import tkinter as tk
from tkinter import ttk
from frontend.components.file_selector import create_file_selector
from frontend.components.input_field import create_input_field


def create_types_tab(notebook: ttk.Notebook, entry_widgets: dict, current_config: dict):
    """
    Creates the types classification tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="2. Классификация типов")

    # Use the new file selector
    create_file_selector(
        frame,
        "Выберите файл для анализа:",
        "input_file",  # This key will be used to get the selected file
        entry_widgets,
        directory=os.path.join(os.getcwd(), "files"),  # Scan the 'files' directory
        file_extension=".xlsx",
    )

    # --- Золотой стандарт ---
    create_input_field(
        frame,
        'Файл "Золотого стандарта" (опционально):',
        "gold_standard_file",
        entry_widgets,
        file_ext=".xlsx",
        default_value=current_config.get("gold_standard_file", ""),
    )

    # --- Редактор Промптов ---
    prompts_notebook = ttk.Notebook(frame)
    prompts_notebook.pack(padx=5, pady=(10, 5), fill="both", expand=True)

    def _create_prompt_tab(notebook, text, key):
        tab_frame = ttk.Frame(notebook, padding=5)
        notebook.add(tab_frame, text=text)

        prompt_var = tk.StringVar(value=current_config.get(key, ""))
        entry_widgets[key] = prompt_var

        prompt_text = tk.Text(tab_frame, height=10, width=80, wrap=tk.WORD)
        prompt_text.pack(fill="both", expand=True)
        prompt_text.insert("1.0", prompt_var.get())
        prompt_text.bind(
            "<KeyRelease>",
            lambda event, v=prompt_var: v.set(event.widget.get("1.0", tk.END)),
        )
        return tab_frame

    _create_prompt_tab(
        prompts_notebook, "Промпт 1 (Классификация)", "prompt_template_typology_1"
    )
    _create_prompt_tab(
        prompts_notebook, "Промпт 2 (Верификация)", "prompt_template_typology_2"
    )
    _create_prompt_tab(
        prompts_notebook, "Промпт 3 (Арбитраж)", "prompt_template_typology_3"
    )

    return frame
