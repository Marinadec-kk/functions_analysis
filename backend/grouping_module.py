import pandas as pd
from typing import List, Dict, Any, Optional, Tuple, Callable
import asyncio
import numpy as np
from scipy.spatial.distance import cosine
import logging

from .utils import (
    log_message,
    update_status,
    update_progress,
    prepare_api_base_url,
    call_api_with_backoff,  # General API call, for LLM specific usage
    call_llm_for_verdict,  # Specific LLM call
    async_get_embedding_batch,
    read_table_auto,
)

logger = logging.getLogger(__name__)


class RoundRobin:
    """A simple round-robin scheduler for API base URLs."""

    def __init__(self, urls: List[str]):
        if not urls:
            raise ValueError("RoundRobin must be initialized with at least one URL.")
        self.urls = urls
        self.index = 0

    def next(self) -> str:
        url = self.urls[self.index]
        self.index = (self.index + 1) % len(self.urls)
        return url


def cosine_similarity_matrix(
    embeddings1: np.ndarray, embeddings2: np.ndarray
) -> np.ndarray:
    """
    Calculates the cosine similarity matrix between two sets of embeddings.
    """
    if embeddings1.ndim == 1:
        embeddings1 = embeddings1.reshape(1, -1)
    if embeddings2.ndim == 1:
        embeddings2 = embeddings2.reshape(1, -1)

    # Нормализуем векторы, если они еще не нормализованы
    # np.linalg.norm(..., axis=1, keepdims=True) предотвращает деление на ноль для нулевых векторов
    norm1 = np.linalg.norm(embeddings1, axis=1, keepdims=True)
    norm2 = np.linalg.norm(embeddings2, axis=1, keepdims=True)

    # Избегаем деления на ноль: заменяем нули на 1 в нормах для безопасного деления
    norm1[norm1 == 0] = 1
    norm2[norm2 == 0] = 1

    embeddings1_norm = embeddings1 / norm1
    embeddings2_norm = embeddings2 / norm2

    return embeddings1_norm @ embeddings2_norm.T


async def _process_embedding_batch_and_store(
    texts_with_ids: List[Tuple[int, str]],
    config_dict: Dict[str, Any],
    results_dict: Dict[int, List[float]],
):
    """Worker to fetch embeddings for a batch of texts and store them in a shared dictionary."""
    texts = [text for _, text in texts_with_ids]
    indices = [idx for idx, _ in texts_with_ids]

    try:
        embeddings = await async_get_embedding_batch(
            texts=texts,
            config_dict=config_dict,
        )
        for i, emb in enumerate(embeddings):
            results_dict[indices[i]] = emb
    except Exception as e:
        log_message(f"Ошибка при получении эмбеддингов для пакета: {e}", level="error")
        for idx in indices:
            results_dict[idx] = []  # Store empty list or None for failed embeddings


