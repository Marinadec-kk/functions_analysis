import pandas as pd
import asyncio
import json
from typing import Dict, Any, List, Optional, Callable

from .utils import (
    log_message,
    update_status,
    update_progress,
    prepare_api_base_url,
    call_llm_for_verdict,
    read_table_auto,
)


async def _verification_worker(
    pair_data_list: List[Dict[str, Any]],
    config: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    current_offset: int = 0,
    total_items: int = 0,
) -> List[Dict[str, Any]]:
    """
    Работник для асинхронной верификации дубликатов с использованием LLM.
    Обрабатывает список пар данных и возвращает результаты.
    """
    results = []
    llm_api_base_url = config.get("ai_api_base_url", "")
    llm_api_key = config.get("ai_api_key", "")
    llm_model = config.get("llm_duplicate_verification_model_name", "gpt-3.5-turbo")
    llm_prompt_template = config.get(
        "llm_duplicate_verification_prompt",
        "Are the following two texts semantically similar enough to be considered duplicates? "
        "Respond with 'YES_DUPLICATE' or 'NO_NOT_DUPLICATE'.\\nText 1: {text1}\\nText 2: {text2}",
    )
    llm_max_tokens = config.get("llm_max_tokens", 50)
    llm_temperature = config.get("llm_temperature", 0.2)
    api_max_retries = config.get("api_max_retries", 3)
    api_timeout = config.get("api_timeout", 60)

    if not llm_api_base_url or not llm_api_key:
        log_message(
            "API для LLM не настроено. Пропускаем LLM верификацию.", level="warning"
        )
        for pair in pair_data_list:
            pair["llm_verdict"] = "LLM_SKIPPED_CONFIG_MISSING"
            results.append(pair)
        return results

    processed_count_in_batch = 0
    for pair in pair_data_list:
        try:
            verdict_data = await call_llm_for_verdict(
                text1=f"{pair.get('name_func1', '')} - {pair.get('desc_func1', '')}",
                text2=f"{pair.get('name_func2', '')} - {pair.get('desc_func2', '')}",
                prompt_template=llm_prompt_template,
                config_dict=config,  # Pass the entire config dictionary
                max_tokens=llm_max_tokens,
                temperature=llm_temperature,
                timeout=api_timeout,
            )
            pair["llm_verdict"] = verdict_data.get("verdict", "ERROR_LLM_RESPONSE")
        except Exception as e:
            log_message(
                f"Ошибка при LLM верификации пары ({pair.get('id1', 'N/A')}-{pair.get('id2', 'N/A')}): {e}",
                level="error",
            )
            pair["llm_verdict"] = f"ERROR: {e}"
        results.append(pair)
        processed_count_in_batch += 1
        if progress_callback:
            progress_callback(
                current_offset + processed_count_in_batch,
                total_items,
                "LLM Верификация дубликатов",
            )

    return results


