import pandas as pd
import os
import re
from typing import Dict, Any, Optional, Callable

from .utils import (
    log_message,
    update_status,
    update_progress,
    sanitize_filename,
    create_clean_tag,
)


class MarkdownModule:
    """
    Модуль для генерации отчетов в формате Markdown.
    Использует данные о функциях для создания индивидуальных файлов,
    групп коллизий и сводных отчетов.
    """

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        log_message(
            f"MarkdownModule инициализирован. Выходная директория: {output_dir}",
            level="info",
        )

    def _generate_function_md_content(self, function_data: Dict[str, Any]) -> str:
        """
        Генерирует содержимое Markdown для одной функции.
        """
        name = function_data.get(
            "extracted_go_full_name",
            function_data.get("potential_function_name", "N/A"),
        )
        content = f"# Функция: {name}\n\n"
        content += f"## Полное Go имя: `{function_data.get('extracted_go_full_name', 'N/A')}`\n\n"

        function_type = function_data.get("function_type", "N/A")
        sphere = function_data.get("sphere", "N/A")
        sphere_parent = function_data.get("sphere_parent", "N/A")

        content += f"**Тип:** {function_type}\n"
        content += f"**Сфера:** {sphere}"
        if sphere_parent and sphere_parent != "N/A":
            content += f" (Родительская сфера: {sphere_parent})\n"
        else:
            content += "\n"
        content += f"**Исходный файл:** `{function_data.get('file_path', 'N/A')}`\n\n"

        description = function_data.get("description", "Описание отсутствует.")
        if description:
            content += "## Описание\n"
            content += f"{description}\n\n"

        if "abbreviation" in function_data and function_data["abbreviation"]:
            content += f"**Аббревиатура:** {function_data['abbreviation']}\n\n"

        tags = []
        if function_type and function_type != "N/A":
            tags.append(create_clean_tag(function_type))
        if sphere and sphere != "N/A":
            tags.append(create_clean_tag(sphere))
        if sphere_parent and sphere_parent != "N/A":
            tags.append(create_clean_tag(sphere_parent))

        content += f"**Теги:** {', '.join([f'#{tag}' for tag in tags])}\n\n"

        if "raw_text" in function_data:
            content += "## Исходный текст (из документа)\n"
            content += "```\n"
            content += function_data["raw_text"] + "\n"
            content += "```\n\n"

        # Информация о дубликатах
        is_duplicate = function_data.get("is_duplicate", False)
        llm_verdict = function_data.get("llm_duplicate_verdict", "")
        duplicate_of = function_data.get("duplicate_of_id")

        if is_duplicate:
            content += "## Информация о дубликатах\n"
            content += f"**Статус дубликата:** ДА (подтверждено LLM)\n"
            if duplicate_of is not None:
                content += f"**Является дубликатом функции ID:** `{duplicate_of}`\n"
            if llm_verdict:
                content += f"**Вердикт LLM:** `{llm_verdict}`\n"
            content += "\n"
        elif llm_verdict and "NO_NOT_DUPLICATE" in llm_verdict.upper():
            content += "## Информация о дубликатах\n"
            content += f"**Статус дубликата:** НЕТ (подтверждено LLM)\n"
            content += f"**Вердикт LLM:** `{llm_verdict}`\n\n"

        return content

    def _create_collision_group_name_map(self, df: pd.DataFrame) -> Dict[Any, str]:
        """
        Создает карту имен для групп коллизий.
        """
        # Сначала фильтруем, чтобы не включать -1, который обычно означает "нет группы"
        collision_groups_ids = df[df["collision_group_id"] != -1][
            "collision_group_id"
        ].unique()
        name_map = {}
        for group_id in collision_groups_ids:
            group_members_df = df[df["collision_group_id"] == group_id]
            # Используем "name" или "extracted_go_full_name" для имени
            group_members_names = group_members_df.get(
                "extracted_go_full_name",
                group_members_df.get("potential_function_name"),
            ).tolist()
            name_map[group_id] = " + ".join(
                sorted(filter(None, group_members_names))
            )  # Filter None, чтобы избежать пустых имен
        return name_map

    def _generate_collision_group_files(
        self, df: pd.DataFrame, collision_group_name_map: Dict[Any, str]
    ):
        """
        Генерирует файлы Markdown для групп коллизий.
        """
        collision_dir = os.path.join(self.output_dir, "collisions")
        os.makedirs(collision_dir, exist_ok=True)
        log_message(
            f"Генерация файлов Markdown для групп коллизий в {collision_dir}",
            level="info",
        )

        total_groups = len(collision_group_name_map)
        for i, (group_id, group_name_str) in enumerate(
            collision_group_name_map.items()
        ):
            update_progress(
                i + 1, total_groups, f"Генерация MD: Группа коллизий {group_id}"
            )
            group_df = df[df["collision_group_id"] == group_id].copy()

            filename = sanitize_filename(
                f"collision_group_{group_id}_{group_name_str[:50]}.md"
            )
            filepath = os.path.join(collision_dir, filename)

            content = f"# Группа Коллизий: {group_name_str}\n\n"
            content += f"## Идентификатор Группы: `{group_id}`\n\n"
            content += "Эта группа содержит функции, которые были идентифицированы как дубликаты или тесно связанные:\n\n"

            for _, row in group_df.iterrows():
                func_name = row.get(
                    "extracted_go_full_name", row.get("potential_function_name", "N/A")
                )
                func_type = row.get("function_type", "N/A")
                func_sphere = row.get("sphere", "N/A")
                func_desc_snippet = row.get("description", "")[:100]  # Сниппет описания

                content += f"- **Функция:** {func_name} (Тип: {func_type}, Сфера: {func_sphere})\n"
                if func_desc_snippet:
                    content += f"  > *Описание:* {func_desc_snippet}...\n"
                content += f"  [Подробнее об этой функции](../functions/{sanitize_filename(func_name)}.md)\n\n"

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        log_message("Генерация файлов групп коллизий завершена.", level="info")

    def _add_stats_table_section(self, df: pd.DataFrame, output_file: str):
        """
        Добавляет секцию со статистической таблицей в основной файл Markdown.
        """
        total_functions = len(df)

        # Функции, которые не являются дубликатами по LLM (или не были помечены как кандидаты)
        unique_functions = len(df[df["is_duplicate"] == False])

        # Функции, которые LLM подтвердил как дубликаты
        llm_duplicates_count = len(df[df["is_duplicate"] == True])

        # Количество уникальных групп коллизий (исключая -1)
        collision_groups_count = df[df["collision_group_id"] != -1][
            "collision_group_id"
        ].nunique()

        stats_table = (
            "## Статистика Анализа Функций\n\n"
            "| Метрика               | Значение     |\n"
            "| :-------------------- | :----------- |\n"
            f"| Всего проанализировано функций         | {total_functions}      |\n"
            f"| Уникальных функций (по LLM)    | {unique_functions}     |\n            "
            f"| Функций, подтвержденных как дубликаты (LLM)    | {llm_duplicates_count}     |\n"
            f"| Идентифицировано групп коллизий (похожих функций)        | {collision_groups_count}     |\n"
            "\n"
        )
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(stats_table)
        log_message(f"Раздел статистики добавлен в {output_file}", level="info")

    def _generate_ministry_portraits(self, df: pd.DataFrame):
        """
        Генерирует "портреты" (сводки) по сферам или другим категориям.
        Для простоты, сделаем по сферам.
        """
        portraits_dir = os.path.join(self.output_dir, "portraits")
        os.makedirs(portraits_dir, exist_ok=True)
        log_message(f"Генерация 'портретов' по сферам в {portraits_dir}", level="info")

        if "sphere" not in df.columns or df["sphere"].isnull().all():
            log_message(
                "Столбец 'sphere' отсутствует или пуст. Пропуск генерации портретов.",
                level="warning",
            )
            return

        unique_spheres = df["sphere"].dropna().unique()
        total_spheres = len(unique_spheres)

        for i, sphere in enumerate(unique_spheres):
            update_progress(
                i + 1, total_spheres, f"Генерация MD: Портрет сферы '{sphere}'"
            )
            group_df = df[df["sphere"] == sphere].copy()

            filename = sanitize_filename(f"portrait_sphere_{sphere}.md")
            filepath = os.path.join(portraits_dir, filename)

            content = f"# Портрет Сферы: {sphere}\n\n"
            content += f"Этот документ представляет собой сводку функций, относящихся к сфере **{sphere}**.\n\n"
            content += f"## Функции в этой сфере ({len(group_df)}):\\n\\n"
            for _, row in group_df.iterrows():
                func_name = row.get(
                    "extracted_go_full_name", row.get("potential_function_name", "N/A")
                )
                func_type = row.get("function_type", "N/A")
                func_desc_snippet = row.get("description", "")[:100]

                content += f"- **{func_name}** (Тип: {func_type})\n"
                if func_desc_snippet:
                    content += f"  > *Описание:* {func_desc_snippet}...\n"
                content += f"  [Подробнее об этой функции](../../functions/{sanitize_filename(func_name)}.md)\n\n"

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        log_message("Генерация 'портретов' завершена.", level="info")


