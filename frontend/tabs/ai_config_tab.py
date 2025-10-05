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

    # Helper for creating simple text entries without a button
    def _create_text_entry(parent, label_text, config_key, default_value, show=None):
        ttk.Label(parent, text=label_text).pack(padx=5, pady=5, anchor="w")
        entry_var = tk.StringVar(value=default_value)
        entry = ttk.Entry(parent, width=80, textvariable=entry_var, show=show)
        entry.pack(padx=5, pady=2, fill="x")
        entry_widgets[config_key] = entry_var

    # --- General API Settings ---
    api_frame = ttk.LabelFrame(frame, text="Общие настройки API")
    api_frame.pack(fill="x", padx=5, pady=5, expand=True)

    _create_text_entry(
        api_frame,
        "Базовый URL AI API:",
        "ai_api_base_url",
        current_config.get("ai_api_base_url", ""),
    )
    _create_text_entry(
        api_frame,
        "AI API Ключ:",
        "ai_api_key",
        current_config.get("ai_api_key", ""),
        show="*",
    )

    # --- LLM Models Settings ---
    llm_frame = ttk.LabelFrame(frame, text="Настройки моделей LLM")
    llm_frame.pack(fill="x", padx=5, pady=5, expand=True)

    _create_text_entry(
        llm_frame,
        "Модель для классификации типов:",
        "classification_model_name",
        current_config.get("classification_model_name", "gpt-3.5-turbo"),
    )
    _create_text_entry(
        llm_frame,
        "Модель для LLM верификации дубликатов:",
        "llm_duplicate_verification_model_name",
        current_config.get("llm_duplicate_verification_model_name", "gpt-3.5-turbo"),
    )

    # --- Embedding Models Settings ---
    embed_frame = ttk.LabelFrame(frame, text="Настройки моделей эмбеддингов")
    embed_frame.pack(fill="x", padx=5, pady=5, expand=True)

    _create_text_entry(
        embed_frame,
        "Модель для эмбеддингов:",
        "embedding_model_name",
        current_config.get("embedding_model_name", "text-embedding-ada-002"),
    )

    # --- Performance and Network Settings ---
    perf_frame = ttk.LabelFrame(frame, text="Производительность и сеть")
    perf_frame.pack(fill="x", padx=5, pady=5, expand=True)

    _create_text_entry(
        perf_frame,
        "Количество повторных попыток API:",
        "api_max_retries",
        str(current_config.get("api_max_retries", 3)),
    )
    _create_text_entry(
        perf_frame,
        "Таймаут API (секунды):",
        "api_timeout",
        str(current_config.get("api_timeout", 60)),
    )
    _create_text_entry(
        perf_frame,
        "Кол-во воркеров AI:",
        "ai_workers",
        str(current_config.get("ai_workers", 10)),
    )
    _create_text_entry(
        perf_frame,
        "Кол-во воркеров для эмбеддингов:",
        "embedding_workers",
        str(current_config.get("embedding_workers", 4)),
    )
    _create_text_entry(
        perf_frame,
        "Воркеров на локальный сервер:",
        "workers_per_local_server",
        str(current_config.get("workers_per_local_server", 4)),
    )
    _create_text_entry(
        perf_frame,
        "Размер пакета (batch size):",
        "batch_size",
        str(current_config.get("batch_size", 32)),
    )
    _create_text_entry(
        perf_frame,
        "Задержка при ошибке API (сек):",
        "api_delay",
        str(current_config.get("api_delay", 2.0)),
    )
    return frame
