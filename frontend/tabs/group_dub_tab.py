import os
import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field
from frontend.components.file_selector import create_file_selector


def create_group_dub_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the grouping and duplicates tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="4. Группировка и дубликаты")

    create_file_selector(
        frame,
        "Выберите файл для анализа:",
        "input_file",
        entry_widgets,
        directory=os.path.join(os.getcwd(), "files"),
        file_extension=".xlsx",
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

    # --- Новые поля для группировки и универсальных функций ---
    adv_frame = ttk.LabelFrame(frame, text="Расширенные настройки")
    adv_frame.pack(fill=tk.X, padx=5, pady=10)

    ttk.Label(adv_frame, text="Столбцы для группировки (через запятую):").pack(
        padx=5, pady=5, anchor="w"
    )
    entry_var_grouping = tk.StringVar(
        value=str(current_config.get("grouping_cols", ""))
    )
    entry_grouping = ttk.Entry(adv_frame, width=80, textvariable=entry_var_grouping)
    entry_grouping.pack(padx=5, pady=2, fill="x")
    entry_widgets["grouping_cols"] = entry_var_grouping

    create_input_field(
        adv_frame,
        "JSON универс. функций:",
        "universal_json_file",
        entry_widgets,
        file_ext=".json",
        default_value=str(current_config.get("universal_json_file", "")),
    )

    ttk.Label(adv_frame, text="Порог для универсальных (0.0-1.0):").pack(
        padx=5, pady=5, anchor="w"
    )
    entry_var_univ = tk.StringVar(value=str(current_config.get("univ_threshold", 0.9)))
    entry_univ = ttk.Entry(adv_frame, width=80, textvariable=entry_var_univ)
    entry_univ.pack(padx=5, pady=2, fill="x")
    entry_widgets["univ_threshold"] = entry_var_univ

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
