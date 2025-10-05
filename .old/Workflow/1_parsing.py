# Workflow/1_parsing.py (Refactored)
import tkinter as tk
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
)
# ... (здесь должны быть импорты основной логики парсинга, если она будет вынесена)


class ParserApp(BaseAppTemplate):
    def __init__(self, root: tk.Tk):
        super().__init__(root, "Парсер функций ГО v7.0 (Unified)", "900x750")

    def _build_custom_ui(self):
        """Создает кастомный UI для этого конкретного приложения."""
        # --- Панель настроек ---
        settings_frame = ttk.LabelFrame(self.left_panel, text="Основные настройки")
        settings_frame.pack(fill=tk.X, pady=5, padx=5)

        # Используем унифицированный виджет для выбора папки
        self.input_folder_widget = FileSelectionWidget(
            settings_frame,
            "Папка с документами:",
            self.config_vars[
                "input_file"
            ],  # Используем 'input_file' для универсальности
            is_dir=True,
        )
        self.input_folder_widget.pack(fill=tk.X, pady=5, padx=5)

        # Используем унифицированный виджет для настроек AI
        # Добавляем специальную опцию "АП" в список режимов
        self.config_vars["ai_mode"].set(
            "Локальный"
        )  # Устанавливаем значение по умолчанию
        self.ai_settings = AISettingsFrame(self.left_panel, self.config_vars)
        ap_radio = ttk.Radiobutton(
            self.ai_settings.mode_frame,
            text="АП",
            variable=self.config_vars["ai_mode"],
            value="АП",
            command=self.ai_settings._on_mode_change,
        )
        ap_radio.pack(side=tk.LEFT, padx=10)
        self.ai_settings.pack(fill=tk.X, pady=5, padx=5)

        # Используем унифицированную панель расширенных настроек
        self.adv_settings = AdvancedSettingsFrame(self.left_panel, self.config_vars)
        self.adv_settings.pack(fill=tk.X, pady=5, padx=5)

        # В этом скрипте только один этап, поэтому можно создать ProgressPanel с одним элементом
        self.progress_panel = ProgressPanel(self.progress_frame, ["Обработка файлов"])
        self.progress_panel.pack(fill=tk.X)

    def _get_config(self) -> dict:
        """Переопределяем для добавления кастомной валидации."""
        config = super()._get_config()
        if not config.get("input_file") or not os.path.isdir(config.get("input_file")):
            raise ValueError("Укажите корректную папку с документами.")

        # Добавляем специфичные для "АП" настройки, если нужно
        if config["ai_mode"] == "АП":
            # Здесь можно добавить логику для режима "АП", например, жестко задать URL
            config["base_url"] = "https://llm.govplan.kz/v1"  # Пример

        return config

    def _worker_main(self, config: dict):
        """Основная логика парсера, выполняемая в фоновом потоке."""
        self.log("Начало процесса парсинга...")
        # TODO: Перенести сюда всю логику из старого скрипта 1_parsing.py
        # 1. Найти все .doc/.docx файлы в папке config['input_file']
        # 2. Инициализировать AI клиент(ы) через ai_utils.create_ai_clients(config)
        # 3. Создать очередь задач
        # 4. Запустить воркеров (потоки), которые будут брать файлы из очереди
        # 5. Каждый воркер:
        #    - Читает файл
        #    - Вызывает ai_utils.safe_api_call для извлечения ГО
        #    - Парсит ответ
        #    - Извлекает функции
        #    - Кладет результат в общую очередь результатов
        #    - Обновляет прогресс: self.progress_panel.step("Обработка файлов")
        # 6. Собрать все результаты
        # 7. Сохранить итоговый Excel файл

        # Имитация работы
        self.log(f"Конфигурация: {config}")
        self.progress_panel.reset("Обработка файлов", 10)
        for i in range(10):
            if self._stop_flag.is_set():
                break
            time.sleep(0.5)
            self.set_status(f"Обработка файла {i + 1}/10")
            self.log(f"Обработан файл {i + 1}.docx")
            self.progress_panel.step("Обработка файлов")

        if self._stop_flag.is_set():
            self.log("Парсинг прерван.")
        else:
            self.log("Парсинг успешно завершен.")


if __name__ == "__main__":
    root = tk.Tk()
    app = ParserApp(root)
    root.mainloop()