def update_dataframe_with_results(
    original_functions_df: pd.DataFrame,
    verified_pairs_df: pd.DataFrame,
    id_col: str = "id",
) -> pd.DataFrame:
    """
    Обновляет исходный DataFrame функций результатами верификации дубликатов.
    Добавляет колонки 'is_duplicate' (на основе LLM), 'duplicate_of_id' и 'llm_verdict_summary'.
    """
    if original_functions_df.empty or verified_pairs_df.empty:
        log_message("Один из DataFrame пуст, обновление пропущено.", level="warning")
        return original_functions_df

    # Инициализируем новые колонки
    if "is_duplicate" not in original_functions_df.columns:
        original_functions_df["is_duplicate"] = False
    if "duplicate_of_id" not in original_functions_df.columns:
        original_functions_df["duplicate_of_id"] = (
            None  # ID функции, с которой эта дублируется
        )
    if "llm_verdict_summary" not in original_functions_df.columns:
        original_functions_df["llm_verdict_summary"] = ""
    if "collision_group_id" not in original_functions_df.columns:
        original_functions_df[
            "collision_group_id"
        ] = -1  # Группа коллизий (пока не используется явно здесь, но может быть позже)

    # Создаем временный словарь для быстрого поиска связей
    # Ключ: ID функции, Значение: {is_duplicate: bool, duplicate_of_id: Any, llm_verdict: str}
    duplicate_info: Dict[Any, Dict[str, Any]] = {}

    for _, row in verified_pairs_df.iterrows():
        id1 = row["id1"]
        id2 = row["id2"]
        llm_verdict = row["llm_verdict"]

        is_llm_duplicate = "yes_duplicate" in str(llm_verdict).lower()

        # Обновляем информацию для id1
        if is_llm_duplicate:
            if id1 not in duplicate_info:
                duplicate_info[id1] = {
                    "is_duplicate": True,
                    "duplicate_of_ids": [],
                    "llm_verdicts": [],
                }
            duplicate_info[id1]["duplicate_of_ids"].append(id2)
            duplicate_info[id1]["llm_verdicts"].append(f"->{id2}: {llm_verdict}")
        else:
            # Если LLM говорит, что это не дубликат, это также информация.
            # Мы можем пометить это, но не как "is_duplicate".
            if id1 not in duplicate_info:
                duplicate_info[id1] = {
                    "is_duplicate": False,
                    "duplicate_of_ids": [],
                    "llm_verdicts": [],
                }
            duplicate_info[id1]["llm_verdicts"].append(f"->{id2}: {llm_verdict}")

        # Обновляем информацию для id2
        if is_llm_duplicate:
            if id2 not in duplicate_info:
                duplicate_info[id2] = {
                    "is_duplicate": True,
                    "duplicate_of_ids": [],
                    "llm_verdicts": [],
                }
            duplicate_info[id2]["duplicate_of_ids"].append(id1)
            duplicate_info[id2]["llm_verdicts"].append(f"->{id1}: {llm_verdict}")
        else:
            if id2 not in duplicate_info:
                duplicate_info[id2] = {
                    "is_duplicate": False,
                    "duplicate_of_ids": [],
                    "llm_verdicts": [],
                }
            duplicate_info[id2]["llm_verdicts"].append(f"->{id1}: {llm_verdict}")

    # Применяем собранную информацию к исходному DataFrame
    for func_id, info in duplicate_info.items():
        # Находим строку по id_col
        idx_in_df = original_functions_df.index[
            original_functions_df[id_col] == func_id
        ].tolist()
        if not idx_in_df:
            continue  # Если функция не найдена в исходном DF, пропускаем

        # Обновляем флаг is_duplicate
        original_functions_df.loc[idx_in_df[0], "is_duplicate"] = info["is_duplicate"]

        # duplicate_of_id: Можно взять первый из списка или сделать строкой с несколькими ID
        if info["duplicate_of_ids"]:
            original_functions_df.loc[idx_in_df[0], "duplicate_of_id"] = info[
                "duplicate_of_ids"
            ][0]
        else:
            original_functions_df.loc[idx_in_df[0], "duplicate_of_id"] = None

        # llm_verdict_summary
        original_functions_df.loc[idx_in_df[0], "llm_verdict_summary"] = "; ".join(
            info["llm_verdicts"]
        )

    log_message(
        "DataFrame функций обновлен результатами верификации дубликатов.", level="info"
    )
    return original_functions_df


def save_file(df: pd.DataFrame, output_path: str):
    """
    Сохраняет DataFrame в указанный файл.
    Поддерживает CSV и Excel.
    """
    try:
        if output_path.endswith(".csv"):
            df.to_csv(output_path, index=False, encoding="utf-8")
        elif output_path.endswith(".xlsx"):
            df.to_excel(output_path, index=False, engine="xlsxwriter")
        else:
            raise ValueError("Unsupported output file format. Use .csv or .xlsx.")
        log_message(f"Успешно сохранены результаты в {output_path}", level="info")
    except Exception as e:
        log_message(f"Ошибка сохранения файла в {output_path}: {e}", level="error")
        raise


