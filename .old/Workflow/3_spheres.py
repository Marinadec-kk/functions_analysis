# Workflow/3_spheres.py (Refactored)
import tkinter as tk
from tkinter import ttk
import os
import sys

# Добавляем путь к общим утилитам
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

from shared_utils.base_app import BaseAppTemplate
from shared_utils.gui_components import (
    FileSelectionWidget,
    AISettingsFrame,
    AdvancedSettingsFrame,
    ProgressPanel,
)


class SphereClassifierApp(BaseAppTemplate):
    def __init__(self, root: tk.Tk):
        super().__init__(root, "Классификатор по сферам v4.0 (Unified)")

    def _create_config_vars(self) -> dict:
        defaults = super()._create_config_vars()
        defaults.update(
            {
                "spheres_file": tk.StringVar(),
                "functions_file": tk.StringVar(),  # input_file будет для функций
                "output_file": tk.StringVar(),  # output_dir будет для этого
                "embed_server_url": tk.StringVar(value="http://localhost:1234"),
                "embed_model": tk.StringVar(value="Qwen/Qwen3-Embedding-8B-GGUF"),
                "top_k_filter": tk.IntVar(value=90),
                "classification_batch_size": tk.IntVar(value=20),
                "system_prompt": tk.StringVar(value="PROMPT..."),  # Заглушка
            }
        )
        # Переназначаем стандартные переменные для ясности
        defaults["input_file"] = defaults["functions_file"]
        defaults["output_dir"] = defaults["output_file"]
        return defaults

    def _build_custom_ui(self):
        # --- Файлы ---
        files_frame = ttk.LabelFrame(self.left_panel, text="Файлы")
        files_frame.pack(fill=tk.X, pady=5, padx=5)
        FileSelectionWidget(
            files_frame, "Файл со сферами:", self.config_vars["spheres_file"]
        ).pack(fill=tk.X, pady=2)
        FileSelectionWidget(
            files_frame, "Файл с функциями:", self.config_vars["functions_file"]
        ).pack(fill=tk.X, pady=2)
        FileSelectionWidget(
            files_frame, "Итоговый файл:", self.config_vars["output_file"]
        ).pack(fill=tk.X, pady=2)

        # --- Параметры обработки ---
        proc_frame = ttk.LabelFrame(self.left_panel, text="Параметры обработки")
        proc_frame.pack(fill=tk.X, pady=5, padx=5)
        self._create_entry(
            proc_frame, "Сервер эмбеддингов:", self.config_vars["embed_server_url"]
        )
        self._create_entry(
            proc_frame, "Модель эмбеддингов:", self.config_vars["embed_model"]
        )
        self._create_entry(
            proc_frame, "Кандидатов для LLM (Top-K):", self.config_vars["top_k_filter"]
        )
        self._create_entry(
            proc_frame,
            "Размер порции для LLM:",
            self.config_vars["classification_batch_size"],
        )

        # --- Настройки AI ---
        self.ai_settings = AISettingsFrame(self.left_panel, self.config_vars)
        self.ai_settings.pack(fill=tk.X, pady=5, padx=5)

        # --- Расширенные настройки ---
        self.adv_settings = AdvancedSettingsFrame(self.left_panel, self.config_vars)
        self.adv_settings.pack(fill=tk.X, pady=5, padx=5)

        # --- Прогресс ---
        self.progress_panel = ProgressPanel(
            self.progress_frame,
            ["Векторизация Сфер", "Векторизация Функций", "Классификация"],
        )
        self.progress_panel.pack(fill=tk.X)

        # --- Промпт ---
        prompts_notebook = ttk.Notebook(self.right_panel)
        prompts_notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self._create_prompt_tab(
            prompts_notebook, "Системный промпт", self.config_vars["system_prompt"]
        )

    def _worker_main(self, config: dict):
        self.log("Начало классификации по сферам...")
        # TODO: Перенести сюда асинхронную логику из старого скрипта 3_spheres.py
        # 1. Загрузить файлы сфер и функций через data_utils.load_dataframe
        # 2. Разделить на обработанные и необработанные
        # 3. Получить эмбеддинги для сфер и функций, обновляя self.progress_panel
        # 4. Запустить асинхронные воркеры для классификации
        #    - Внутри использовать safe_api_call_async
        #    - Обновлять self.progress_panel.step('Классификация')
        # 5. Собрать результаты, добавить иерархию и сохранить итоговый файл

        # Имитация работы
        self.log(f"Конфигурация: {config}")
        stages = ["Векторизация Сфер", "Векторизация Функций", "Классификация"]
        for stage in stages:
            if self._stop_flag.is_set():
                break
            self.set_status(f"Этап: {stage}")
            self.progress_panel.reset(stage, 15)
            for i in range(15):
                if self._stop_flag.is_set():
                    break
                time.sleep(0.2)
                self.progress_panel.step(stage)
            self.log(f"Этап {stage} завершен.")

        if self._stop_flag.is_set():
            self.log("Классификация прервана.")
        else:
            self.log("Классификация по сферам успешно завершена.")

    # Вспомогательные функции для UI
    def _create_entry(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=22).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )

    def _create_prompt_tab(self, notebook, title, var):
        frame = ttk.Frame(notebook, padding=5)
        notebook.add(frame, text=title)
        text_widget = tk.Text(frame, wrap=tk.WORD, height=5)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert("1.0", var.get())
        text_widget.bind(
            "<KeyRelease>", lambda event, v=var: v.set(event.widget.get("1.0", tk.END))
        )


if __name__ == "__main__":
    root = tk.Tk()
    app = SphereClassifierApp(root)
    root.mainloop()