async def calculate_embeddings_for_df(
    df: pd.DataFrame,
    id_column: str,
    text_column: str,
    config_dict: Dict[str, Any],  # New parameter
    batch_size: int = 16,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Dict[Any, List[float]]:
    """
    Calculates and returns embeddings for all texts in a DataFrame column.
    Returns a dictionary mapping DataFrame ID to embedding vector.
    """
    if status_callback:
        status_callback(f"Начало вычисления эмбеддингов для {len(df)} элементов.")
    log_message(f"Вычисление эмбеддингов для {len(df)} элементов.", level="info")

    all_texts_with_ids = [
        (row[id_column], row[text_column]) for _, row in df.iterrows()
    ]
    embeddings_map: Dict[Any, List[float]] = {}

    tasks = []
    total_batches = (len(all_texts_with_ids) + batch_size - 1) // batch_size
    current_batch_num = 0

    for i in range(0, len(all_texts_with_ids), batch_size):
        batch = all_texts_with_ids[i : i + batch_size]
        current_batch_num += 1
        tasks.append(
            _process_embedding_batch_and_store(batch, config_dict, embeddings_map)
        )

    # Run tasks with progress reporting
    for i, task in enumerate(asyncio.as_completed(tasks)):
        await task  # Wait for each batch to complete and store results
        if progress_callback:
            progress_callback(
                i + 1,
                total_batches,
                f"Получение эмбеддингов (партия {i + 1}/{total_batches})",
            )
        if (i + 1) % 5 == 0:  # Update status less frequently
            if status_callback:
                status_callback(
                    f"Получено эмбеддингов для {len(embeddings_map)} элементов."
                )

    if status_callback:
        status_callback("Вычисление эмбеддингов завершено.")
    log_message(
        f"Вычисление эмбеддингов завершено. Получено {len(embeddings_map)} эмбеддингов.",
        level="info",
    )
    return embeddings_map


async def find_candidate_pairs(
    df: pd.DataFrame,
    id_column: str,
    text_column: str,  # Имя колонки, которая содержит основной текст для сравнения
    embeddings_map: Dict[Any, List[float]],
    similarity_threshold: float = 0.8,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """
    Finds candidate pairs of functions that might be duplicates based on cosine similarity.
    Returns a DataFrame of potential collision pairs.
    """
    if status_callback:
        status_callback(
            "Поиск кандидатов на дубликаты на основе сходства эмбеддингов..."
        )
    log_message("Поиск кандидатов на дубликаты.", level="info")

    if not embeddings_map or len(embeddings_map) < 2:
        log_message("Недостаточно эмбеддингов для поиска пар.", level="warning")
        return pd.DataFrame()

    # Сортируем индексы, чтобы матрица сходства была упорядоченной
    sorted_ids = sorted(embeddings_map.keys())
    embeddings = np.array([embeddings_map[idx] for idx in sorted_ids])

    # Проверка на наличие хотя бы одного непустого эмбеддинга
    if not np.any(embeddings):
        log_message(
            "Все эмбеддинги пусты, невозможно вычислить сходство.", level="warning"
        )
        return pd.DataFrame()

    # Применяем проверку для исключения нулевых векторов перед нормализацией в cosine_similarity_matrix
    # Это предотвращается внутри самой функции cosine_similarity_matrix

    similarity_matrix_result = cosine_similarity_matrix(embeddings, embeddings)
    np.fill_diagonal(similarity_matrix_result, 0)  # Не сравниваем элемент сам с собой

    candidate_pairs = []
    total_comparisons = len(sorted_ids) * (len(sorted_ids) - 1) // 2
    current_comparison = 0

    for i in range(len(sorted_ids)):
        for j in range(i + 1, len(sorted_ids)):
            current_comparison += 1
            if (
                progress_callback and current_comparison % 1000 == 0
            ):  # Обновлять прогресс реже для больших данных
                progress_callback(
                    current_comparison, total_comparisons, "Поиск схожих пар"
                )

            if similarity_matrix_result[i, j] > similarity_threshold:
                id1 = sorted_ids[i]
                id2 = sorted_ids[j]

                # Извлекаем данные из исходного DataFrame по оригинальным ID
                text1 = df.loc[df[id_column] == id1, text_column].iloc[0]
                text2 = df.loc[df[id_column] == id2, text_column].iloc[0]

                candidate_pairs.append(
                    {
                        "id1": id1,
                        "text1": text1,
                        "id2": id2,
                        "text2": text2,
                        "similarity": float(similarity_matrix_result[i, j]),
                    }
                )

    if progress_callback:
        progress_callback(
            total_comparisons, total_comparisons, "Поиск схожих пар завершен"
        )

    candidates_df = pd.DataFrame(candidate_pairs)
    log_message(f"Найдено {len(candidates_df)} пар-кандидатов.", level="info")
    if status_callback:
        status_callback(f"Найдено {len(candidates_df)} пар-кандидатов на дубликаты.")

    return candidates_df


import os
import json


async def group_and_find_candidates(
    functions_df: pd.DataFrame,
    config: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """
    Основной публичный интерфейс для группировки и поиска кандидатов на дубликаты.
    Поддерживает предварительную группировку и фильтрацию "универсальных" функций.
    """
    if status_callback:
        status_callback("Начало этапа группировки и поиска кандидатов на дубликаты...")
    log_message("Запуск модуля группировки и поиска дубликатов.", level="info")

    if functions_df.empty:
        log_message("Входной DataFrame пуст, пропускаем группировку.", level="warning")
        if status_callback:
            status_callback("Группировка: Нет данных для обработки.")
        return functions_df.copy()

    # --- Извлечение конфигурации ---
    similarity_threshold = config.get("similarity_threshold", 0.8)
    id_column = config.get("function_id_column", "id")
    text_column = config.get("function_description_column", "description")
    grouping_cols_str = config.get("grouping_cols", "")
    grouping_cols = (
        [col.strip() for col in grouping_cols_str.split(",") if col.strip()]
        if grouping_cols_str
        else []
    )

    universal_json_path = config.get("universal_json_file", "")
    univ_threshold = config.get("univ_threshold", 0.9)

    if id_column not in functions_df.columns or text_column not in functions_df.columns:
        log_message(
            f"Необходимые колонки '{id_column}' или '{text_column}' отсутствуют.",
            level="error",
        )
        if status_callback:
            status_callback("Ошибка: Отсутствуют необходимые колонки для группировки.")
        return functions_df.copy()

    # --- Инициализация колонок результатов ---
    functions_df["is_universal"] = False
    functions_df["is_duplicate_candidate"] = False
    functions_df["collision_group_id"] = -1

    # --- 1. Вычисление эмбеддингов для всех функций ---
    embedding_map = await calculate_embeddings_for_df(
        df=functions_df,
        id_column=id_column,
        text_column=text_column,
        config_dict=config,
        progress_callback=progress_callback,
        status_callback=status_callback,
    )

    if not embedding_map:
        log_message(
            "Эмбеддинги не были получены. Пропуск поиска кандидатов.", level="warning"
        )
        if status_callback:
            status_callback("Группировка: Не удалось получить эмбеддинги.")
        return functions_df.copy()

    # --- 2. Фильтрация "универсальных" функций ---
    if universal_json_path and os.path.exists(universal_json_path):
        if status_callback:
            status_callback("Обнаружение универсальных функций...")
        try:
            with open(universal_json_path, "r", encoding="utf-8") as f:
                universal_funcs_data = json.load(f)

            universal_texts = [
                item["text"] for item in universal_funcs_data if "text" in item
            ]
            if not universal_texts:
                raise ValueError("JSON с универсальными функциями не содержит текстов.")

            universal_embeddings = await async_get_embedding_batch(
                universal_texts, config
            )
            universal_embeddings_np = np.array(universal_embeddings)

            all_func_ids = list(embedding_map.keys())
            all_func_embeddings_np = np.array(
                [embedding_map[fid] for fid in all_func_ids]
            )

            similarity_matrix = cosine_similarity_matrix(
                all_func_embeddings_np, universal_embeddings_np
            )

            max_sim_per_func = np.max(similarity_matrix, axis=1)
            universal_count = 0
            for i, func_id in enumerate(all_func_ids):
                if max_sim_per_func[i] > univ_threshold:
                    functions_df.loc[
                        functions_df[id_column] == func_id, "is_universal"
                    ] = True
                    universal_count += 1
            log_message(
                f"Найдено и помечено {universal_count} универсальных функций.",
                level="info",
            )
            if status_callback:
                status_callback(f"Найдено {universal_count} универсальных функций.")

        except Exception as e:
            log_message(
                f"Ошибка при обработке универсальных функций: {e}", level="error"
            )
            if status_callback:
                status_callback(f"Ошибка обработки универсальных функций: {e}")

    # --- 3. Поиск кандидатов на дубликаты ---
    search_df = functions_df[~functions_df["is_universal"]].copy()
    log_message(
        f"Исключив универсальные, ищем дубликаты среди {len(search_df)} функций.",
        level="info",
    )

    all_candidate_pairs_df = []

    if grouping_cols and all(col in search_df.columns for col in grouping_cols):
        log_message(
            f"Выполняется поиск дубликатов с группировкой по колонкам: {grouping_cols}",
            level="info",
        )
        if status_callback:
            status_callback(f"Группировка по {grouping_cols}...")

        grouped = search_df.groupby(grouping_cols)
        num_groups = len(grouped)
        for i, (_, group_df) in enumerate(grouped):
            if len(group_df) < 2:
                continue

            group_embedding_map = {
                idx: embedding_map[idx]
                for idx in group_df[id_column]
                if idx in embedding_map
            }

            if status_callback:
                status_callback(
                    f"Анализ группы {i + 1}/{num_groups} (размер: {len(group_df)})..."
                )

            candidate_pairs_in_group = await find_candidate_pairs(
                df=group_df,
                id_column=id_column,
                text_column=text_column,
                embeddings_map=group_embedding_map,
                similarity_threshold=similarity_threshold,
                # progress_callback и status_callback можно передать, но это будет шумно
            )
            if not candidate_pairs_in_group.empty:
                all_candidate_pairs_df.append(candidate_pairs_in_group)
    else:
        if grouping_cols:
            log_message(
                f"Колонки для группировки {grouping_cols} не найдены. Выполняется глобальный поиск.",
                level="warning",
            )
            if status_callback:
                status_callback(
                    "Внимание: Колонки для группировки не найдены. Глобальный поиск."
                )
        else:
            log_message("Выполняется глобальный поиск дубликатов.", level="info")

        candidate_pairs = await find_candidate_pairs(
            df=search_df,
            id_column=id_column,
            text_column=text_column,
            embeddings_map={
                k: v
                for k, v in embedding_map.items()
                if k in search_df[id_column].values
            },
            similarity_threshold=similarity_threshold,
            progress_callback=progress_callback,
            status_callback=status_callback,
        )
        if not candidate_pairs.empty:
            all_candidate_pairs_df.append(candidate_pairs)

    if not all_candidate_pairs_df:
        log_message("Кандидаты на дубликаты не найдены.", level="info")
        if status_callback:
            status_callback("Кандидаты на дубликаты не найдены.")
        return functions_df

    final_candidate_pairs = pd.concat(all_candidate_pairs_df, ignore_index=True)
    log_message(
        f"Всего найдено {len(final_candidate_pairs)} пар-кандидатов.", level="info"
    )

    # --- 4. Создание групп коллизий ---
    collision_groups: Dict[Any, int] = {}
    next_group_id = 0

    for _, row in final_candidate_pairs.iterrows():
        id1, id2 = row["id1"], row["id2"]
        group1 = collision_groups.get(id1)
        group2 = collision_groups.get(id2)

        if group1 is None and group2 is None:
            collision_groups[id1] = next_group_id
            collision_groups[id2] = next_group_id
            next_group_id += 1
        elif group1 is None:
            collision_groups[id1] = group2
        elif group2 is None:
            collision_groups[id2] = group1
        elif group1 != group2:
            # Объединяем группы
            old_group_id, new_group_id = (group2, group1)
            for key, val in collision_groups.items():
                if val == old_group_id:
                    collision_groups[key] = new_group_id

    # Обновляем DataFrame с группами
    for original_id, group_id in collision_groups.items():
        functions_df.loc[
            functions_df[id_column] == original_id, "collision_group_id"
        ] = group_id
        functions_df.loc[
            functions_df[id_column] == original_id, "is_duplicate_candidate"
        ] = True

    if status_callback:
        status_callback("Группировка и поиск кандидатов завершены.")
    log_message("Модуль группировки и поиска дубликатов завершен.", level="info")

    return functions_df


if __name__ == "__main__":
    # Пример использования модуля для автономного тестирования
    logging.basicConfig(level=logging.INFO)

    # Имитация данных после классификации сфер
    dummy_functions_data = {
        "id": [1, 2, 3, 4, 5, 6],
        "extracted_go_full_name": [
            "pkg.auth.Login",
            "pkg.auth.AuthUser",
            "pkg.data.ProcessInput",
            "pkg.data.HandleData",
            "pkg.report.GenerateReport",
            "pkg.log.WriteLog",
        ],
        "description": [
            "Authenticates user credentials and creates a session.",
            "Verifies user identity and manages access tokens.",
            "Processes raw input data from external sources.",
            "Handles and transforms incoming data streams.",
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
    }
    dummy_functions_df = pd.DataFrame(dummy_functions_data)

    # Имитация конфигурации (замените на реальные данные при тестировании)
    mock_config = {
        "ai_api_base_url": os.getenv(
            "AI_API_BASE_URL", "http://localhost:8000/api"
        ),  # Замените на реальный URL вашего AI API
        "ai_api_key": os.getenv(
            "AI_API_KEY", "YOUR_ACTUAL_AI_API_KEY"
        ),  # Замените на реальный ключ
        "ai_api_version": "v1",
        "embedding_model_name": "text-embedding-ada-002",
        "llm_duplicate_verification_model_name": "gpt-3.5-turbo",
        "similarity_threshold": 0.75,  # Порог для косинусного сходства
        "function_id_column": "id",
        "function_description_column": "description",
        "api_max_retries": 1,
        "api_timeout": 30,
        "llm_duplicate_verification_prompt": (
            "Review the following two function descriptions and determine if they describe the same underlying function or highly overlapping functionality. "
            "Respond only with 'YES_DUPLICATE' if they are duplicates, or 'NO_NOT_DUPLICATE' if they are distinct. "
            "Text 1: {text1}\\nText 2: {text2}"
        ),
    }

    async def run_test():
        print("Запуск тестовой функции group_and_find_candidates...")
        # Для использования utils.log_message и других функций в автономном режиме
        # нужно установить ui_queue_callback
        import queue
        from backend.utils import set_ui_queue_callback

        mock_ui_queue = queue.Queue()
        set_ui_queue_callback(mock_ui_queue.put)

        grouped_df = await group_and_find_candidates(
            functions_df=dummy_functions_df.copy(),
            config=mock_config,
            progress_callback=lambda c, t, s: print(f"Прогресс: {s} - {c}/{t}"),
            status_callback=lambda msg: print(f"Статус: {msg}"),
        )
        print("\n--- Результаты группировки и поиска кандидатов ---")
        print(grouped_df.head(10))  # Увеличим head, чтобы увидеть больше

        # Проверка групп коллизий
        print("\n--- Группы коллизий ---")
        duplicate_candidates = grouped_df[grouped_df["is_duplicate_candidate"] == True]
        if not duplicate_candidates.empty:
            for group_id in duplicate_candidates["collision_group_id"].unique():
                if group_id != -1:  # Игнорируем -1 для "не дубликатов"
                    group_members = duplicate_candidates[
                        duplicate_candidates["collision_group_id"] == group_id
                    ]
                    print(f"Группа {group_id}:")
                    for _, row in group_members.iterrows():
                        print(
                            f"  - ID: {row['id']}, Name: {row['extracted_go_full_name']}, Desc: {row['description'][:50]}..."
                        )
        else:
            print("Дубликаты-кандидаты не найдены.")

        print("\n--- Сообщения из UI очереди (моделирование) ---")
        while not mock_ui_queue.empty():
            print(mock_ui_queue.get())

    asyncio.run(run_test())
    print("Демонстрация grouping_module.py завершена.")