async def verify_duplicates(
    functions_df: pd.DataFrame,
    config: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """
    Основной публичный интерфейс для гибридной проверки дубликатов.
    Принимает DataFrame со всеми функциями, включая кандидатов на дублирование
    (предполагается, что 'is_duplicate_candidate' и 'collision_group_id' уже установлены).
    """
    if status_callback:
        status_callback("Начало гибридной проверки дубликатов с LLM...")
    log_message("Starting hybrid duplicate verification.", level="info")

    if functions_df.empty:
        log_message(
            "Входной DataFrame функций пуст. Пропускаем проверку дубликатов.",
            level="warning",
        )
        if status_callback:
            status_callback("Проверка дубликатов: Нет данных для обработки.")
        return functions_df.copy()

    # Фильтруем только те функции, которые являются кандидатами на дублирование
    # и имеют связанную группу коллизий.
    duplicate_candidates_df = functions_df[
        (functions_df["is_duplicate_candidate"] == True)
        & (functions_df["collision_group_id"] != -1)
    ].copy()

    if duplicate_candidates_df.empty:
        log_message("Нет кандидатов на дублирование для верификации.", level="info")
        if status_callback:
            status_callback("Проверка дубликатов: Нет кандидатов для верификации.")
        return functions_df.copy()

    # Генерируем пары для LLM-верификации из групп коллизий
    # Для каждой группы коллизий, создаем пары из всех элементов в группе
    pairs_to_verify_list: List[Dict[str, Any]] = []
    id_col = config.get("function_id_column", "id")
    name_col = config.get("function_name_column", "extracted_go_full_name")
    desc_col = config.get("function_description_column", "description")

    for group_id in duplicate_candidates_df["collision_group_id"].unique():
        group_members = duplicate_candidates_df[
            duplicate_candidates_df["collision_group_id"] == group_id
        ]
        member_ids = group_members[id_col].tolist()

        for i in range(len(member_ids)):
            for j in range(i + 1, len(member_ids)):
                id1 = member_ids[i]
                id2 = member_ids[j]

                func1_data = group_members[group_members[id_col] == id1].iloc[0]
                func2_data = group_members[group_members[id_col] == id2].iloc[0]

                pairs_to_verify_list.append(
                    {
                        "id1": id1,
                        "name_func1": func1_data.get(name_col, ""),
                        "desc_func1": func1_data.get(desc_col, ""),
                        "id2": id2,
                        "name_func2": func2_data.get(name_col, ""),
                        "desc_func2": func2_data.get(desc_col, ""),
                    }
                )

    if not pairs_to_verify_list:
        log_message("Не сгенерировано ни одной пары для LLM верификации.", level="info")
        if status_callback:
            status_callback("Проверка дубликатов: Нет пар для LLM верификации.")
        return functions_df.copy()

    total_pairs = len(pairs_to_verify_list)
    update_status(f"Начало LLM верификации для {total_pairs} пар...")
    log_message(f"Starting LLM verification for {total_pairs} pairs.", level="info")

    batch_size = config.get(
        "llm_verification_batch_size", 5
    )  # Настраиваемый размер батча
    processed_count = 0
    all_verified_results: List[Dict[str, Any]] = []

    tasks = []
    for i in range(0, total_pairs, batch_size):
        batch = pairs_to_verify_list[i : i + batch_size]
        tasks.append(
            _verification_worker(
                batch,
                config,
                progress_callback,
                current_offset=i,
                total_items=total_pairs,
            )
        )

    # Выполняем все батчи параллельно
    for i, future in enumerate(asyncio.as_completed(tasks)):
        batch_results = await future
        all_verified_results.extend(batch_results)
        # Прогресс обновляется внутри worker, здесь просто убедимся, что все завершено

    verified_pairs_df = pd.DataFrame(all_verified_results)
    log_message("LLM верификация дубликатов завершена.", level="info")
    update_status("LLM верификация дубликатов завершена. Обновление DataFrame...")

    # Обновляем исходный functions_df с результатами верификации
    final_functions_df = update_dataframe_with_results(
        original_functions_df=functions_df.copy(),
        verified_pairs_df=verified_pairs_df,
        id_col=id_col,
    )

    log_message("Гибридная проверка дубликатов завершена.", level="info")
    if status_callback:
        status_callback("Гибридная проверка дубликатов завершена.")

    return final_functions_df


if __name__ == "__main__":
    # Пример использования модуля (для автономного тестирования)
    import sys
    import os
    import queue
    import logging

    logging.basicConfig(level=logging.INFO)

    sys.path.append(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    from backend.utils import set_ui_queue_callback

    # Имитация UI очереди для логирования
    mock_ui_queue = queue.Queue()
    set_ui_queue_callback(mock_ui_queue.put)

    # Фиктивные данные после группировки (grouping_module)
    dummy_functions_data = {
        "id": [1, 2, 3, 4, 5, 6],
        "extracted_go_full_name": [
            "pkg.auth.Login",
            "pkg.auth.Authenticate",
            "pkg.data.ProcessInput",
            "pkg.data.HandleData",
            "pkg.report.GenerateReport",
            "pkg.log.WriteLog",
        ],
        "description": [
            "Handles user login and session creation.",
            "Verifies user identity and manages access tokens. Similar to Login.",
            "Processes raw input data from external sources.",
            "Handles and transforms incoming data streams. Similar to ProcessInput.",
            "Creates a detailed financial report in PDF format.",
            "Writes system events and errors to a log file.",
        ],
        "function_type": [
            "Authentication",
            "Authentication",
            "Data Processing",
            "Data Processing",
            "Reporting",
            "Logging",
        ],
        "sphere": [
            "Security",
            "Security",
            "Data Management",
            "Data Management",
            "Reporting & Analytics",
            "Operations",
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
    }
    dummy_functions_df = pd.DataFrame(dummy_functions_data)

    # Фиктивная конфигурация (замените на реальные данные при тестировании)
    mock_config = {
        "ai_api_base_url": os.getenv("AI_API_BASE_URL", "http://localhost:8000/api"),
        "ai_api_key": os.getenv("AI_API_KEY", "YOUR_ACTUAL_AI_API_KEY"),
        "ai_api_version": "v1",
        "llm_duplicate_verification_model_name": "gpt-3.5-turbo",
        "llm_duplicate_verification_prompt": (
            "Review the following two function descriptions and determine if they describe the same underlying function or highly overlapping functionality. "
            "Respond only with 'YES_DUPLICATE' if they are duplicates, or 'NO_NOT_DUPLICATE' if they are distinct. "
            "Text 1: {text1}\\nText 2: {text2}"
        ),
        "llm_max_tokens": 50,
        "llm_temperature": 0.2,
        "api_max_retries": 1,
        "api_timeout": 30,
        "llm_verification_batch_size": 2,  # Малый размер батча для демонстрации
        "function_id_column": "id",
        "function_name_column": "extracted_go_full_name",
        "function_description_column": "description",
    }

    async def run_test():
        print("Запуск тестовой функции verify_duplicates...")
        final_df = await verify_duplicates(
            functions_df=dummy_functions_df.copy(),
            config=mock_config,
            progress_callback=lambda c, t, s: print(f"Прогресс: {s} - {c}/{t}"),
            status_callback=lambda msg: print(f"Статус: {msg}"),
        )
        print("\n--- Результаты верификации дубликатов ---")
        print(final_df)

        print("\n--- Сообщения из UI очереди (моделирование) ---")
        while not mock_ui_queue.empty():
            print(mock_ui_queue.get())

    asyncio.run(run_test())
    print("Демонстрация duplicates_module.py завершена.")
