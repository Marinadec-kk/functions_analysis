import tkinter as tk
from tkinter import ttk
from frontend.components.input_field import create_input_field


def create_ai_config_tab(
    notebook: ttk.Notebook, entry_widgets: dict, current_config: dict
):
    """
    Creates the AI configuration tab.
    """
    frame = ttk.Frame(notebook)
    notebook.add(frame, text="Настройки AI")

    create_input_field(
        frame,
        "Базовый URL AI API:",
        "ai_api_base_url",
        entry_widgets,
        default_value=current_config.get("ai_api_base_url", ""),
    )

    ttk.Label(frame, text="AI API Ключ:").pack(padx=5, pady=5, anchor="w")
    entry_var_key = tk.StringVar(value=current_config.get("ai_api_key", ""))
    entry_key = ttk.Entry(frame, width=80, textvariable=entry_var_key, show="*")
    entry_key.pack(padx=5, pady=2, fill="x")
    entry_widgets["ai_api_key"] = entry_var_key

    create_input_field(
        frame,
        "Модель для эмбеддингов:",
        "embedding_model_name",
        entry_widgets,
        default_value=current_config.get(
            "embedding_model_name", "text-embedding-ada-002"
        ),
    )
    create_input_field(
        frame,
        "Модель для классификации типов:",
        "classification_model_name",
        entry_widgets,
        default_value=current_config.get("classification_model_name", "gpt-3.5-turbo"),
    )
    create_input_field(
        frame,
        "Модель для LLM верификации дубликатов:",
        "llm_duplicate_verification_model_name",
        entry_widgets,
        default_value=current_config.get(
            "llm_duplicate_verification_model_name", "gpt-3.5-turbo"
        ),
    )
    create_input_field(
        frame,
        "Количество повторных попыток API:",
        "api_max_retries",
        entry_widgets,
        default_value=str(current_config.get("api_max_retries", 3)),
    )
    create_input_field(
        frame,
        "Таймаут API (секунды):",
        "api_timeout",
        entry_widgets,
        default_value=str(current_config.get("api_timeout", 60)),
    )
    return frame
