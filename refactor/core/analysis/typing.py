"""
Async business logic for the function typology analyzer (legacy ``2_types.py``).
The GUI layer is expected to configure callbacks for logging and progress updates
and to supply a cancel event when the user stops execution.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from ..file_io import read_table_auto, write_dataframe
from ..ai.client import prepare_api_base_url
from ..text_processing import normalize_whitespace

try:  # Optional dependency for embedding support
    import torch  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    torch = None

try:
    import openai  # type: ignore
except ImportError as exc:  # pragma: no cover - optional dependency
    openai = None  # type: ignore[assignment]
    raise RuntimeError(
        "Библиотека 'openai' не установлена. Установите ее командой: pip install openai"
    ) from exc

# ---------------------------------------------------------------------- defaults
DEFAULT_NEEDS_REVIEW_LABEL = "НЕОПРЕДЕЛЕНО"
ALL_CATEGORIES = ["Стратегические", "Регулятивные", "Реализационные", "Контрольные", "Общие"]

DEFAULT_EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-8B-GGUF"
DEFAULT_EMBEDDING_SERVER = "http://localhost:1234"
DEFAULT_EMBEDDING_BATCH_SIZE = 64

DEFAULT_ONLINE_MODEL = "gpt-4o-mini"
DEFAULT_ONLINE_CONCURRENT = 30
DEFAULT_ONLINE_RETRIES = 5
DEFAULT_ONLINE_TEMP = 1.0
DEFAULT_ONLINE_MAX_TOKENS = 15

DEFAULT_LOCAL_MODEL = "локальная-модель/имя-gguf"
DEFAULT_LOCAL_CONCURRENT = 4
DEFAULT_LOCAL_RETRIES = 3
DEFAULT_LOCAL_TEMP = 1.0
DEFAULT_LOCAL_MAX_TOKENS = 256

COL_ID = "ID"
COL_TEXT = "FunctionText"
COL_TYPE = "TrueType"
COL_LABELED_BY = "Labeled_by"
COL_AI1_REASON = "AI1_Reason"
COL_AI2_REASON = "AI2_Reason"
COL_AI3_REASON = "AI3_Reason"
COL_FINAL_LABEL = "final_label"

STATUS_HUMAN = "Human"
STATUS_AI_APPROVED = "AI_approved"
STATUS_AI_PENDING = "AI_pending"
STATUS_AI_FINALIZED = "AI_finalized"
TRUSTED_STATUSES = [STATUS_HUMAN, STATUS_AI_APPROVED, STATUS_AI_FINALIZED]

SYS_PROMPT_1_TEMPLATE = (
    "You are an expert in public administration. Your task is to classify a government function. "
    "In your analysis, always proceed from the 'action - subject - purpose' structure to understand the function's essence. "
    "Classify the function as one of the following types: "
    f"{', '.join(ALL_CATEGORIES)}. "
    "Strategic functions involve the development and adoption of planning documents, ensuring international relations, national security, and defense capability."
    "Regulatory functions involve the legal and regulatory support for the implementation of state functions, registration and analysis of the execution of legal acts, coordination of government bodies, and management of state assets."
    "Implementation functions are aimed at executing planning documents, legal acts, achieving goals and tasks stipulated by the planning documents of a state body, and providing public services, including issuing, renewing, reissuing, and other actions related to permits as provided by the legislation of the Republic of Kazakhstan."
    "Control functions involve checking and monitoring the activities of individuals and legal entities, including state institutions, for compliance with the requirements established by legal acts."
    "General functions cover the entire activity of a state body across several types but do not include details (e.g., strategic + control, control + regulatory). Example: carrying out strategic, regulatory, implementation, and control-supervisory functions within its competence."
    "{embedding_suggestions}"
)

SYS_PROMPT_1_REFINEMENT_TEMPLATE = (
    "You are an expert in public administration. Your task is to classify a government function. "
    "In your analysis, always proceed from the 'action - subject - purpose' structure to understand the function's essence. "
    "Classify the function as one of the following types: "
    f"{', '.join(ALL_CATEGORIES)}. "
    "Strategic functions involve the development and adoption of planning documents, ensuring international relations, national security, and defense capability."
    "Regulatory functions involve the legal and regulatory support for the implementation of state functions, registration and analysis of the execution of legal acts, coordination of government bodies, and management of state assets."
    "Implementation functions are aimed at executing planning documents, legal acts, achieving goals and tasks stipulated by the planning documents of a state body, and providing public services, including issuing, renewing, reissuing, and other actions related to permits as provided by the legislation of the Republic of Kazakhstan."
    "Control functions involve checking and monitoring the activities of individuals and legal entities, including state institutions, for compliance with the requirements established by legal acts."
    "General functions cover the entire activity of a state body across several types but do not include details (e.g., strategic + control, control + regulatory). Example: carrying out strategic, regulatory, implementation, and control-supervisory functions within its competence."
    "{embedding_suggestions}"
    "\nYour previous classification of this function was deemed incorrect. Please re-analyze it and provide a more accurate answer. "
)

SYS_PROMPT_2_TEMPLATE = (
    "You are an auditor. You are given a function and its proposed type. Your task is to verify if the type is correct. "
    "To make a decision, analyze the function using the 'action - subject - purpose' structure. "
    "The possible types are: "
    f"{', '.join(ALL_CATEGORIES)}. "
    "Strategic functions involve the development and adoption of planning documents, ensuring international relations, national security, and defense capability."
    "Regulatory functions involve the legal and regulatory support for the implementation of state functions, registration and analysis of the execution of legal acts, coordination of government bodies, and management of state assets."
    "Implementation functions are aimed at executing planning documents, legal acts, achieving goals and tasks stipulated by the planning documents of a state body, and providing public services, including issuing, renewing, reissuing, and other actions related to permits as provided by the legislation of the Republic of Kazakhstan."
    "Control functions involve checking and monitoring the activities of individuals and legal entities, including state institutions, for compliance with the requirements established by legal acts."
    "General functions cover the entire activity of a state body across several types but do not include details (e.g., strategic + control, control + regulatory). Example: carrying out strategic, regulatory, implementation, and control-supervisory functions within its competence."
    "A function can only be of ONE of the listed types. If it does not fit any category, determine which one is the closest match."
)

SYS_PROMPT_3_TEMPLATE = (
    "You are a chief expert-arbiter in public administration. You are provided with a function and the analysis history from two AI assistants. AI2 disagreed with AI1. "
    "Your task is to analyze all the data and make a FINAL decision on the classification. "
    "To do this, conduct your own analysis of the function based on the key 'action - subject - purpose' structure. "
    "The function types are: "
    f"{', '.join(ALL_CATEGORIES)}. "
    "Type criteria: "
    "Strategic - developing plans, concepts, international relations, national security. "
    "Regulatory - developing legal acts, rules, procedures, coordination, asset management. "
    "Implementation - providing public services, executing plans and legal acts, issuing permits. "
    "Control - checking, supervising, monitoring for compliance with requirements. "
    "General - covering multiple types without specifics."
)


# ---------------------------------------------------------------- configuration
Callback = Callable[[str, Any], None]
LogFunc = Callable[[str], None]


@dataclass
class TypologyConfig:
    gold_path: str
    input_path: str
    output_path: str
    ai_mode: str
    prompt1_initial: str
    prompt1_refinement: str
    prompt2: str
    prompt3: str
    use_embeddings: bool
    embed_server: str
    embed_model: str
    api_key: str = ""
    model: str = DEFAULT_ONLINE_MODEL
    concurrent_requests: int = DEFAULT_ONLINE_CONCURRENT
    max_retries: int = DEFAULT_ONLINE_RETRIES
    temperature: float = DEFAULT_ONLINE_TEMP
    max_tokens: int = DEFAULT_ONLINE_MAX_TOKENS
    local_servers: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.local_servers is None:
            self.local_servers = []


# ---------------------------------------------------------------- async globals
progress_callback_async: Optional[Callback] = None
stop_event_async: threading.Event = threading.Event()


def configure_async_environment(progress_callback: Callback, stop_event: threading.Event) -> None:
    global progress_callback_async, stop_event_async
    progress_callback_async = progress_callback
    stop_event_async = stop_event


# ------------------------------------------------------------------ io helpers
def read_any(path: str) -> pd.DataFrame:
    """Backward compatible reader for CSV/XLSX files using ``read_table_auto``."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Файл не найден: {path}")
    return read_table_auto(path)


