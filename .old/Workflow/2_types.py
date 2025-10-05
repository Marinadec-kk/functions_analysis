# Workflow/2_types.py (Refactored)
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


class TypologyApp(BaseAppTemplate):
    def __init__(self, root: tk.Tk):
        super().__init__(root, "Анализатор типов функций v5.0 (Unified)")

    def _create_config_vars(self) -> dict:
        """Переопределяем, чтобы добавить специфичные переменные."""
        defaults = super()._create_config_vars()
        defaults.update(
            {
                "gold_standard_file": tk.StringVar(),
                "use_embeddings": tk.BooleanVar(value=True),
                "embed_server_url": tk.StringVar(value="http://localhost:1234"),
                "embed_model": tk.StringVar(value="Qwen/Qwen3-Embedding-8B-GGUF"),
                "prompt1_initial": tk.StringVar(value="PROMPT 1..."),  # Заглушка
                "prompt1_refinement": tk.StringVar(
                    value="PROMPT 1 REFINE..."
                ),  # Заглушка
                "prompt2": tk.StringVar(value="PROMPT 2..."),  # Заглушка
                "prompt3": tk.StringVar(value="PROMPT 3..."),  # Заглушка
            }
        )
        return defaults

    def _build_custom_ui(self):
        """Создает кастомный UI для анализатора типов."""
        # --- Файлы ---
        files_frame = ttk.LabelFrame(self.left_panel, text="Файлы")
        files_frame.pack(fill=tk.X, pady=5, padx=5)
        FileSelectionWidget(
            files_frame, "Золотой стандарт:", self.config_vars["gold_standard_file"]
        ).pack(fill=tk.X, pady=2)
        FileSelectionWidget(
            files_frame, "Файл для обработки:", self.config_vars["input_file"]
        ).pack(fill=tk.X, pady=2)
        FileSelectionWidget(
            files_frame, "Сохранить результат:", self.config_vars["output_dir"]
        ).pack(fill=tk.X, pady=2)

        # --- Эмбеддинги ---
        embed_frame = ttk.LabelFrame(self.left_panel, text="Помощь эмбеддингов")
        embed_frame.pack(fill=tk.X, pady=5, padx=5)
        ttk.Checkbutton(
            embed_frame,
            text="Включить подсказки",
            variable=self.config_vars["use_embeddings"],
        ).pack(anchor="w", padx=5)
        self._create_entry(
            embed_frame, "Сервер эмбеддингов:", self.config_vars["embed_server_url"]
        )
        self._create_entry(
            embed_frame, "Модель эмбеддингов:", self.config_vars["embed_model"]
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
            ["Эмбеддинги", "ИИ1 Классификация", "ИИ2 Верификация", "ИИ3 Арбитраж"],
        )
        self.progress_panel.pack(fill=tk.X)

        # --- Промпты ---
        prompts_notebook = ttk.Notebook(self.right_panel)
        prompts_notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self._create_prompt_tab(
            prompts_notebook, "Промпт ИИ1", self.config_vars["prompt1_initial"]
        )
        self._create_prompt_tab(
            prompts_notebook,
            "Промпт ИИ1 (Уточ.)",
            self.config_vars["prompt1_refinement"],
        )
        self._create_prompt_tab(
            prompts_notebook, "Промпт ИИ2", self.config_vars["prompt2"]
        )
        self._create_prompt_tab(
            prompts_notebook, "Промпт ИИ3", self.config_vars["prompt3"]
        )

    def _worker_main(self, config: dict):
        self.log("Начало анализа типов...")
        # TODO: Перенести сюда асинхронную логику из старого скрипта 2_types.py
        # 1. Загрузить все файлы через data_utils.load_dataframe
        # 2. Валидировать их
        # 3. Если use_embeddings == True:
        #    - Запустить получение эмбеддингов для золотого стандарта и входного файла
        #    - Обновлять self.progress_panel.step('Эмбеддинги')
        #    - Сформировать карту подсказок
        # 4. Запустить основной асинхронный цикл обработки:
        #    - process_unresolved_with_ai_loop
        #    - Внутри него вызывать safe_api_call_async из ai_utils
        #    - Обновлять соответствующие прогресс-бары: ИИ1, ИИ2, ИИ3
        # 5. Собрать результаты и сохранить итоговый файл

        # Имитация работы
        self.log(f"Конфигурация: {config}")
        stages = ["Эмбеддинги", "ИИ1 Классификация", "ИИ2 Верификация", "ИИ3 Арбитраж"]
        for stage in stages:
            if self._stop_flag.is_set():
                break
            self.set_status(f"Этап: {stage}")
            self.progress_panel.reset(stage, 20)
            for i in range(20):
                if self._stop_flag.is_set():
                    break
                time.sleep(0.1)
                self.progress_panel.step(stage)
            self.log(f"Этап {stage} завершен.")

        if self._stop_flag.is_set():
            self.log("Анализ прерван.")
        else:
            self.log("Анализ типов успешно завершен.")

    # Вспомогательные функции для UI
    def _create_entry(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
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
    app = TypologyApp(root)
    root.mainloop()
