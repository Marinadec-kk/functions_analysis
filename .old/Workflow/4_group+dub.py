# Workflow/4_group+dub.py (Refactored)
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


class CollisionAnalyzerApp(BaseAppTemplate):
    def __init__(self, root: tk.Tk):
        super().__init__(root, "Анализатор коллизий v15.0 (Unified)")

    def _create_config_vars(self) -> dict:
        defaults = super()._create_config_vars()
        defaults.update(
            {
                "universal_json_file": tk.StringVar(),
                "grouping_cols": tk.StringVar(value="Type, Sphere_3"),
                "sim_threshold": tk.DoubleVar(value=0.50),
                "univ_threshold": tk.DoubleVar(value=0.75),
                "ai_should_verify": tk.BooleanVar(value=True),
                "system_prompt": tk.StringVar(value="PROMPT..."),  # Заглушка
            }
        )
        return defaults

    def _build_custom_ui(self):
        # --- Данные и группировка ---
        data_frame = ttk.LabelFrame(self.left_panel, text="Данные и группировка")
        data_frame.pack(fill=tk.X, pady=5, padx=5)
        FileSelectionWidget(
            data_frame, "Исходный файл:", self.config_vars["input_file"]
        ).pack(fill=tk.X, pady=2)
        FileSelectionWidget(
            data_frame, "Папка для отчета:", self.config_vars["output_dir"], is_dir=True
        ).pack(fill=tk.X, pady=2)
        FileSelectionWidget(
            data_frame,
            "JSON универс. функций:",
            self.config_vars["universal_json_file"],
        ).pack(fill=tk.X, pady=2)
        self._create_entry(
            data_frame, "Столбцы для группировки:", self.config_vars["grouping_cols"]
        )

        # --- Поиск (Эмбеддинги) ---
        embed_frame = ttk.LabelFrame(self.left_panel, text="Поиск (Эмбеддинги)")
        embed_frame.pack(fill=tk.X, pady=5, padx=5)
        self._create_entry(
            embed_frame, "Порог похожести:", self.config_vars["sim_threshold"]
        )
        self._create_entry(
            embed_frame, "Порог универсальных:", self.config_vars["univ_threshold"]
        )

        # --- Анализ (AI) ---
        ai_frame = ttk.LabelFrame(self.left_panel, text="Анализ (AI-верификация)")
        ai_frame.pack(fill=tk.X, pady=5, padx=5)
        ttk.Checkbutton(
            ai_frame,
            text="Включить AI-проверку",
            variable=self.config_vars["ai_should_verify"],
        ).pack(anchor="w", padx=5)
        self.ai_settings = AISettingsFrame(ai_frame, self.config_vars)
        self.ai_settings.pack(fill=tk.X, pady=5)

        # --- Расширенные настройки ---
        self.adv_settings = AdvancedSettingsFrame(self.left_panel, self.config_vars)
        self.adv_settings.pack(fill=tk.X, pady=5, padx=5)

        # --- Прогресс ---
        self.progress_panel = ProgressPanel(
            self.progress_frame, ["Поиск кандидатов", "AI-верификация"]
        )
        self.progress_panel.pack(fill=tk.X)
        self.candidates_found_var = tk.StringVar(value="Найдено кандидатов: 0")
        self.candidates_label = ttk.Label(
            self.progress_frame, textvariable=self.candidates_found_var
        )
        self.candidates_label.pack(anchor="w", padx=5)

        # --- Промпт ---
        prompts_notebook = ttk.Notebook(self.right_panel)
        prompts_notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self._create_prompt_tab(
            prompts_notebook, "Системный промпт", self.config_vars["system_prompt"]
        )

    def _worker_main(self, config: dict):
        self.log("Начало анализа коллизий...")
        # TODO: Перенести сюда логику из старого скрипта 4_group+dub.py
        # 1. Загрузить основной файл и JSON универсальных функций
        # 2. Сгруппировать данные по колонкам
        # 3. В цикле по группам:
        #    - Получить эмбеддинги для группы
        #    - Найти пары-кандидаты
        #    - Обновить self.progress_panel.step('Поиск кандидатов') и счетчик кандидатов
        # 4. Если config['ai_should_verify'] == True:
        #    - Создать AI клиентов
        #    - Запустить воркеры для верификации пар-кандидатов
        #    - Обновлять self.progress_panel.step('AI-верификация')
        # 5. Сформировать и сохранить итоговый отчет

        # Имитация работы
        self.log(f"Конфигурация: {config}")
        self.progress_panel.reset("Поиск кандидатов", 10)
        total_candidates = 0
        for i in range(10):
            if self._stop_flag.is_set():
                break
            time.sleep(0.2)
            self.progress_panel.step("Поиск кандидатов")
            new_candidates = i * 2
            total_candidates += new_candidates
            self.candidates_found_var.set(f"Найдено кандидатов: {total_candidates}")
        self.log("Поиск кандидатов завершен.")

        if config["ai_should_verify"] and not self._stop_flag.is_set():
            self.progress_panel.reset("AI-верификация", total_candidates)
            for i in range(total_candidates):
                if self._stop_flag.is_set():
                    break
                time.sleep(0.1)
                self.progress_panel.step("AI-верификация")
            self.log("AI-верификация завершена.")

        if self._stop_flag.is_set():
            self.log("Анализ прерван.")
        else:
            self.log("Анализ коллизий успешно завершен.")

    # Вспомогательные функции для UI
    def _create_entry(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=20).pack(side=tk.LEFT)
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
    app = CollisionAnalyzerApp(root)
    root.mainloop()