# ---------------------------------------------------------------- AI helpers ---
async def _call_api_with_backoff(
    client: "openai.AsyncOpenAI",
    model: str,
    messages: List[dict],
    temperature: float,
    semaphore: asyncio.Semaphore,
    max_tokens: int,
) -> str:
    """Call the chat completion API with rudimentary error handling."""
    try:
        async with semaphore:
            if stop_event_async.is_set():
                return "ОСТАНОВЛЕНО"
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()
    except openai.APIStatusError as exc:  # pragma: no cover - network
        error_details = f"Код: {exc.status_code}. Ответ: {exc.response.text}"
        logging.error("Ошибка статуса API: %s", error_details)
        return f"ОШИБКА API: {error_details}"
    except openai.APIConnectionError as exc:  # pragma: no cover
        logging.error("Ошибка подключения к API: %s", exc.__cause__)
        return f"ОШИБКА ПОДКЛЮЧЕНИЯ: {exc.__cause__}"
    except openai.RateLimitError as exc:  # pragma: no cover
        logging.error("Превышен лимит запросов к API: %s", exc)
        return "ОШИБКА API: Превышен лимит запросов."
    except openai.AuthenticationError as exc:  # pragma: no cover
        logging.error("Ошибка аутентификации API: %s", exc)
        return "ОШИБКА API: Неверный ключ API."
    except Exception as exc:  # pragma: no cover
        logging.error("Неизвестная ошибка вызова API: %s", exc, exc_info=True)
        return f"ОШИБКА API: {exc}"


