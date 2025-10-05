# Workflow/5_dublicates_check.py (Refactored)
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


class DuplicateCheckApp(BaseAppTemplate):
    def __init__(self, root: tk.Tk):
        super().__init__(root, "Проверка дубликатов v9.0 (Unified)")
        self.final_df = None  # Для хранения итогового DataFrame

    def _create_config_vars(self) -> dict:
        defaults = super()._create_config_vars()
        defaults.update(
            {
                "system_prompt": tk.StringVar(value="PROMPT..."),  # Заглушка
            }
        )
        return defaults

    def _build_custom_ui(self):
        # --- Файл ---
        files_frame = ttk.LabelFrame(self.left_panel, text="Входные данные")
        files_frame.pack(fill=tk.X, pady=5, padx=5)
        FileSelectionWidget(
            files_frame, "Исходный файл:", self.config_vars["input_file"]
        ).pack(fill=tk.X, pady=2)

        # --- Настройки AI ---
        self.ai_settings = AISettingsFrame(self.left_panel, self.config_vars)
        self.ai_settings.pack(fill=tk.X, pady=5, padx=5)

        # --- Расширенные настройки ---
        self.adv_settings = AdvancedSettingsFrame(self.left_panel, self.config_vars)
        self.adv_settings.pack(fill=tk.X, pady=5, padx=5)

        # --- Прогресс ---
        self.progress_panel = ProgressPanel(self.progress_frame, ["AI-верификация"])
        self.progress_panel.pack(fill=tk.X)

        # --- Промпт ---
        prompts_notebook = ttk.Notebook(self.right_panel)
        prompts_notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self._create_prompt_tab(
            prompts_notebook, "Системный промпт", self.config_vars["system_prompt"]
        )

        # --- Кастомная кнопка сохранения ---
        self.save_btn = ttk.Button(
            self.main_frame,
            text="Сохранить отчет",
            command=self._save_report,
            state=tk.DISABLED,
        )
        self.save_btn.pack(
            in_=self.left_panel, side=tk.BOTTOM, fill=tk.X, pady=10, padx=5, ipady=5
        )

    def _on_worker_finished(self):
        """Переопределяем, чтобы активировать кнопку сохранения."""
        super()._on_worker_finished()
        if self.final_df is not None:
            self.save_btn.config(state=tk.NORMAL)

    def _on_start(self):
        """Переопределяем, чтобы сбросить состояние кнопки сохранения."""
        self.save_btn.config(state=tk.DISABLED)
        self.final_df = None
        super()._on_start()

    def _save_report(self):
        if self.final_df is None:
            messagebox.showwarning("Нет данных", "Нет данных для сохранения.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            title="Сохранить отчет",
        )
        if not path:
            return
        try:
            self.set_status(f"Сохранение отчета в {path}...")
            self.final_df.to_excel(path, index=False)
            self.log(f"Отчет успешно сохранен: {path}")
            messagebox.showinfo("Успех", f"Отчет сохранен в: {path}")
        except Exception as e:
            self.log(f"Ошибка сохранения: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить файл: {e}")

    def _worker_main(self, config: dict):
        self.log("Начало проверки дубликатов...")
        # TODO: Перенести сюда логику из старого скрипта 5_dublicates_check.py
        # 1. Загрузить DataFrame через data_utils.load_dataframe
        # 2. Валидировать его через data_utils.validate_dataframe
        # 3. Собрать все уникальные пары для проверки из колонки 'CollisionWith'
        # 4. Создать AI клиентов через ai_utils.create_ai_clients
        # 5. Запустить воркеры для верификации пар
        #    - Внутри вызывать safe_api_call
        #    - Обновлять self.progress_panel.step('AI-верификация')
        # 6. Собрать подтвержденные пары
        # 7. Обновить исходный DataFrame результатами
        # 8. Сохранить результат в self.final_df

        # Имитация работы
        from shared_utils.data_utils import load_dataframe

        self.log(f"Конфигурация: {config}")
        # df = load_dataframe(config['input_file'])
        # self.log(f"Загружено {len(df)} строк.")
        num_tasks = 50
        self.progress_panel.reset("AI-верификация", num_tasks)
        for i in range(num_tasks):
            if self._stop_flag.is_set():
                break
            time.sleep(0.1)
            self.progress_panel.step("AI-верификация")

        # Имитация сохранения результата для кнопки
        import pandas as pd

        self.final_df = pd.DataFrame([{"a": 1, "b": 2}])

        if self._stop_flag.is_set():
            self.log("Проверка прервана.")
        else:
            self.log("Проверка дубликатов успешно завершена.")

    # Вспомогательные функции для UI
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
    app = DuplicateCheckApp(root)
    root.mainloop()
