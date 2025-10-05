# Workflow/6_md_sharding.py (Refactored)
import tkinter as tk
from tkinter import ttk
import os
import sys

# Добавляем путь к общим утилитам
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

from shared_utils.base_app import BaseAppTemplate
from shared_utils.gui_components import FileSelectionWidget


class MDShardingApp(BaseAppTemplate):
    def __init__(self, root: tk.Tk):
        # Этот скрипт проще, поэтому окно меньше и нет сложных настроек
        super().__init__(root, "MD Sharding v4.0 (Unified)", "900x600")

    def _build_custom_ui(self):
        """Создает UI только для выбора файлов, т.к. AI не используется."""
        settings_frame = ttk.LabelFrame(self.left_panel, text="Настройки")
        settings_frame.pack(fill=tk.X, pady=5, padx=5)

        FileSelectionWidget(
            settings_frame, "Исходный Excel файл:", self.config_vars["input_file"]
        ).pack(fill=tk.X, pady=5)
        FileSelectionWidget(
            settings_frame,
            "Папка для сохранения:",
            self.config_vars["output_dir"],
            is_dir=True,
        ).pack(fill=tk.X, pady=5)

        # Удаляем ненужные элементы из базового шаблона
        self.progress_frame.pack_forget()  # Прогресс-бар не нужен

    def _get_config(self) -> dict:
        config = super()._get_config()
        if not config.get("input_file") or not os.path.isfile(config.get("input_file")):
            raise ValueError("Укажите корректный исходный Excel файл.")
        if not config.get("output_dir") or not os.path.isdir(config.get("output_dir")):
            raise ValueError("Укажите корректную папку для сохранения.")
        return config

    def _worker_main(self, config: dict):
        self.log("Начало процесса MD Sharding...")
        # TODO: Перенести сюда всю логику из старого скрипта 6_md_sharding.py
        # 1. Загрузить DataFrame через data_utils.load_dataframe
        # 2. Проанализировать кластеры коллизий
        # 3. Создать карту имен для групп коллизий
        # 4. В цикле по DataFrame:
        #    - Если уровень функции 1-3, создать для нее MD-файл
        #    - Собрать данные для портрета исполнителя
        #    - Периодически проверять self._stop_flag.is_set()
        #    - Обновлять статус через self.set_status(...)
        # 5. Создать файлы групп коллизий
        # 6. Создать портреты исполнителей

        # Имитация работы
        self.log(f"Конфигурация: {config}")
        total_steps = 3

        self.set_status("Шаг 1: Анализ кластеров...")
        time.sleep(1)
        if self._stop_flag.is_set():
            return

        self.set_status("Шаг 2: Создание файлов функций...")
        time.sleep(1.5)
        if self._stop_flag.is_set():
            return

        self.set_status("Шаг 3: Создание портретов...")
        time.sleep(1)
        if self._stop_flag.is_set():
            return

        if self._stop_flag.is_set():
            self.log("Процесс прерван.")
        else:
            self.log("MD Sharding успешно завершен.")


if __name__ == "__main__":
    root = tk.Tk()
    app = MDShardingApp(root)
    root.mainloop()