async def generate_markdown_reports(
    functions_df: pd.DataFrame,
    output_dir: str,
    config: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Основная функция для генерации всех выходных файлов Markdown.
    functions_df: pandas DataFrame с полностью обработанными данными функций.
    """
    if status_callback:
        status_callback("Начало генерации отчетов Markdown...")
    log_message("Запуск модуля генерации Markdown отчетов.", level="info")

    if functions_df.empty:
        log_message(
            "Входной DataFrame для генерации Markdown пуст. Пропускаем генерацию.",
            level="warning",
        )
        if status_callback:
            status_callback("Генерация MD: Нет данных для обработки.")
        return

    md_module = MarkdownModule(output_dir)

    total_steps = 4  # Индивидуальные функции, группы коллизий, основной отчет, портреты
    current_step = 0

    # 1. Генерация индивидуальных файлов для каждой функции
    current_step += 1
    update_status(
        f"Генерация MD: Этап {current_step}/{total_steps} - Индивидуальные файлы функций"
    )
    log_message("Генерация индивидуальных файлов для каждой функции.", level="info")
    functions_dir = os.path.join(md_module.output_dir, "functions")
    os.makedirs(functions_dir, exist_ok=True)

    total_functions = len(functions_df)
    for i, (_, row) in enumerate(functions_df.iterrows()):
        filename = (
            sanitize_filename(
                row.get(
                    "extracted_go_full_name",
                    row.get("potential_function_name", f"function_{i}"),
                )
            )
            + ".md"
        )
        filepath = os.path.join(functions_dir, filename)
        md_content = md_module._generate_function_md_content(row.to_dict())
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)
        if progress_callback:
            progress_callback(
                i + 1,
                total_functions,
                f"Генерация MD: {row.get('extracted_go_full_name', f'Функция {i + 1}')}",
            )
        if (i + 1) % 50 == 0:
            log_message(
                f"Сгенерировано {i + 1}/{total_functions} файлов функций.",
                level="debug",
            )
    log_message("Генерация индивидуальных файлов функций завершена.", level="info")
    if progress_callback:
        progress_callback(
            total_functions,
            total_functions,
            "Генерация MD: Индивидуальные файлы функций завершены",
        )

    # 2. Генерация файлов для групп коллизий
    current_step += 1
    update_status(f"Генерация MD: Этап {current_step}/{total_steps} - Группы коллизий")
    if "collision_group_id" in functions_df.columns:
        collision_group_name_map = md_module._create_collision_group_name_map(
            functions_df
        )
        md_module._generate_collision_group_files(
            functions_df, collision_group_name_map
        )
    else:
        log_message(
            "Столбец 'collision_group_id' не найден. Пропуск генерации файлов групп коллизий.",
            level="warning",
        )
    if progress_callback:
        progress_callback(
            current_step, total_steps, "Генерация MD: Группы коллизий завершены"
        )

    # 3. Генерация основного файла отчета со статистикой
    current_step += 1
    update_status(f"Генерация MD: Этап {current_step}/{total_steps} - Основной отчет")
    main_report_file = os.path.join(md_module.output_dir, "full_report.md")
    with open(main_report_file, "w", encoding="utf-8") as f:
        f.write("# Общий Отчет Анализа Функций\n\n")
        f.write(
            "Этот отчет содержит сводную информацию по всем проанализированным функциям.\n\n"
        )
    md_module._add_stats_table_section(functions_df, main_report_file)
    if progress_callback:
        progress_callback(
            current_step, total_steps, "Генерация MD: Основной отчет завершен"
        )

    # 4. Генерация "портретов" (сводок)
    current_step += 1
    update_status(f"Генерация MD: Этап {current_step}/{total_steps} - Портреты")
    md_module._generate_ministry_portraits(functions_df)
    if progress_callback:
        progress_callback(current_step, total_steps, "Генерация MD: Портреты завершены")

    log_message(
        f"Markdown файлы успешно сгенерированы в директории: {md_module.output_dir}",
        level="info",
    )
    if status_callback:
        status_callback("Генерация отчетов Markdown завершена.")
    if progress_callback:
        progress_callback(
            total_steps, total_steps, "Генерация Markdown отчетов завершена"
        )


if __name__ == "__main__":
    # Пример использования (для автономного тестирования)
    import sys
    import os
    import queue
    import logging
    import asyncio

    logging.basicConfig(level=logging.INFO)

    sys.path.append(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    from backend.utils import set_ui_queue_callback

    # Имитация UI очереди для логирования
    mock_ui_queue = queue.Queue()
    set_ui_queue_callback(mock_ui_queue.put)

    # Фиктивный DataFrame, имитирующий выходные данные предыдущих модулей
    # В этом DF уже должны быть колонки с результатами всех предыдущих этапов
    dummy_processed_data = {
        "id": [1, 2, 3, 4, 5, 6],
        "extracted_go_full_name": [
            "pkg.user.Login",
            "pkg.auth.Authenticate",
            "pkg.data.Fetch",
            "pkg.data.Retrieve",
            "pkg.util.Logger",
            "pkg.sys.LogWriter",
        ],
        "potential_function_name": [
            "LoginUser",
            "AuthCheck",
            "GetData",
            "GetRecords",
            "LogEvent",
            "SysLog",
        ],
        "description": [
            "Handles user login and session creation.",
            "Verifies user identity and manages access tokens.",
            "Fetches data from an external API.",
            "Retrieves records from the database based on criteria.",
            "Logs application events to a file.",
            "Writes system log messages to a persistent store.",
        ],
        "raw_text": [
            "func Login(user, pass string) error {}",
            "func Authenticate(token string) (bool, error) {}",
            "func Fetch(endpoint string) ([]byte, error) {}",
            "func Retrieve(query string) ([]Row, error) {}",
            "func Logger(event string, level int) {}",
            "func SysLog(message string) {}",
        ],
        "function_type": [
            "Authentication",
            "Authentication",
            "Data Access",
            "Data Access",
            "Logging",
            "Logging",
        ],
        "sphere": [
            "Security",
            "Security",
            "Data Management",
            "Data Management",
            "System Utilities",
            "System Utilities",
        ],
        "sphere_parent_id": ["S1", "S1", "S2", "S2", "S3", "S3"],
        "sphere_parent": [
            "Core Services",
            "Core Services",
            "Data Layer",
            "Data Layer",
            "Platform",
            "Platform",
        ],
        "is_duplicate_candidate": [True, True, True, True, False, False],
        "collision_group_id": [
            0,
            0,
            1,
            1,
            -1,
            -1,
        ],  # Функции 1,2 в группе 0; 3,4 в группе 1
        "is_duplicate": [
            True,
            True,
            False,
            False,
            False,
            False,
        ],  # LLM подтвердил дубликаты для группы 0
        "duplicate_of_id": [2, 1, None, None, None, None],
        "llm_duplicate_verdict": [
            "YES_DUPLICATE",
            "YES_DUPLICATE",
            "NO_NOT_DUPLICATE",
            "NO_NOT_DUPLICATE",
            "",
            "",
        ],
    }
    dummy_functions_df = pd.DataFrame(dummy_processed_data)

    mock_output_dir = "test_md_reports_output"
    mock_config = {}  # Для этого модуля конфиг не так критичен, но можно добавить настройки для промптов если будут

    async def test_markdown_module():
        print(
            f"Запуск тестовой функции generate_markdown_reports в директорию: {mock_output_dir}"
        )
        await generate_markdown_reports(
            functions_df=dummy_functions_df.copy(),
            output_dir=mock_output_dir,
            config=mock_config,
            progress_callback=lambda c, t, s: print(f"Прогресс: {s} - {c}/{t}"),
            status_callback=lambda msg: print(f"Статус: {msg}"),
        )
        print("\n--- Сообщения из UI очереди (моделирование) ---")
        while not mock_ui_queue.empty():
            print(mock_ui_queue.get())

        print(
            f"\nПроверьте папку '{mock_output_dir}' для сгенерированных Markdown файлов."
        )
        # Очистка временных файлов (опционально)
        # import shutil
        # if os.path.exists(mock_output_dir):
        #     shutil.rmtree(mock_output_dir)

    asyncio.run(test_markdown_module())
    print("Демонстрация markdown_module.py завершена.")
