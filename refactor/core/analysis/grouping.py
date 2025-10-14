"""
Collision analysis logic extracted from the legacy ``4_group+dub.py`` script.
This module is GUI agnostic; it exposes a dataclass-driven configuration and a
single `run_collision_analysis` entry point that coordinates embedding
generation, candidate discovery, optional AI verification, and report export.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import torch

try:
    from openai import (
        OpenAI,
        AuthenticationError,
        APIConnectionError,
        APIStatusError,
        RateLimitError,
    )
except ImportError as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "Библиотека 'openai' не установлена. Установите ее командой: pip install openai"
    ) from exc

from ..ai.client import prepare_api_base_url
from ..file_io import read_table_auto, write_dataframe

TEXT_COLUMN = "FunctionText"
EXECUTOR_COLUMN = "Исполняющий ГО"
ID_COLUMN = "ID"

DEFAULT_GROUPING_COLUMNS = ["Type", "Sphere_3"]
DEFAULT_SIMILARITY_THRESHOLD = 0.50
DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD = 0.75
DEFAULT_EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-8B-GGUF"
DEFAULT_CHAT_MODEL_NAME = "gpt-4o-mini"
DEFAULT_EMBEDDING_WORKERS = 8
DEFAULT_BATCH_SIZE = 64
DEFAULT_AI_WORKERS = 30
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 150
WORKERS_PER_LOCAL_SERVER = 4

DEFAULT_AI_SYSTEM_PROMPT = (
    "You are a highly qualified AI analyst specializing in identifying duplication in government functions. "
    "Your task is to perform a deep semantic and contextual analysis of two functions.\n\n"
    "**Context is crucial:** Always consider which agencies (Agency 1, Agency 2) are performing these functions. "
    "Sometimes, functions with similar wording have different meanings in the context of different agencies.\n\n"
    "**What to consider a real collision (verdict: CORRECT):**\n"
    "* The functions are completely identical in their essence, meaning, and final outcome. They describe the same action aimed at the same object.\n"
    "* The wording might differ slightly (use of synonyms, different word order), but the semantic core and the ultimate goal are the same.\n\n"
    "**What is NOT a collision (verdict: NOT_CORRECT):**\n"
    "1.  Different Actions: The functions describe fundamentally different tasks.\n"
    "2.  Different Process Stages: One function describes planning while the other describes execution.\n"
    "3.  Different Objects/Subjects.\n"
    "4.  General vs. Specific functions.\n\n"
    'Respond only with JSON: {"verdict": "CORRECT"} or {"verdict": "NOT_CORRECT"}.'
)


Callback = Callable[[str, Any], None]
LogFunc = Callable[[str], None]


@dataclass
class CollisionConfig:
    input_file: str
    output_dir: str
    grouping_cols: List[str]
    univ_json_path: str
    sim_thr: float = DEFAULT_SIMILARITY_THRESHOLD
    univ_thr: float = DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD
    embedding_workers: int = DEFAULT_EMBEDDING_WORKERS
    batch_size: int = DEFAULT_BATCH_SIZE
    embed_server_url: str = ""
    embed_model: str = DEFAULT_EMBEDDING_MODEL_NAME
    ai_should_verify: bool = True
    verification_mode: str = "Онлайн"  # or "Локальная"
    ai_system_prompt: str = DEFAULT_AI_SYSTEM_PROMPT
    ai_temperature: float = DEFAULT_TEMPERATURE
    ai_max_tokens: int = DEFAULT_MAX_TOKENS
    use_json_mode: bool = True
    openai_api_key: str = ""
    ai_chat_model: str = DEFAULT_CHAT_MODEL_NAME
    ai_workers: int = DEFAULT_AI_WORKERS
    local_servers: List[str] = field(default_factory=list)
    local_chat_model: str = DEFAULT_CHAT_MODEL_NAME


@dataclass
class CollisionAnalysisResult:
    output_path: str
    candidate_pairs: int
    confirmed_pairs: int


def _cosine_matrix(x: torch.Tensor) -> torch.Tensor:
    x = x.to(dtype=torch.float32)
    x = torch.nn.functional.normalize(x, p=2, dim=1)
    return x @ x.T


def _get_embeddings_from_server(
    client: OpenAI,
    model: str,
    texts: List[str],
    retries: int = 3,
    delay: float = 1.0,
) -> Optional[List[List[float]]]:
    for attempt in range(retries):
        try:
            resp = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in resp.data]
        except Exception:  # pragma: no cover - network variability
            time.sleep(delay)
            delay *= 2
    return None


def _load_universal_texts(path: str, log: LogFunc) -> List[str]:
    if not (path and os.path.isfile(path)):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception as exc:
        log(f"Не удалось прочитать JSON универсальных функций: {exc}")
        return []

    unique: Set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if (func := node.get("function")) and isinstance(func, str) and func.strip():
                unique.add(func.strip())
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(data)
    return sorted(unique)


def _calculate_embeddings_for_df(
    df: pd.DataFrame,
    client: OpenAI,
    config: CollisionConfig,
    stop_event: threading.Event,
) -> Dict[int, List[float]]:
    texts = df[TEXT_COLUMN].fillna("").astype(str)
    valid_rows = [(idx, text) for idx, text in texts.items() if text.strip()]
    if not valid_rows:
        return {}

    batches = [
        valid_rows[i : i + config.batch_size]
        for i in range(0, len(valid_rows), config.batch_size)
    ]
    task_queue: "queue.Queue[List[Tuple[int, str]]]" = queue.Queue()
    for batch in batches:
        task_queue.put(batch)

    embeddings: Dict[int, List[float]] = {}
    lock = threading.Lock()

    def worker() -> None:
        while not task_queue.empty() and not stop_event.is_set():
            try:
                batch_items = task_queue.get_nowait()
            except queue.Empty:
                break
            indices = [item[0] for item in batch_items]
            payloads = [item[1] for item in batch_items]
            results = _get_embeddings_from_server(
                client, config.embed_model, payloads
            )
            if results and len(results) == len(indices):
                with lock:
                    for idx, embedding in zip(indices, results):
                        embeddings[idx] = embedding
            task_queue.task_done()

    threads = []
    for _ in range(max(1, config.embedding_workers)):
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return embeddings


def _parse_verdict(content: str) -> Optional[str]:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            verdict = data.get("verdict")
            if verdict in {"CORRECT", "NOT_CORRECT"}:
                return verdict
        except json.JSONDecodeError:
            return None
    if "CORRECT" in content:
        return "CORRECT"
    if "NOT_CORRECT" in content:
        return "NOT_CORRECT"
    return None


def _get_llm_verdict(
    client: OpenAI,
    model: str,
    system_prompt: str,
    go1: str,
    func1: str,
    go2: str,
    func2: str,
    use_json_mode: bool,
    temperature: float,
    verification_mode: str,
    max_completion_tokens: int,
) -> Tuple[str, Optional[str]]:
    user_prompt = (
        f"Agency 1: {go1}\nFunction 1: {func1}\n\n"
        f"Agency 2: {go2}\nFunction 2: {func2}\n"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    request_params: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
    }
    if use_json_mode and verification_mode == "Онлайн":
        request_params["response_format"] = {"type": "json_object"}

    last_error = "Unknown error after retries"
    for _ in range(3):
        try:
            response = client.chat.completions.create(**request_params)
            verdict = _parse_verdict(response.choices[0].message.content)
            if verdict in {"CORRECT", "NOT_CORRECT"}:
                return verdict, None
            last_error = "Не удалось распарсить ответ модели."
        except AuthenticationError as exc:
            return "AUTH_ERROR", str(exc)
        except APIStatusError as exc:
            return "ERROR", f"API Error {exc.status_code}: {exc.response.text}"
        except (APIConnectionError, RateLimitError) as exc:
            last_error = str(exc)
            time.sleep(1)
        except Exception as exc:  # pragma: no cover - robustness
            last_error = str(exc)
    return "ERROR", last_error


def _verification_worker(
    task_queue: "queue.Queue[Tuple[str, str]]",
    confirmed_pairs: Set[Tuple[str, str]],
    lock: threading.Lock,
    df_indexed: pd.DataFrame,
    client: OpenAI,
    model_name: str,
    config: CollisionConfig,
    stop_event: threading.Event,
    notify: Callback,
    log: LogFunc,
) -> None:
    while not task_queue.empty() and not stop_event.is_set():
        try:
            id1, id2 = task_queue.get_nowait()
        except queue.Empty:
            break
        try:
            row1, row2 = df_indexed.loc[id1], df_indexed.loc[id2]
        except KeyError as exc:
            log(f"ОШИБКА: ID {exc} не найден. Пропускаю пару ({id1}, {id2}).")
            notify("verification", 1)
            continue

        verdict, message = _get_llm_verdict(
            client,
            model_name,
            config.ai_system_prompt,
            row1.get(EXECUTOR_COLUMN, ""),
            row1.get(TEXT_COLUMN, ""),
            row2.get(EXECUTOR_COLUMN, ""),
            row2.get(TEXT_COLUMN, ""),
            config.use_json_mode,
            config.ai_temperature,
            config.verification_mode,
            config.ai_max_tokens,
        )
        if verdict == "CORRECT":
            with lock:
                confirmed_pairs.add(tuple(sorted((id1, id2))))
        elif verdict == "AUTH_ERROR":
            log(f"Ошибка аутентификации при проверке {id1}-{id2}: {message}")
            task_queue.put((id1, id2))
            break
        elif verdict == "ERROR" and message:
            log(f"Ошибка API (пара {id1}, {id2}): {message}")
        notify("verification", 1)
        task_queue.task_done()


def _find_candidate_pairs(
    df: pd.DataFrame,
    file_embeddings: Dict[int, List[float]],
    universal_embeddings: List[List[float]],
    sim_thr: float,
    univ_thr: float,
) -> Set[Tuple[str, str]]:
    if not file_embeddings:
        return set()

    idx_keep = sorted(file_embeddings.keys())
    emb_keep = torch.tensor([file_embeddings[i] for i in idx_keep], dtype=torch.float32)

    if universal_embeddings:
        univ_embeds_tensor = torch.tensor(universal_embeddings, dtype=torch.float32)
        sims = (
            torch.nn.functional.normalize(emb_keep)
            @ torch.nn.functional.normalize(univ_embeds_tensor).T
        )
        universal_mask_keep = sims.max(dim=1).values >= univ_thr
    else:
        universal_mask_keep = torch.zeros(len(idx_keep), dtype=torch.bool)

    non_universal_indices_local = torch.where(~universal_mask_keep)[0].tolist()
    to_analyze_global_idx = [idx_keep[i] for i in non_universal_indices_local]
    candidate_pairs: Set[Tuple[str, str]] = set()

    if len(to_analyze_global_idx) > 1:
        df_analyze = df.loc[to_analyze_global_idx]
        emb_analyze = emb_keep[non_universal_indices_local]
        cos_scores = _cosine_matrix(emb_analyze)

        exec_series = df_analyze[EXECUTOR_COLUMN].astype(str).values
        diff_exec_mask = torch.from_numpy(~np.equal.outer(exec_series, exec_series))

        edges_mask = (
            (cos_scores >= sim_thr)
            & diff_exec_mask
            & torch.triu(torch.ones_like(cos_scores), 1).bool()
        )
        edge_i, edge_j = torch.nonzero(edges_mask, as_tuple=True)

        ids_analyze = df_analyze[ID_COLUMN].astype(str).values
        for a, b in zip(edge_i.tolist(), edge_j.tolist()):
            pair = tuple(sorted((ids_analyze[a], ids_analyze[b])))
            candidate_pairs.add(pair)
    return candidate_pairs


def run_collision_analysis(
    config: CollisionConfig,
    progress_callback: Optional[Callback],
    stop_event: threading.Event,
    log: LogFunc,
) -> CollisionAnalysisResult:
    def emit(event: str, payload: Any) -> None:
        if progress_callback:
            progress_callback(event, payload)

    os.makedirs(config.output_dir, exist_ok=True)

    embed_server_url = prepare_api_base_url(config.embed_server_url)
    if not embed_server_url:
        raise ValueError("Не указан адрес сервера эмбеддингов.")
    embed_client = OpenAI(base_url=embed_server_url, api_key="not-needed")

    universal_texts = _load_universal_texts(config.univ_json_path, log)
    universal_embeddings: List[List[float]] = []
    if universal_texts:
        log(f"Векторизация {len(universal_texts)} универсальных функций...")
        for i in range(0, len(universal_texts), config.batch_size):
            if stop_event.is_set():
                raise InterruptedError()
            batch = universal_texts[i : i + config.batch_size]
            embeds = _get_embeddings_from_server(embed_client, config.embed_model, batch)
            if embeds:
                universal_embeddings.extend(embeds)

    log(f"Чтение исходного файла: {os.path.basename(config.input_file)}")
    source_df = read_table_auto(config.input_file)
    if ID_COLUMN not in source_df.columns:
        source_df = source_df.reset_index().rename(columns={"index": ID_COLUMN})
    source_df[ID_COLUMN] = source_df[ID_COLUMN].astype(str)

    missing_group_cols = [col for col in config.grouping_cols if col not in source_df.columns]
    if missing_group_cols:
        raise ValueError(
            f"В файле отсутствуют столбцы для группировки: {', '.join(missing_group_cols)}"
        )

    grouped_data = source_df.groupby(config.grouping_cols)
    total_groups = grouped_data.ngroups
    log(f"Данные сгруппированы. Найдено {total_groups} уникальных групп для анализа.")

    emit("reset", ("discovery", total_groups))
    all_candidate_pairs: Set[Tuple[str, str]] = set()

    for i, (group_name, group_df) in enumerate(grouped_data, start=1):
        if stop_event.is_set():
            raise InterruptedError()
        display_group_name = (
            ", ".join(
                f"{col}: {val}" for col, val in zip(config.grouping_cols, group_name)
            )
            if isinstance(group_name, tuple)
            else str(group_name)
        )
        emit("status", f"Группа {i}/{total_groups}: {display_group_name}")

        group_df_for_analysis = group_df.copy()
        if "Type" in group_df_for_analysis.columns:
            group_df_for_analysis = group_df_for_analysis[
                group_df_for_analysis["Type"] != "Общие функции"
            ]

        if len(group_df_for_analysis) < 2:
            log(
                f"Группа '{group_name}' ({len(group_df)} строк) пропущена — недостаточно данных."
            )
            emit("discovery", 1)
            continue

        file_embeddings = _calculate_embeddings_for_df(
            group_df_for_analysis, embed_client, config, stop_event
        )
        if stop_event.is_set():
            raise InterruptedError()

        candidate_pairs = _find_candidate_pairs(
            group_df_for_analysis,
            file_embeddings,
            universal_embeddings,
            config.sim_thr,
            config.univ_thr,
        )

        if candidate_pairs:
            log(f"В группе '{group_name}' найдено кандидатов: {len(candidate_pairs)}")
            all_candidate_pairs.update(candidate_pairs)
            emit("update_candidates_count", len(all_candidate_pairs))
        emit("discovery", 1)

    log(f"Поиск завершен. Всего найдено уникальных кандидатов: {len(all_candidate_pairs)}")
    emit("update_candidates_count", len(all_candidate_pairs))

    confirmed_pairs: Set[Tuple[str, str]] = set()
    if config.ai_should_verify and all_candidate_pairs:
        log(f"ФАЗА 2: Запуск AI-верификации в режиме '{config.verification_mode}'...")
        llm_clients: List[OpenAI] = []
        try:
            if config.verification_mode == "Онлайн":
                if not config.openai_api_key:
                    raise ValueError("Для онлайн-верификации требуется API ключ OpenAI.")
                llm_clients.append(OpenAI(api_key=config.openai_api_key))
            else:
                for url in config.local_servers:
                    llm_clients.append(
                        OpenAI(base_url=prepare_api_base_url(url), api_key="not-needed")
                    )
            if not llm_clients:
                raise ValueError("Не удалось создать AI-клиентов.")
        except Exception as exc:
            log(f"Ошибка инициализации AI-клиента: {exc}. Верификация будет пропущена.")
            confirmed_pairs = all_candidate_pairs
        else:
            task_queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
            for pair in all_candidate_pairs:
                task_queue.put(pair)

            df_indexed = source_df.set_index(ID_COLUMN)
            total_tasks = len(all_candidate_pairs)
            emit("reset", ("verification", total_tasks))
            confirmed_lock = threading.Lock()
            threads: List[threading.Thread] = []

            if config.verification_mode == "Онлайн":
                model_name = config.ai_chat_model
                client = llm_clients[0]
                for _ in range(max(1, config.ai_workers)):
                    thread = threading.Thread(
                        target=_verification_worker,
                        args=(
                            task_queue,
                            confirmed_pairs,
                            confirmed_lock,
                            df_indexed,
                            client,
                            model_name,
                            config,
                            stop_event,
                            emit,
                            log,
                        ),
                        daemon=True,
                    )
                    thread.start()
                    threads.append(thread)
            else:
                model_name = config.local_chat_model
                for client in llm_clients:
                    for _ in range(WORKERS_PER_LOCAL_SERVER):
                        thread = threading.Thread(
                            target=_verification_worker,
                            args=(
                                task_queue,
                                confirmed_pairs,
                                confirmed_lock,
                                df_indexed,
                                client,
                                model_name,
                                config,
                                stop_event,
                                emit,
                                log,
                            ),
                            daemon=True,
                        )
                        thread.start()
                        threads.append(thread)

            for thread in threads:
                thread.join()
            log(f"AI-верификация завершена. Подтверждено коллизий: {len(confirmed_pairs)}")
    else:
        confirmed_pairs = all_candidate_pairs
        if not all_candidate_pairs:
            log("AI-верификация пропущена (нет кандидатов).")

    if stop_event.is_set():
        raise InterruptedError()

    log("ФАЗА 3: Формирование и сохранение итогового отчета...")
    emit("status", "Формирование отчета...")

    final_df = source_df.copy()
    confirmed_dict: Dict[str, Set[str]] = defaultdict(set)
    for id1, id2 in confirmed_pairs:
        confirmed_dict[id1].add(id2)
        confirmed_dict[id2].add(id1)

    if "Anomaly" not in final_df.columns:
        final_df["Anomaly"] = ""
    if "CollisionWith" not in final_df.columns:
        final_df["CollisionWith"] = ""

    for idx, row in final_df.iterrows():
        row_id = str(row[ID_COLUMN])
        if row_id in confirmed_dict:
            final_df.at[idx, "Anomaly"] = "Collision"
            final_df.at[idx, "CollisionWith"] = "; ".join(sorted(confirmed_dict[row_id]))
        else:
            final_df.at[idx, "Anomaly"] = ""
            final_df.at[idx, "CollisionWith"] = ""

    output_fname = f"Сводный_отчет_по_коллизиям_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    output_path = os.path.join(config.output_dir, output_fname)
    write_dataframe(final_df, output_path, index=False)
    log(f"Сохранен сводный отчет: {output_path}")

    return CollisionAnalysisResult(
        output_path=output_path,
        candidate_pairs=len(all_candidate_pairs),
        confirmed_pairs=len(confirmed_pairs),
    )


__all__ = [
    "CollisionConfig",
    "CollisionAnalysisResult",
    "run_collision_analysis",
    "DEFAULT_GROUPING_COLUMNS",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD",
    "DEFAULT_EMBEDDING_MODEL_NAME",
    "DEFAULT_CHAT_MODEL_NAME",
    "DEFAULT_AI_SYSTEM_PROMPT",
]

