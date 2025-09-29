import os
import pandas as pd
from typing import Dict, Any, List, Optional, Callable
import asyncio
import threading
import queue

from backend.parsing_module import ParsingModule
from backend.typology_module import classify_function_types
from backend.spheres_module import classify_function_spheres
from backend.grouping_module import group_and_find_candidates
from backend.duplicates_module import verify_duplicates
from backend.markdown_module import generate_markdown_reports
from backend.utils import (
    log_message,
    update_status,
    update_progress,
    set_ui_queue_callback,
)


import pandas as pd


class FullAnalysisPipeline:
    """
    Оркестрирует полный конвейер анализа функций, от парсинга до генерации отчетов.
    Управляет последовательностью выполнения модулей и взаимодействием с UI через очередь.
    """

    def __init__(self, ui_queue: queue.Queue, config: Dict[str, Any]):
        self.ui_queue = ui_queue
        self.config = config
        self.data: pd.DataFrame = pd.DataFrame()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

        # Устанавливаем callback для utils, чтобы логи отправлялись в UI
        set_ui_queue_callback(ui_queue.put)
        self.parsing_module = ParsingModule(ui_queue)  # Инициализируем ParsingModule
        log_message("FullAnalysisPipeline инициализирован.", level="info")

    def start_analysis(self, ui_config: Dict[str, Any]):
        """
        Запускает конвейер анализа в отдельном потоке.
        Принимает конфигурацию, собранную из UI.
        """
        if self.worker_thread and self.worker_thread.is_alive():
            log_message("Конвейер уже запущен.", level="warning")
            return

        self.stop_event.clear()  # Сбрасываем флаг остановки для нового запуска
        log_message("Запуск конвейера анализа в новом потоке...", level="info")

        # Передаем ui_config в основной config
        self.config.update(ui_config)

        self.worker_thread = threading.Thread(target=self._worker_main_run_async)
        self.worker_thread.daemon = (
            True  # Поток завершится при выходе основного приложения
        )
        self.worker_thread.start()

    def stop_analysis(self):
        """
        Посылает сигнал остановки рабочему потоку.
        """
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            log_message("Отправлен сигнал остановки конвейера.", level="info")
            update_status("Попытка остановить конвейер...")
        else:
            log_message("Конвейер не запущен.", level="warning")

    def _worker_main_run_async(self):
        """
        Точка входа для рабочего потока, которая запускает асинхронный цикл.
        """
        log_message(
            "Рабочий поток запущен. Инициализация asyncio loop...", level="info"
        )
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.run_full_pipeline())
        except Exception as e:
            log_message(f"Критическая ошибка в рабочем потоке: {e}", level="error")
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
        finally:
            log_message("Asyncio loop завершен в рабочем потоке.", level="info")
            if not loop.is_closed():
                loop.close()

    async def run_full_pipeline(self) -> bool:
        """
        Основная асинхронная логика полного конвейера анализа.
        """
        update_status("Начало полного конвейера анализа...")
        log_message("Начало полного конвейера анализа...", level="info")

        # Этап 0: Предварительная проверка конфигурации
        try:
            if not self._validate_config():
                log_message(
                    "Неверная конфигурация. Прерывание конвейера.", level="error"
                )
                update_status("Конвейер завершен с ошибками (конфигурация).")
                self.ui_queue.put(
                    {
                        "type": "pipeline_finished",
                        "success": False,
                        "error": "Invalid configuration",
                    }
                )
                return False
        except Exception as e:
            log_message(
                f"Ошибка при проверке конфигурации: {e}. Прерывание конвейера.",
                level="error",
            )
            update_status(f"Ошибка конфигурации: {e}")
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False

        # --- Этап 1: Парсинг документов ---
        try:
            if self.stop_event.is_set():
                return self._handle_stop("Парсинг")
            update_status("Этап 1/6: Парсинг документов...")
            log_message("Начало этапа 1/6: Парсинг документов...", level="info")

            parsing_output_file = self.config.get(
                "parsing_output_file",
                os.path.join(
                    self.config["DEFAULT_OUTPUT_DIR"], "parsed_functions.xlsx"
                ),
            )
            self.data = await self.parsing_module.run_parsing_pipeline(
                input_folder=self.config["parsing_input_folder"],
                output_file=parsing_output_file,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
            )
            if self.data.empty:
                log_message(
                    "Парсинг не дал результатов. Прерывание конвейера.", level="error"
                )
                update_status("Конвейер завершен с ошибками (парсинг).")
                self.ui_queue.put(
                    {
                        "type": "pipeline_finished",
                        "success": False,
                        "error": "Parsing yielded no results",
                    }
                )
                return False
            log_message(
                f"Этап 1/6: Парсинг завершен. Найдено {len(self.data)} функций.",
                level="info",
            )
        except Exception as e:
            log_message(f"Ошибка на этапе 1 (Парсинг документов): {e}", level="error")
            update_status(f"Конвейер завершен с ошибками на этапе Парсинга: {e}")
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False

        # --- Этап 2: Классификация типов функций ---
        try:
            if self.stop_event.is_set():
                return self._handle_stop("Классификация типов")
            update_status("Этап 2/6: Классификация типов функций...")
            log_message(
                "Начало этапа 2/6: Классификация типов функций...", level="info"
            )
            self.data = await classify_function_types(
                functions_df=self.data,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,  # Передаем stop_event
            )
            log_message(
                "Этап 2/6: Классификация типов функций завершена.", level="info"
            )
        except Exception as e:
            log_message(
                f"Ошибка на этапе 2 (Классификация типов функций): {e}", level="error"
            )
            update_status(
                f"Конвейер завершен с ошибками на этапе Классификации типов: {e}"
            )
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False

        # --- Этап 3: Классификация сфер функций ---
        try:
            if self.stop_event.is_set():
                return self._handle_stop("Классификация сфер")
            update_status("Этап 3/6: Классификация сфер функций...")
            log_message("Начало этапа 3/6: Классификация сфер функций...", level="info")

            spheres_definitions_file = self.config.get("spheres_definitions_file")
            if not spheres_definitions_file or not os.path.exists(
                spheres_definitions_file
            ):
                log_message(
                    f"Файл определений сфер не найден: {spheres_definitions_file}. Пропускаем этап классификации сфер.",
                    level="warning",
                )
                update_status("Классификация сфер пропущена (файл не найден).")
                self.data["sphere"] = "N/A"
                self.data["sphere_parent_id"] = "N/A"
                self.data["sphere_parent"] = "N/A"
            else:
                spheres_df = pd.read_excel(spheres_definitions_file)
                self.data = await classify_function_spheres(
                    functions_df=self.data,
                    spheres_df=spheres_df,
                    config=self.config,
                    progress_callback=self._update_progress_wrapper,
                    status_callback=update_status,
                    stop_event=self.stop_event,  # Передаем stop_event
                )
            log_message("Этап 3/6: Классификация сфер функций завершена.", level="info")
        except Exception as e:
            log_message(
                f"Ошибка на этапе 3 (Классификация сфер функций): {e}", level="error"
            )
            update_status(
                f"Конвейер завершен с ошибками на этапе Классификации сфер: {e}"
            )
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False

        # --- Этап 4: Группировка и поиск кандидатов на дубликаты ---\
        try:
            if self.stop_event.is_set():
                return self._handle_stop("Группировка и поиск дубликатов")
            update_status("Этап 4/6: Группировка и поиск кандидатов на дубликаты...")
            log_message(
                "Начало этапа 4/6: Группировка и поиск кандидатов на дубликаты...",
                level="info",
            )
            self.data = await group_and_find_candidates(
                functions_df=self.data,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,  # Передаем stop_event
            )
            log_message(
                "Этап 4/6: Группировка и поиск кандидатов на дубликаты завершены.",
                level="info",
            )
        except Exception as e:
            log_message(
                f"Ошибка на этапе 4 (Группировка и поиск дубликатов): {e}",
                level="error",
            )
            update_status(f"Конвейер завершен с ошибками на этапе Группировки: {e}")
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False

        # --- Этап 5: Гибридная верификация дубликатов ---
        try:
            if self.stop_event.is_set():
                return self._handle_stop("Верификация дубликатов")
            update_status("Этап 5/6: Гибридная верификация дубликатов...")
            log_message(
                "Начало этапа 5/6: Гибридная верификация дубликатов...", level="info"
            )
            self.data = await verify_duplicates(
                functions_df=self.data,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,  # Передаем stop_event
            )
            log_message(
                "Этап 5/6: Гибридная верификация дубликатов завершена.", level="info"
            )
        except Exception as e:
            log_message(
                f"Ошибка на этапе 5 (Верификация дубликатов): {e}", level="error"
            )
            update_status(
                f"Конвейер завершен с ошибками на этапе Верификации дубликатов: {e}"
            )
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False

        # --- Этап 6: Генерация отчетов Markdown ---
        try:
            if self.stop_event.is_set():
                return self._handle_stop("Генерация отчетов Markdown")
            update_status("Этап 6/6: Генерация отчетов Markdown...")
            log_message("Начало этапа 6/6: Генерация отчетов Markdown...", level="info")

            markdown_output_dir = self.config.get(
                "markdown_output_dir",
                os.path.join(self.config["DEFAULT_OUTPUT_DIR"], "markdown_reports"),
            )
            os.makedirs(markdown_output_dir, exist_ok=True)
            await generate_markdown_reports(
                functions_df=self.data,
                output_dir=markdown_output_dir,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,  # Передаем stop_event
            )
            log_message("Этап 6/6: Генерация отчетов Markdown завершена.", level="info")
        except Exception as e:
            log_message(
                f"Ошибка на этапе 6 (Генерация отчетов Markdown): {e}", level="error"
            )
            update_status(
                f"Конвейер завершен с ошибками на этапе Генерации отчетов: {e}"
            )
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False

        update_status("Полный конвейер анализа завершен успешно!")
        log_message("Полный конвейер анализа завершен успешно!", level="info")
        self.ui_queue.put({"type": "pipeline_finished", "success": True})
        return True

    def _handle_stop(self, stage_name: str) -> bool:
        """
        Обрабатывает запрос на остановку конвейера.
        """
        log_message(
            f"Конвейер остановлен пользователем на этапе: {stage_name}", level="info"
        )
        update_status(f"Конвейер остановлен на этапе: {stage_name}")
        self.ui_queue.put(
            {"type": "pipeline_finished", "success": None, "stopped": True}
        )  # None для успеха означает "не завершен"
        return False

    def _update_progress_wrapper(self, current: int, total: int, stage_detail: str):
        """
        Обертка для обновления прогресса, которая использует update_progress из utils.
        """
        update_progress(current, total, stage_detail)

    def _validate_config(self) -> bool:
        """
        Проверяет наличие необходимых полей в конфигурации.
        """
        required_paths = {
            "DEFAULT_OUTPUT_DIR": "Не указана выходная директория по умолчанию.",
            "parsing_input_folder": "Не указана входная папка для парсинга.",
        }

        for key, error_msg in required_paths.items():
            if not self.config.get(key):
                log_message(error_msg, level="error")
                return False
            if "folder" in key or "dir" in key:
                if not os.path.exists(self.config[key]):
                    log_message(
                        f"Указанный путь не существует: {self.config[key]}",
                        level="error",
                    )
                    return False

        # Проверка URL и ключей API, если AI включен
        if self.config.get("enable_ai_features", True):
            if not self.config.get(
                "AI_API_BASE_URL"
            ):  # Использовать AI_API_BASE_URL из config.py
                log_message("Не указан базовый URL для AI API.", level="error")
                return False
            if (
                not self.config.get(
                    "AI_API_KEY"
                )  # Использовать AI_API_KEY из config.py
                or self.config.get("AI_API_KEY") == "your_ai_api_key_here"
            ):
                log_message(
                    "Не указан или недействителен API ключ для AI.", level="error"
                )
                return False

        return True
