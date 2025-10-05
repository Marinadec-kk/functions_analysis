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
from backend.hierarchy_module import perform_hierarchical_analysis  # New import
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
        self.auto_advance_stages = config.get(
            "auto_advance_stages", True
        )  # New: Auto-advance setting
        self.continue_event = asyncio.Event()  # New: Event for manual stage advancement

        # Устанавливаем callback для utils, чтобы логи отправлялись в UI
        set_ui_queue_callback(ui_queue.put)
        self.parsing_module = ParsingModule(ui_queue)  # Инициализируем ParsingModule
        log_message("FullAnalysisPipeline инициализирован.", level="info")

    def continue_pipeline(self):
        """
        Signals the pipeline to continue to the next stage when in manual advance mode.
        """
        log_message("Получен сигнал на продолжение конвейера.", level="info")
        self.continue_event.set()

    def start_analysis(
        self,
        ui_config: Dict[str, Any],
        input_file: Optional[str] = None,
        start_stage: Optional[str] = None,
    ):
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

        self.worker_thread = threading.Thread(
            target=self._worker_main_run_async,
            args=(input_file, start_stage),
        )
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

    def _worker_main_run_async(
        self, input_file: Optional[str], start_stage: Optional[str]
    ):
        """
        Точка входа для рабочего потока, которая запускает асинхронный цикл.
        """
        log_message(
            "Рабочий поток запущен. Инициализация asyncio loop...", level="info"
        )
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.run_full_pipeline(input_file, start_stage))
        except Exception as e:
            log_message(f"Критическая ошибка в рабочем потоке: {e}", level="error")
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
        finally:
            log_message("Asyncio loop завершен в рабочем потоке.", level="info")
            if not loop.is_closed():
                loop.close()

    async def _run_stage(
        self,
        stage_logic: Callable,
        stage_name: str,
        stage_num: int,
        total_stages: int,
        required_columns: Optional[List[str]] = None,  # New parameter
    ) -> bool:
        """
        Выполняет один этап конвейера, инкапсулируя общую логику.
        """
        if self.stop_event.is_set():
            self._handle_stop(stage_name)
            return False

        update_status(f"Этап {stage_num}/{total_stages}: {stage_name}...")
        log_message(
            f"Начало этапа {stage_num}/{total_stages}: {stage_name}...", level="info"
        )

        # New: Validate required columns
        if required_columns and not self.data.empty:
            missing_columns = [
                col for col in required_columns if col not in self.data.columns
            ]
            if missing_columns:
                error_msg = f"Отсутствуют необходимые столбцы для этапа '{stage_name}': {', '.join(missing_columns)}. Пропуск этапа."
                log_message(error_msg, level="error")
                update_status(error_msg)
                return False  # Skip this stage due to missing columns

        try:
            await stage_logic()

            # New: Manual advance logic
            if (
                not self.auto_advance_stages and stage_num < total_stages
            ):  # Don't pause after the last stage
                update_status(
                    f"Этап {stage_name} завершен. Ожидание ручного перехода..."
                )
                log_message(
                    f"Этап {stage_name} завершен. Ожидание ручного перехода...",
                    level="info",
                )
                self.ui_queue.put(
                    {"type": "waiting_for_manual_advance", "stage_name": stage_name}
                )
                self.continue_event.clear()  # Clear the event for the next wait
                await self.continue_event.wait()  # Wait for UI to signal continuation
                log_message(
                    f"Получен сигнал на продолжение после этапа {stage_name}.",
                    level="info",
                )

            return True  # Success
        except Exception as e:
            log_message(
                f"Ошибка на этапе {stage_num} ({stage_name}): {e}", level="error"
            )
            update_status(f"Конвейер завершен с ошибками на этапе {stage_name}: {e}")
            self.ui_queue.put(
                {"type": "pipeline_finished", "success": False, "error": str(e)}
            )
            return False  # Failure

    async def run_full_pipeline(
        self, input_file: Optional[str] = None, start_stage: Optional[str] = None
    ) -> bool:
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

        total_stages = 7  # Updated total stages

        # --- Определение логики для каждого этапа ---
        async def stage1_parsing():
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
                # Генерируем исключение, которое будет поймано в _run_stage
                raise ValueError(
                    "Парсинг не дал результатов (Parsing yielded no results)"
                )
            log_message(
                f"Этап 1/{total_stages}: Парсинг завершен. Найдено {len(self.data)} функций.",
                level="info",
            )

        async def stage2_classify_types():
            self.data = await classify_function_types(
                functions_df=self.data,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,
            )
            log_message(
                f"Этап 2/{total_stages}: Классификация типов функций завершена.",
                level="info",
            )

        async def stage3_classify_spheres():
            spheres_definitions_file = self.config.get("spheres_definitions_file")
            if not spheres_definitions_file or not os.path.exists(
                spheres_definitions_file
            ):
                log_message(
                    f"Файл определений сфер не найден: {spheres_definitions_file}. Пропускаем этап.",
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
                    stop_event=self.stop_event,
                )
            log_message(
                f"Этап 3/{total_stages}: Классификация сфер функций завершена.",
                level="info",
            )

        async def stage4_grouping():
            self.data = await group_and_find_candidates(
                functions_df=self.data,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,
            )
            log_message(
                f"Этап 4/{total_stages}: Группировка и поиск кандидатов на дубликаты завершены.",
                level="info",
            )

        async def stage5_verify_duplicates():
            self.data = await verify_duplicates(
                functions_df=self.data,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,
            )
            log_message(
                f"Этап 5/{total_stages}: Гибридная верификация дубликатов завершена.",
                level="info",
            )

        async def stage6_hierarchical_analysis():  # New stage
            self.data = await perform_hierarchical_analysis(
                functions_df=self.data,
                config=self.config,
                progress_callback=self._update_progress_wrapper,
                status_callback=update_status,
                stop_event=self.stop_event,
            )
            log_message(
                f"Этап 6/{total_stages}: Иерархический анализ завершен.",
                level="info",
            )

        async def stage7_generate_reports():  # Renamed stage
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
                stop_event=self.stop_event,
            )
            log_message(
                f"Этап 7/{total_stages}: Генерация отчетов Markdown завершена.",
                level="info",
            )

        stages = [
            (
                "parsing",
                stage1_parsing,
                "Парсинг документов",
                None,
            ),
            (
                "classify_types",
                stage2_classify_types,
                "Классификация типов функций",
                ["Полный текст функции"],
            ),
            (
                "classify_spheres",
                stage3_classify_spheres,
                "Классификация сфер функций",
                ["Полный текст функции"],
            ),
            (
                "grouping",
                stage4_grouping,
                "Группировка и поиск кандидатов на дубликаты",
                ["Полный текст функции"],
            ),
            (
                "verify_duplicates",
                stage5_verify_duplicates,
                "Гибридная верификация дубликатов",
                ["Полный текст функции", "group_id", "candidate_duplicates"],
            ),
            (
                "hierarchical_analysis",
                stage6_hierarchical_analysis,
                "Иерархический анализ",
                [
                    "ID функции",
                    "Центральный ГО",
                    "Подведомственный ГО",
                    "Полный текст функции",
                ],
            ),
            (
                "generate_reports",
                stage7_generate_reports,
                "Генерация отчетов Markdown",
                ["ID функции", "Полный текст функции", "type", "sphere", "group_id"],
            ),
        ]

        start_index = 0
        if input_file and start_stage:
            if not await self._validate_and_load_input_file(
                input_file, start_stage, stages
            ):
                return False  # Validation failed

            # Find the index of the starting stage
            stage_ids = [s[0] for s in stages]
            try:
                start_index = stage_ids.index(start_stage)
            except ValueError:
                log_message(
                    f"Указанный начальный этап '{start_stage}' не найден. Запуск с начала.",
                    level="warning",
                )
                # Optionally, you could fail here instead
                # update_status(f"Ошибка: начальный этап '{start_stage}' не найден.")
                # return False

        # Execute stages from the determined start_index
        for i, (stage_id, logic, name, required_cols) in enumerate(
            stages[start_index:], start=start_index + 1
        ):
            # Skip parsing if we loaded a file
            if stage_id == "parsing" and input_file:
                log_message(
                    "Пропускаем этап парсинга, так как был предоставлен входной файл.",
                    level="info",
                )
                continue

            if not await self._run_stage(logic, name, i, total_stages, required_cols):
                return False  # Stop if a stage fails

        update_status("Полный конвейер анализа завершен успешно!")
        log_message("Полный конвейер анализа завершен успешно!", level="info")
        self.ui_queue.put({"type": "pipeline_finished", "success": True})
        return True

    async def _validate_and_load_input_file(
        self, file_path: str, start_stage: str, stages: List[tuple]
    ) -> bool:
        """
        Validates the provided Excel file and loads it into self.data.
        """
        update_status(f"Валидация входного файла: {os.path.basename(file_path)}...")
        log_message(f"Начало валидации файла: {file_path}", level="info")

        # 1. Check existence
        if not os.path.exists(file_path):
            log_message(f"Файл не найден: {file_path}", level="error")
            update_status(f"Ошибка: Файл не найден: {os.path.basename(file_path)}")
            return False

        # 2. Check readability (is it a valid Excel file?)
        try:
            temp_df = pd.read_excel(file_path)
        except Exception as e:
            log_message(f"Не удалось прочитать Excel файл: {e}", level="error")
            update_status(
                f"Ошибка: Не удалось прочитать файл: {os.path.basename(file_path)}"
            )
            return False

        # 3. Check for emptiness
        if temp_df.empty:
            log_message("Входной файл пуст.", level="error")
            update_status("Ошибка: Входной файл не содержит данных.")
            return False

        # 4. Check for required columns based on the start_stage
        required_columns = []
        for stage_id, _, _, cols in stages:
            if stage_id == start_stage and cols:
                required_columns = cols
                break

        if required_columns:
            missing_columns = [
                col for col in required_columns if col not in temp_df.columns
            ]
            if missing_columns:
                error_msg = f"Отсутствуют необходимые столбцы для этапа '{start_stage}': {', '.join(missing_columns)}"
                log_message(error_msg, level="error")
                update_status(f"Ошибка: {error_msg}")
                return False

        log_message("Валидация файла прошла успешно.", level="info")
        self.data = temp_df  # Load data into the pipeline
        update_status("Входной файл успешно загружен.")
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