async def async_get_embeddings(
    client: "openai.AsyncOpenAI",
    model: str,
    texts: List[str],
    semaphore: asyncio.Semaphore,
) -> Optional[List[List[float]]]:
    """Request embeddings with cancellation support."""
    try:
        async with semaphore:
            if stop_event_async.is_set():
                return None
            resp = await client.embeddings.create(model=model, input=texts, timeout=120.0)
            return [item.embedding for item in resp.data]
    except Exception as exc:  # pragma: no cover - robustness
        print(f"Ошибка получения эмбеддингов: {exc}", flush=True)
        return None


def _parse_json_verdict(response_text: str) -> str:
    """Robustly extract a verdict field from model output."""
    try:
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            parsed_json = json.loads(json_match.group(0))
            if isinstance(parsed_json, dict):
                return normalize_whitespace(parsed_json.get("verdict", ""))
            logging.warning(
                "Ожидался JSON-объект, но получен другой тип: %s. Ответ: %s",
                type(parsed_json),
                response_text,
            )
    except (json.JSONDecodeError, AttributeError):
        return ""
    return ""


async def call_ai_1_for_classification(
    client: "openai.AsyncOpenAI",
    row: pd.Series,
    semaphore: asyncio.Semaphore,
    prompts: Dict[str, str],
    model: str,
    temperature: float,
    max_tokens: int,
    suggestions: Optional[List[str]] = None,
    previous_ai2_reason: str = "",
) -> Tuple[str, str, str]:
    """Primary classifier (AI-1)."""
    if stop_event_async.is_set():
        return row[COL_ID], "ОСТАНОВЛЕНО", ""
    text = row[COL_TEXT]
    prompt_template = prompts["refinement"] if previous_ai2_reason else prompts["initial"]
    suggestion_text = ""
    if suggestions:
        unique_suggestions = sorted(set(suggestions))
        suggestion_text = (
            "\n\nSIMILARITY ANALYSIS: Based on semantic proximity to verified examples, "
            f"the most likely types are: {', '.join(unique_suggestions)}. "
            "Please consider this information when making your verdict."
        )
    system_prompt = prompt_template.format(embedding_suggestions=suggestion_text)
    response_text = await _call_api_with_backoff(
        client,
        model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        semaphore=semaphore,
        max_tokens=max_tokens,
    )
    if progress_callback_async:
        progress_callback_async("ai1", 1)
    if response_text == "ОСТАНОВЛЕНО":
        return row[COL_ID], response_text, ""
    verdict = _parse_json_verdict(response_text).capitalize()
    if verdict not in ALL_CATEGORIES:
        verdict = DEFAULT_NEEDS_REVIEW_LABEL
    return row[COL_ID], verdict, ""


async def call_ai_2_for_verification(
    client: "openai.AsyncOpenAI",
    row: pd.Series,
    semaphore: asyncio.Semaphore,
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> Tuple[str, str, str]:
    """Verifier (AI-2)."""
    if stop_event_async.is_set():
        return row[COL_ID], "ОСТАНОВЛЕНО", ""
    text = row[COL_TEXT]
    proposed_type = row[COL_TYPE]
    prompt = f"Function:\n{text}\n\nProposed type: {proposed_type}"
    response_text = await _call_api_with_backoff(
        client,
        model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        semaphore=semaphore,
        max_tokens=max_tokens,
    )
    if progress_callback_async:
        progress_callback_async("ai2", 1)
    if response_text == "ОСТАНОВЛЕНО":
        return row[COL_ID], response_text, ""
    verdict_from_json = _parse_json_verdict(response_text)
    verdict = "ВЕРНО" if verdict_from_json == "ВЕРНО" else "НЕ_ВЕРНО"
    reason = "Классификация отклонена" if verdict == "НЕ_ВЕРНО" else ""
    return row[COL_ID], verdict, reason


async def call_ai_3_for_final_decision(
    client: "openai.AsyncOpenAI",
    row: pd.Series,
    semaphore: asyncio.Semaphore,
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> Tuple[str, str, str]:
    """Arbiter (AI-3)."""
    if stop_event_async.is_set():
        return row[COL_ID], "ОСТАНОВЛЕНО", ""
    prompt = (
        f"INITIAL FUNCTION: {row[COL_TEXT]}\n\n"
        f"---- ANALYSIS ----\n"
        f"AI1 proposed type: {row[COL_TYPE]}\n"
        f"AI2 verdict: DISAGREED\n\n"
        f"---- TASK ----\n"
        f"Analyze the situation and make the final decision."
    )
    response_text = await _call_api_with_backoff(
        client,
        model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        semaphore=semaphore,
        max_tokens=max_tokens,
    )
    if progress_callback_async:
        progress_callback_async("ai3", 1)
    if response_text == "ОСТАНОВЛЕНО":
        return row[COL_ID], response_text, ""
    final_type = _parse_json_verdict(response_text).capitalize()
    if final_type not in ALL_CATEGORIES:
        final_type = DEFAULT_NEEDS_REVIEW_LABEL
    return row[COL_ID], final_type, ""


async def process_unresolved_with_ai_loop(
    client_sem_pairs: List[Tuple["openai.AsyncOpenAI", asyncio.Semaphore]],
    df_to_process: pd.DataFrame,
    config: TypologyConfig,
    suggestions_map: Dict[Any, List[str]],
) -> pd.DataFrame:
    """Iteratively resolve rows using AI1/AI2/AI3 voting."""
    client_sem_cycle = itertools.cycle(client_sem_pairs)
    model_name = config.model
    max_tokens = config.max_tokens

    for col in [COL_TYPE, COL_LABELED_BY, COL_AI1_REASON, COL_AI2_REASON, COL_AI3_REASON, COL_FINAL_LABEL]:
        if col not in df_to_process.columns:
            df_to_process[col] = ""

    df_to_process[COL_TYPE] = DEFAULT_NEEDS_REVIEW_LABEL
    df_to_process[COL_LABELED_BY] = STATUS_AI_PENDING
    df_to_process[COL_FINAL_LABEL] = DEFAULT_NEEDS_REVIEW_LABEL

    resolved_functions_list: List[Dict[str, Any]] = []
    current_unresolved_df = df_to_process.copy()

    if current_unresolved_df.empty:
        return pd.DataFrame(columns=df_to_process.columns)

    for iteration in range(1, config.max_retries + 1):
        if stop_event_async.is_set():
            break
        if progress_callback_async:
            progress_callback_async(
                "status",
                f"Итерация ИИ №{iteration}/{config.max_retries}. Функций в работе: {len(current_unresolved_df)}",
            )
            progress_callback_async("reset", ("ai1", len(current_unresolved_df)))

        ai1_tasks = []
        for _, row in current_unresolved_df.iterrows():
            client, semaphore = next(client_sem_cycle)
            ai1_tasks.append(
                call_ai_1_for_classification(
                    client,
                    row,
                    semaphore,
                    prompts={"initial": config.prompt1_initial, "refinement": config.prompt1_refinement},
                    model=model_name,
                    temperature=config.temperature,
                    max_tokens=max_tokens,
                    suggestions=suggestions_map.get(row[COL_ID]),
                    previous_ai2_reason=row.get(COL_AI2_REASON, ""),
                )
            )
        ai1_results = await asyncio.gather(*ai1_tasks)
        if stop_event_async.is_set():
            break

        ai1_updates = {id_val: (new_type, reason) for id_val, new_type, reason in ai1_results}
        for idx, row in current_unresolved_df.iterrows():
            new_type, reason = ai1_updates.get(row[COL_ID], (DEFAULT_NEEDS_REVIEW_LABEL, ""))
            current_unresolved_df.loc[idx, COL_TYPE] = new_type
            current_unresolved_df.loc[idx, COL_AI1_REASON] = reason

        if progress_callback_async:
            progress_callback_async("reset", ("ai2", len(current_unresolved_df)))

        ai2_tasks = []
        for _, row in current_unresolved_df.iterrows():
            client, semaphore = next(client_sem_cycle)
            ai2_tasks.append(
                call_ai_2_for_verification(
                    client,
                    row,
                    semaphore,
                    config.prompt2,
                    model_name,
                    config.temperature,
                    max_tokens,
                )
            )
        ai2_results = await asyncio.gather(*ai2_tasks)
        if stop_event_async.is_set():
            break

        next_iteration_unresolved_list: List[Dict[str, Any]] = []
        for id_val, verdict, reason in ai2_results:
            row_idx_series = current_unresolved_df[current_unresolved_df[COL_ID] == id_val].index
            if row_idx_series.empty:
                continue
            row_idx = row_idx_series[0]
            current_unresolved_df.loc[row_idx, COL_AI2_REASON] = reason
            if verdict == "ВЕРНО":
                current_unresolved_df.loc[row_idx, COL_LABELED_BY] = STATUS_AI_APPROVED
                current_unresolved_df.loc[row_idx, COL_FINAL_LABEL] = current_unresolved_df.loc[row_idx, COL_TYPE]
                resolved_functions_list.append(current_unresolved_df.loc[row_idx].to_dict())
            else:
                current_unresolved_df.loc[row_idx, COL_LABELED_BY] = STATUS_AI_PENDING
                current_unresolved_df.loc[row_idx, COL_FINAL_LABEL] = DEFAULT_NEEDS_REVIEW_LABEL
                next_iteration_unresolved_list.append(current_unresolved_df.loc[row_idx].to_dict())

        current_unresolved_df = pd.DataFrame(next_iteration_unresolved_list) if next_iteration_unresolved_list else pd.DataFrame()

    if not stop_event_async.is_set() and not current_unresolved_df.empty:
        if progress_callback_async:
            progress_callback_async("status", f"Запуск ИИ3 (арбитра) для {len(current_unresolved_df)} функций...")
            progress_callback_async("reset", ("ai3", len(current_unresolved_df)))

        ai3_tasks = []
        for _, row in current_unresolved_df.iterrows():
            client, semaphore = next(client_sem_cycle)
            ai3_tasks.append(
                call_ai_3_for_final_decision(
                    client,
                    row,
                    semaphore,
                    config.prompt3,
                    model_name,
                    config.temperature,
                    max_tokens,
                )
            )
        ai3_results = await asyncio.gather(*ai3_tasks)
        if not stop_event_async.is_set():
            for id_val, final_type, final_reason in ai3_results:
                row_idx_series = current_unresolved_df[current_unresolved_df[COL_ID] == id_val].index
                if row_idx_series.empty:
                    continue
                row_idx = row_idx_series[0]
                current_unresolved_df.loc[row_idx, COL_TYPE] = final_type
                current_unresolved_df.loc[row_idx, COL_AI3_REASON] = final_reason
                current_unresolved_df.loc[row_idx, COL_LABELED_BY] = STATUS_AI_FINALIZED
                current_unresolved_df.loc[row_idx, COL_FINAL_LABEL] = final_type
            resolved_functions_list.extend(current_unresolved_df.to_dict("records"))

    return pd.DataFrame(resolved_functions_list) if resolved_functions_list else pd.DataFrame(columns=df_to_process.columns)


# ---------------------------------------------------------------- main routine
async def _analysis_logic(
    config: TypologyConfig,
    progress_callback: Callback,
    stop_event: threading.Event,
    log: LogFunc,
) -> None:
    configure_async_environment(progress_callback, stop_event)
    clients: List["openai.AsyncOpenAI"] = []
    client_sem_pairs: List[Tuple["openai.AsyncOpenAI", asyncio.Semaphore]] = []
    try:
        json_instruction_classifier = (
            "\nNow, analyze the following function. Respond strictly in a JSON format like {{\"verdict\": \"YOUR_VERDICT\"}}. "
            "The verdict value MUST be in Russian. For example: {{\"verdict\": \"Регулятивные\"}}."
        )
        json_instruction_auditor = (
            "\nReview the proposed classification. Respond strictly in a JSON format: {{\"verdict\": \"ВЕРНО\"}} or {{\"verdict\": \"НЕ_ВЕРНО\"}}. "
            "The verdict value MUST be in Russian."
        )
        json_instruction_arbiter = (
            "\nAnalyze the disputed situation. Respond strictly in a JSON format like {{\"verdict\": \"YOUR_VERDICT\"}}. "
            "The verdict value MUST be in Russian. For example: {{\"verdict\": \"Реализационные\"}}."
        )

        config.prompt1_initial += json_instruction_classifier
        config.prompt1_refinement += json_instruction_classifier.replace("Now, analyze the following function.", "")
        config.prompt2 += json_instruction_auditor
        config.prompt3 += json_instruction_arbiter

        if config.ai_mode == "Онлайн":
            client = openai.AsyncOpenAI(api_key=config.api_key)
            clients.append(client)
            semaphore = asyncio.Semaphore(config.concurrent_requests)
            client_sem_pairs.append((client, semaphore))
            log(f"Режим ИИ: Онлайн. Модель: {config.model}")
        else:
            num_servers = len(config.local_servers)
            concurrency_per_server = max(1, config.concurrent_requests // max(1, num_servers))
            log(f"Режим ИИ: Локальный. Серверов: {num_servers}. Запросов на сервер: {concurrency_per_server}")
            for url in config.local_servers:
                client = openai.AsyncOpenAI(base_url=prepare_api_base_url(url), api_key="not-needed")
                clients.append(client)
                semaphore = asyncio.Semaphore(concurrency_per_server)
                client_sem_pairs.append((client, semaphore))
        if not clients:
            raise ValueError("Не удалось создать ни одного клиента ИИ.")

        df_gold = read_any(config.gold_path)
        df_gold.dropna(subset=[COL_TEXT, COL_TYPE], inplace=True)
        df_gold_trusted = df_gold[df_gold[COL_LABELED_BY].isin(TRUSTED_STATUSES)].copy()
        if len(df_gold_trusted) < 2:
            raise ValueError("Недостаточно доверенных данных в золотом стандарте (нужно хотя бы 2).")

        log("Few-shot-примеры отключены.")

        df_inp = read_any(config.input_path)
        if COL_TEXT not in df_inp.columns:
            raise ValueError(f"В файле для обработки отсутствует обязательная колонка '{COL_TEXT}'.")

        id_was_added = False
        if COL_ID not in df_inp.columns:
            log(f"Колонка '{COL_ID}' не найдена. Создаю ее на основе индекса строк.")
            df_inp.reset_index(inplace=True)
            df_inp.rename(columns={"index": COL_ID}, inplace=True)
            id_was_added = True

        df_inp.dropna(subset=[COL_TEXT], inplace=True)

        df_already_processed = pd.DataFrame()
        df_to_process_ai = pd.DataFrame()

        if "Type" in df_inp.columns:
            df_inp["Type"] = df_inp["Type"].apply(lambda x: str(x).strip())
            df_already_processed = df_inp[df_inp["Type"].astype(bool)].copy()
            df_to_process_ai = df_inp[~df_inp["Type"].astype(bool)].copy()
        else:
            df_to_process_ai = df_inp.copy()

        suggestions_map: Dict[Any, List[str]] = {}

        if config.use_embeddings:
            if torch is None:
                raise RuntimeError("Библиотека 'torch' не найдена. Установите ее для поддержки эмбеддингов.")
            if not config.embed_server:
                raise ValueError("Если включена помощь эмбеддингов, необходимо указать адрес сервера.")

            embed_client = openai.AsyncOpenAI(base_url=prepare_api_base_url(config.embed_server), api_key="not-needed")
            embed_semaphore = asyncio.Semaphore(config.concurrent_requests)
            try:
                if progress_callback_async:
                    progress_callback_async("reset", ("embed", len(df_gold_trusted)))
                gold_texts = df_gold_trusted[COL_TEXT].tolist()
                gold_embeddings: List[List[float]] = []
                batch_size = DEFAULT_EMBEDDING_BATCH_SIZE
                for i in range(0, len(gold_texts), batch_size):
                    batch = gold_texts[i : i + batch_size]
                    result = await async_get_embeddings(embed_client, config.embed_model, batch, embed_semaphore)
                    if result:
                        gold_embeddings.extend(result)
                    if progress_callback_async:
                        progress_callback_async("embed", len(batch))
                if not gold_embeddings:
                    log("ПРЕДУПРЕЖДЕНИЕ: Не удалось получить эмбеддинги для золотого стандарта. Продолжаю без подсказок.")
                else:
                    log(f"Успешно получено {len(gold_embeddings)} эмбеддингов для золотого стандарта.")
                    gold_embeddings_tensor = torch.nn.functional.normalize(
                        torch.tensor(gold_embeddings, dtype=torch.float32), p=2, dim=1
                    )
                    gold_labels = df_gold_trusted[COL_TYPE].tolist()

                    if progress_callback_async:
                        input_count = len(df_to_process_ai)
                        progress_callback_async("reset", ("embed", input_count))

                    input_texts = df_to_process_ai[COL_TEXT].tolist()
                    input_ids = df_to_process_ai[COL_ID].tolist()
                    input_embeddings: List[List[float]] = []
                    for i in range(0, len(input_texts), batch_size):
                        batch = input_texts[i : i + batch_size]
                        result = await async_get_embeddings(embed_client, config.embed_model, batch, embed_semaphore)
                        if result:
                            input_embeddings.extend(result)
                        if progress_callback_async:
                            progress_callback_async("embed", len(batch))

                    if not input_embeddings:
                        log("ПРЕДУПРЕЖДЕНИЕ: Не удалось получить эмбеддинги для входного файла. Продолжаю без подсказок.")
                    else:
                        log(f"Успешно получено {len(input_embeddings)} эмбеддингов для входа.")
                        input_embeddings_tensor = torch.nn.functional.normalize(
                            torch.tensor(input_embeddings, dtype=torch.float32), p=2, dim=1
                        )
                        sim_matrix = input_embeddings_tensor @ gold_embeddings_tensor.T
                        _, top_k_indices = torch.topk(sim_matrix, k=2, dim=1)
                        for idx, top_indices in enumerate(top_k_indices.tolist()):
                            suggestions_map[input_ids[idx]] = [gold_labels[i] for i in top_indices]
                        log("Карта подсказок на основе семантической близости создана.")
            finally:
                await embed_client.close()

        log(f"Найдено {len(df_to_process_ai)} функций для обработки ИИ. Запуск анализа...")
        processed_by_ai_df = await process_unresolved_with_ai_loop(client_sem_pairs, df_to_process_ai, config, suggestions_map)

        if stop_event.is_set():
            log("Анализ остановлен. Результаты могут быть неполными.")
            return

        log("Формирование итогового файла...")
        final_df_list: List[pd.DataFrame] = []
        if not df_already_processed.empty:
            final_df_list.append(df_already_processed)
        if not processed_by_ai_df.empty:
            id_to_results = processed_by_ai_df.set_index(COL_ID).to_dict("index")
            df_newly_processed = df_to_process_ai.copy()
            df_newly_processed["Type"] = df_newly_processed[COL_ID].map(
                lambda x: id_to_results.get(x, {}).get(COL_FINAL_LABEL)
            )
            final_df_list.append(df_newly_processed)
        if not final_df_list:
            log("Нет данных для сохранения.")
            return
        final_result_df = pd.concat(final_df_list, ignore_index=True)
        if COL_ID in df_inp.columns and COL_ID in final_result_df.columns:
            final_result_df = final_result_df.set_index(COL_ID).loc[df_inp[COL_ID]].reset_index()
        if id_was_added and COL_ID in final_result_df.columns:
            final_result_df = final_result_df.drop(columns=[COL_ID])
        log(f"Сохранение итогового файла в: {config.output_path}")
        write_dataframe(final_result_df, config.output_path, index=False)
        log("Анализ успешно завершен!")
    except Exception as exc:
        log(f"Ошибка в процессе анализа: {exc}")
        if progress_callback_async:
            progress_callback_async("status", f"Ошибка: {exc}")
    finally:
        for client in clients:
            await client.close()


def run_typology_analysis(
    config: TypologyConfig,
    progress_callback: Callback,
    stop_event: threading.Event,
    log: LogFunc,
) -> None:
    """
    Public entry point that spins up a temporary asyncio loop to execute the
    asynchronous pipeline.
    """
    if openai is None:  # pragma: no cover - guarded earlier
        raise RuntimeError("OpenAI dependency is required for typology analysis.")
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_analysis_logic(config, progress_callback, stop_event, log))
    finally:
        loop.close()


__all__ = [
    "TypologyConfig",
    "run_typology_analysis",
    "DEFAULT_NEEDS_REVIEW_LABEL",
    "ALL_CATEGORIES",
    "DEFAULT_EMBEDDING_MODEL_NAME",
    "DEFAULT_EMBEDDING_SERVER",
    "DEFAULT_EMBEDDING_BATCH_SIZE",
    "DEFAULT_ONLINE_MODEL",
    "DEFAULT_ONLINE_CONCURRENT",
    "DEFAULT_ONLINE_RETRIES",
    "DEFAULT_ONLINE_TEMP",
    "DEFAULT_ONLINE_MAX_TOKENS",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_LOCAL_CONCURRENT",
    "DEFAULT_LOCAL_RETRIES",
    "DEFAULT_LOCAL_TEMP",
    "DEFAULT_LOCAL_MAX_TOKENS",
    "COL_ID",
    "COL_TEXT",
    "COL_TYPE",
    "COL_LABELED_BY",
    "COL_AI1_REASON",
    "COL_AI2_REASON",
    "COL_AI3_REASON",
    "COL_FINAL_LABEL",
    "STATUS_HUMAN",
    "STATUS_AI_APPROVED",
    "STATUS_AI_PENDING",
    "STATUS_AI_FINALIZED",
    "SYS_PROMPT_1_TEMPLATE",
    "SYS_PROMPT_1_REFINEMENT_TEMPLATE",
    "SYS_PROMPT_2_TEMPLATE",
    "SYS_PROMPT_3_TEMPLATE",
]
