"""
Business logic for the sphere classification tool (legacy ``3_spheres.py``).
This module encapsulates file loading, embedding preparation, LLM classification,
and hierarchy enrichment while remaining GUI agnostic.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..ai.client import prepare_api_base_url
from ..file_io import read_table_auto, write_dataframe
from ..text_processing import normalize_whitespace

try:  # Optional dependency required by the original scripts
    import openai  # type: ignore
except ImportError as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "Библиотека 'openai' не установлена. Установите ее командой: pip install openai"
    ) from exc

# --------------------------------------------------------------------------- data
SYSTEM_PROMPT_TEMPLATE = (
    "You are an expert in classifying the functions of government bodies. Your task is to select the ONE most suitable sphere from the provided list for the given function.\n\n"
    "CRITICAL RULES:\n"
    "1. To understand the function's essence, always analyze it based on the 'action - subject - purpose' structure. Consider the role of the government body and do not rely solely on keywords.\n"
    '2. If no sphere is a direct and logical match, you MUST return "NO_MATCH".\n'
    '3. Otherwise, return the answer as a SINGLE JSON line without any explanations. The value for the "name" key MUST be in Russian, taken directly from the provided list of spheres.\n\n'
    "RESPONSE EXAMPLES:\n"
    '- Match found: {"name":"02.1 ВООРУЖЕННЫЕ СИЛЫ"}\n'
    '- No match found: {"name":"NO_MATCH"}'
)

SPHERES_MAP: Dict[str, str] = {
    "01": "ГОСУДАРСТВЕННЫЕ СЛУЖБЫ ОБЩЕГО НАЗНАЧЕНИЯ",
    "01.1": "ИСПОЛНИТЕЛЬНЫЕ И ЗАКОНОДАТЕЛЬНЫЕ ОРГАНЫ, БЮДЖЕТНО-ФИНАНСОВЫЕ ВОПРОСЫ, МЕЖДУНАРОДНЫЕ ОТНОШЕНИЯ",
    "01.2": "ИНОСТРАННАЯ ЭКОНОМИЧЕСКАЯ ПОМОЩЬ",
    "01.3": "ОБЩИЕ СЛУЖБЫ",
    "01.4": "ФУНДАМЕНТАЛЬНЫЕ ИССЛЕДОВАНИЯ",
    "01.5": "НИОКР, СВЯЗАННЫЕ С ГОСУДАРСТВЕННЫМИ СЛУЖБАМИ ОБЩЕГО НАЗНАЧЕНИЯ",
    "01.6": "ГОСУДАРСТВЕННЫЕ СЛУЖБЫ ОБЩЕГО НАЗНАЧЕНИЯ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "01.7": "ОПЕРАЦИИ, СВЯЗАННЫЕ С ГОСУДАРСТВЕННЫМ ДОЛГОМ",
    "01.8": "ТРАНСФЕРТЫ ОБЩЕГО ХАРАКТЕРА МЕЖДУ ОРГАНАМИ ГОСУДАРСТВЕННОГО УПРАВЛЕНИЯ РАЗЛИЧНОГО УРОВНЯ",
    "02": "ОБОРОНА",
    "02.1": "ВООРУЖЕННЫЕ СИЛЫ",
    "02.2": "ГРАЖДАНСКАЯ ОБОРОНА",
    "02.3": "ИНОСТРАННАЯ ВОЕННАЯ ПОМОЩЬ",
    "02.4": "НИОКР В ОБЛАСТИ ОБОРОНЫ",
    "02.5": "ВОПРОСЫ ОБОРОНЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "03": "ОБЩЕСТВЕННЫЙ ПОРЯДОК И БЕЗОПАСНОСТЬ",
    "03.1": "ПОЛИЦЕЙСКИЕ СЛУЖБЫ",
    "03.2": "ПОЖАРНАЯ ОХРАНА",
    "03.3": "СУДЫ",
    "03.4": "ТЮРЬМЫ",
    "03.5": "НИОКР, СВЯЗАННЫЕ С ВОПРОСАМИ ОБЩЕСТВЕННОГО ПОРЯДКА И БЕЗОПАСНОСТИ",
    "03.6": "ВОПРОСЫ ОБЩЕСТВЕННОГО ПОРЯДКА И БЕЗОПАСНОСТИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "04": "ЭКОНОМИЧЕСКИЕ ВОПРОСЫ",
    "04.1": "ОБЩИЕ ЭКОНОМИЧЕСКИЕ И КОММЕРЧЕСКИЕ ВОПРОСЫ И ВОПРОСЫ, ОТНОСЯЩИЕСЯ К РАБОЧЕЙ СИЛЕ",
    "04.2": "СЕЛЬСКОЕ ХОЗЯЙСТВО, ЛЕСНОЕ ХОЗЯЙСТВО, РЫБОЛОВСТВО И ОХОТА",
    "04.3": "ТОПЛИВО И ЭНЕРГЕТИКА",
    "04.4": "ГОРНОДОБЫВАЮЩАЯ ПРОМЫШЛЕННОСТЬ, ОБРАБАТЫВАЮЩАЯ ПРОМЫШЛЕННОСТЬ И СТРОИТЕЛЬСТВО",
    "04.5": "ТРАНСПОРТ",
    "04.6": "СВЯЗЬ",
    "04.7": "ПРОЧИЕ ОТРАСЛИ",
    "04.8": "НИОКР, СВЯЗАННЫЕ С ЭКОНОМИЧЕСКИМИ ВОПРОСАМИ",
    "04.9": "ЭКОНОМИЧЕСКИЕ ВОПРОСЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "05": "ОХРАНА ОКРУЖАЮЩЕЙ СРЕДЫ",
    "05.1": "СБОР И УДАЛЕНИЕ ОТХОДОВ",
    "05.2": "УДАЛЕНИЕ И ОЧИСТКА СТОЧНЫХ ВОД",
    "05.3": "БОРЬБА С ЗАГРЯЗНЕНИЕМ ОКРУЖАЮЩЕЙ СРЕДЫ",
    "05.4": "ЗАЩИТА БИОРАЗНООБРАЗИЯ И ОХРАНА ЛАНДШАФТА",
    "05.5": "НИОКР В ОБЛАСТИ ОХРАНЫ ОКРУЖАЮЩЕЙ СРЕДЫ",
    "05.6": "ВОПРОСЫ ОХРАНЫ ОКРУЖАЮЩЕЙ СРЕДЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "06": "ЖИЛИЩНЫЕ И КОММУНАЛЬНЫЕ УСЛУГИ",
    "06.1": "ЖИЛИЩНОЕ СТРОИТЕЛЬСТВО",
    "06.2": "КОММУНАЛЬНОЕ РАЗВИТИЕ",
    "06.3": "ВОДОСНАБЖЕНИЕ",
    "06.4": "ОСВЕЩЕНИЕ УЛИЦ",
    "06.5": "НИОКР В ОБЛАСТИ ЖИЛИЩНОГО И КОММУНАЛЬНОГО ХОЗЯЙСТВА",
    "06.6": "ЖИЛИЩНЫЕ И КОММУНАЛЬНЫЕ УСЛУГИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "07": "ЗДРАВООХРАНЕНИЕ",
    "07.1": "МЕДИЦИНСКАЯ ПРОДУКЦИЯ, ОБОРУДОВАНИЕ И ИЗДЕЛИЯ, ИСПОЛЬЗУЕМЫЕ В МЕДИЦИНЕ",
    "07.2": "АМБУЛАТОРНЫЕ УСЛУГИ",
    "07.3": "УСЛУГИ БОЛЬНИЦ",
    "07.4": "УСЛУГИ В ОБЛАСТИ ЗДРАВООХРАНЕНИЯ",
    "07.5": "НИОКР В ОБЛАСТИ ЗДРАВООХРАНЕНИЯ",
    "07.6": "ВОПРОСЫ ЗДРАВООХРАНЕНИЯ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "08": "ОТДЫХ, КУЛЬТУРА И РЕЛИГИЯ",
    "08.1": "УСЛУГИ В ОБЛАСТИ ОРГАНИЗАЦИИ ОТДЫХА И ЗАНЯТИЙ СПОРТОМ",
    "08.2": "УСЛУГИ В ОБЛАСТИ КУЛЬТУРЫ",
    "08.3": "УСЛУГИ В ОБЛАСТИ РАДИО- И ТЕЛЕВЕЩАНИЯ И ИЗДАТЕЛЬСКОГО ДЕЛА",
    "08.4": "РЕЛИГИОЗНЫЕ И ДРУГИЕ ОБЩЕСТВЕННЫЕ УСЛУГИ",
    "08.5": "НИОКР В ОБЛАСТИ ОТДЫХА, КУЛЬТУРЫ И РЕЛИГИИ",
    "08.6": "ВОПРОСЫ ОТДЫХА, КУЛЬТУРЫ И РЕЛИГИИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "09": "ОБРАЗОВАНИЕ",
    "09.1": "ДОШКОЛЬНОЕ И НАЧАЛЬНОЕ ОБРАЗОВАНИЕ",
    "09.2": "СРЕДНЕЕ ОБРАЗОВАНИЕ",
    "09.3": "ПРОДОЛЖЕННОЕ СРЕДНЕЕ ОБРАЗОВАНИЕ",
    "09.4": "ВЫСШЕЕ ОБРАЗОВАНИЕ",
    "09.5": "ОБРАЗОВАНИЕ, НЕ ПОДРАЗДЕЛЕННОЕ ПО СТУПЕНЯМ",
    "09.6": "ВСПОМОГАТЕЛЬНЫЕ УСЛУГИ В СИСТЕМЕ ОБРАЗОВАНИЯ",
    "09.7": "НИОКР В ОБЛАСТИ ОБРАЗОВАНИЯ",
    "09.8": "ВОПРОСЫ ОБРАЗОВАНИЯ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "10": "СОЦИАЛЬНАЯ ЗАЩИТА",
    "10.1": "ЗАБОЛЕВАНИЯ И НЕТРУДОСПОСОБНОСТЬ",
    "10.2": "СТАРОСТЬ",
    "10.3": "ИЖДИВЕНЦЫ, ОСТАВШИЕСЯ БЕЗ КОРМИЛЬЦА",
    "10.4": "СЕМЬЯ И ДЕТИ",
    "10.5": "БЕЗРАБОТИЦА",
    "10.6": "ЖИЛЬЕ",
    "10.7": "ВОПРОСЫ СОЦИАЛЬНОЙ НЕУСТРОЕННОСТИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "10.8": "НИОКР В ОБЛАСТИ СОЦИАЛЬНОЙ ЗАЩИТЫ",
    "10.9": "ВОПРОСЫ СОЦИАЛЬНОЙ ЗАЩИТЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
}

FUNC_ID_COL = "ID"
FUNC_GO_COL = "Исполняющий ГО"
FUNC_TEXT_COL = "FunctionText"
SPHERE_NAME_COL = "Название сферы"
SPHERE_DESC_COL = "Описание сферы"
SPHERE_ACTIVITIES_COL = "Виды деятельности"


# --------------------------------------------------------------------- dataclass
Callback = Callable[[str, Any], None]
LogFunc = Callable[[str], None]


@dataclass
class SphereConfig:
    spheres_path: str
    functions_path: str
    output_path: str
    ai_mode: str
    system_prompt: str
    embed_server: str
    embed_model: str
    top_k_filter: int
    classification_batch_size: int
    max_retries: int
    temperature: float
    max_tokens: int
    model: str
    concurrent_requests: int
    api_key: str = ""
    local_servers: List[str] = field(default_factory=list)


@dataclass
class SphereAnalysisResult:
    processed_count: int
    skipped_count: int
    output_path: str


# --------------------------------------------------------------------- helpers
def _trim_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.fillna("", inplace=True)
    return df


def load_dataframe(path: str) -> pd.DataFrame:
    return _trim_dataframe(read_table_auto(path))


def require_columns(df: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = [col for col in columns if col and col not in df.columns]
    if missing:
        raise ValueError(
            f"В файле '{label}' отсутствуют необходимые колонки: {', '.join(missing)}"
        )


def normalize_name_key(value: str) -> str:
    return " ".join(str(value).strip().split()).upper()


def build_sphere_indexes(sphere_rows: List[dict]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    exact_map: Dict[str, dict] = {}
    normalized_map: Dict[str, dict] = {}
    for row in sphere_rows:
        name = str(row.get(SPHERE_NAME_COL, "")).strip()
        if not name:
            continue
        meta = {
            "name": name,
            "desc": str(row.get(SPHERE_DESC_COL, "")).strip(),
            "activities": str(row.get(SPHERE_ACTIVITIES_COL, "")).strip(),
        }
        exact_map[name] = meta
        normalized_map[normalize_name_key(name)] = meta
    return exact_map, normalized_map


def format_spheres_for_prompt(sphere_rows: List[dict]) -> str:
    blocks = []
    for item in sphere_rows:
        blocks.append(
            "Название сферы: {name}\nОписание: {desc}\nВиды деятельности: {acts}".format(
                name=str(item.get(SPHERE_NAME_COL, "")),
                desc=str(item.get(SPHERE_DESC_COL, "")),
                acts=str(item.get(SPHERE_ACTIVITIES_COL, "")),
            )
        )
    return "\n---\n".join(blocks)


def build_prompt_messages(system_prompt: str, gov_body: str, function_text: str, spheres_text: str) -> List[Dict[str, str]]:
    user_content = (
        f'ДАННЫЕ ДЛЯ АНАЛИЗА:\nГосударственный орган: "{gov_body}"\n'
        f'Текст функции: "{function_text}"\n\nСПИСОК РЕЛЕВАНТНЫХ СФЕР:\n---\n{spheres_text}\n---'
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def parse_name_from_model(content: str) -> str:
    payload = (content or "").strip()
    try:
        match = re.search(r"\{.*\}", payload, re.DOTALL)
        if match:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict) and "name" in obj:
                return normalize_whitespace(obj["name"])
    except Exception:  # pragma: no cover - resilience
        logging.warning("Не удалось распарсить JSON из ответа модели: %s", payload)
    if "NO_MATCH" in payload.upper():
        return "NO_MATCH"
    return payload.splitlines()[0].strip()


def add_hierarchy_spheres(df: pd.DataFrame) -> pd.DataFrame:
    if "Sphere" not in df.columns:
        df = df.copy()
        df["Sphere"] = ""
    df_copy = df.copy()
    df_copy["temp_code"] = df_copy["Sphere"].astype(str).str.split(" ").str[0]
    df_copy["Sphere_2_code"] = df_copy["temp_code"].str.slice(0, 4)
    df_copy["Sphere_3_code"] = df_copy["temp_code"].str.slice(0, 2)

    def _compose(code: str) -> str:
        name = SPHERES_MAP.get(code, "")
        return f"{code} {name.capitalize()}" if name else ""

    df_copy["Sphere_2"] = df_copy["Sphere_2_code"].apply(_compose)
    df_copy["Sphere_3"] = df_copy["Sphere_3_code"].apply(_compose)
    return df_copy.drop(columns=["temp_code", "Sphere_2_code", "Sphere_3_code"])


# ---------------------------------------------------------------- AI helpers ---
async def async_get_embedding_batch(
    client: "openai.AsyncOpenAI",
    model: str,
    texts: List[str],
    semaphore: asyncio.Semaphore,
    stop_event: threading.Event,
) -> Optional[List[List[float]]]:
    try:
        async with semaphore:
            if stop_event.is_set():
                return None
            valid_texts = [t if normalize_whitespace(t) else " " for t in texts]
            response = await client.embeddings.create(
                model=model,
                input=valid_texts,
                timeout=180.0,
            )
            return [item.embedding for item in response.data]
    except Exception as exc:  # pragma: no cover - network variability
        logging.error("Ошибка получения эмбеддингов: %s", exc)
        return None


async def _call_api_with_backoff(
    client: "openai.AsyncOpenAI",
    model: str,
    messages: List[dict],
    temperature: float,
    semaphore: asyncio.Semaphore,
    max_tokens: int,
    max_retries: int,
    run_config: SphereConfig,
    stop_event: threading.Event,
) -> str:
    api_params = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }
    if run_config.ai_mode == "Онлайн":
        api_params["response_format"] = {"type": "json_object"}

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        if stop_event.is_set():
            return "ОСТАНОВЛЕНО"
        try:
            async with semaphore:
                response = await client.chat.completions.create(**api_params)
                return response.choices[0].message.content.strip()
        except openai.APIStatusError as exc:  # pragma: no cover - network
            last_exc = exc
            logging.warning(
                "Попытка API %s/%s не удалась. Код: %s. Ответ: %s",
                attempt + 1,
                max_retries,
                exc.status_code,
                exc.response.text,
            )
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            logging.warning(
                "Попытка API %s/%s не удалась. Ошибка: %s",
                attempt + 1,
                max_retries,
                exc,
            )
        if attempt < max_retries - 1:
            await asyncio.sleep(min(60, (2**attempt) + np.random.random()))
    logging.error("Все попытки вызова API исчерпаны. Последняя ошибка: %s", last_exc)
    return f"ОШИБКА API: {last_exc}"


async def classify_one_async(
    client: "openai.AsyncOpenAI",
    semaphore: asyncio.Semaphore,
    run_config: SphereConfig,
    func_item: dict,
    spheres_prompt_filtered: str,
    valid_names_exact: set,
    valid_names_norm_map: Dict[str, dict],
    stop_event: threading.Event,
    progress_callback: Optional[Callback],
) -> dict:
    gov_body = normalize_whitespace(func_item.get(FUNC_GO_COL, ""))
    func_text = normalize_whitespace(func_item.get(FUNC_TEXT_COL, ""))
    messages = build_prompt_messages(
        run_config.system_prompt, gov_body, func_text, spheres_prompt_filtered
    )
    content = await _call_api_with_backoff(
        client,
        run_config.model,
        messages,
        run_config.temperature,
        semaphore,
        run_config.max_tokens,
        run_config.max_retries,
        run_config,
        stop_event,
    )
    if content.startswith("ОШИБКА API") or content == "ОСТАНОВЛЕНО":
        return {"assigned_name": "ERROR", "status": "ERROR"}

    name = parse_name_from_model(content)
    if name == "NO_MATCH":
        return {"assigned_name": "NO_MATCH", "status": "NO_MATCH"}

    status = "INVALID"
    matched_name = name
    if name in valid_names_exact:
        status = "OK"
    else:
        meta = valid_names_norm_map.get(normalize_name_key(name))
        if meta:
            status = "OK"
            matched_name = meta["name"]
    if progress_callback:
        msg = f"ID {func_item.get(FUNC_ID_COL, 'N/A')}: статус = {status}, ответ = {matched_name}"
        progress_callback("log", msg)
    return {"assigned_name": matched_name, "status": status}


async def worker_task(
    client: "openai.AsyncOpenAI",
    semaphore: asyncio.Semaphore,
    run_config: SphereConfig,
    queue: "asyncio.Queue[Tuple[int, dict, Optional[np.ndarray]]]",
    results: List[Optional[dict]],
    sphere_rows: List[dict],
    sphere_embeddings: np.ndarray,
    valid_names_exact: set,
    valid_names_norm: Dict[str, dict],
    stop_event: threading.Event,
    progress_callback: Optional[Callback],
) -> None:
    top_k = max(1, run_config.top_k_filter)
    batch_size = max(1, run_config.classification_batch_size)

    while not stop_event.is_set():
        try:
            idx, func_item, func_embedding = await queue.get()
        except asyncio.CancelledError:  # pragma: no cover - task cancellation
            break
        if func_embedding is None or not np.any(func_embedding):
            results[idx] = {"assigned_name": "INVALID_INPUT", "status": "INVALID_INPUT"}
            if progress_callback:
                progress_callback("classify", 1)
            queue.task_done()
            continue

        similarities = sphere_embeddings @ func_embedding
        denom = np.linalg.norm(sphere_embeddings, axis=1) * np.linalg.norm(func_embedding)
        denom = np.where(denom == 0, 1e-9, denom)
        similarities = similarities / denom
        sorted_indices = np.argsort(similarities)[::-1]
        top_candidate_indices = sorted_indices[:top_k]

        final_res = {"assigned_name": "NO_MATCH", "status": "NO_MATCH"}
        for i in range(0, len(top_candidate_indices), batch_size):
            if stop_event.is_set():
                break
            candidate_rows = [sphere_rows[j] for j in top_candidate_indices[i : i + batch_size]]
            spheres_prompt_short = format_spheres_for_prompt(candidate_rows)
            batch_res = await classify_one_async(
                client,
                semaphore,
                run_config,
                func_item,
                spheres_prompt_short,
                valid_names_exact,
                valid_names_norm,
                stop_event,
                progress_callback,
            )
            if batch_res.get("status") == "OK":
                final_res = batch_res
                break
        results[idx] = final_res
        if progress_callback:
            progress_callback("classify", 1)
        queue.task_done()


# -------------------------------------------------------------- main workflow
async def _analysis_logic(
    config: SphereConfig,
    progress_callback: Optional[Callback],
    stop_event: threading.Event,
    log: LogFunc,
) -> SphereAnalysisResult:
    def emit(event: str, payload: Any) -> None:
        if progress_callback:
            progress_callback(event, payload)

    log("ШАГ 1: ЗАПУСК LLM-КЛАССИФИКАЦИИ")
    log("Загрузка и подготовка данных...")

    spheres_df = load_dataframe(config.spheres_path)
    functions_df = load_dataframe(config.functions_path)

    require_columns(
        spheres_df,
        [SPHERE_NAME_COL, SPHERE_DESC_COL, SPHERE_ACTIVITIES_COL],
        "Сферы",
    )
    require_columns(functions_df, [FUNC_GO_COL, FUNC_TEXT_COL], "Функции")

    if FUNC_ID_COL not in functions_df.columns:
        log(f"Колонка '{FUNC_ID_COL}' не найдена. Создаю ее на основе индекса.")
        functions_df = functions_df.reset_index().rename(columns={"index": FUNC_ID_COL})

    df_already_processed = pd.DataFrame()
    df_to_process = pd.DataFrame()

    if "Sphere_3" in functions_df.columns:
        functions_df["Sphere_3"] = functions_df["Sphere_3"].astype(str).str.strip()
        functions_df["Sphere_3"].replace(["", "nan", "None"], pd.NA, inplace=True)
        df_already_processed = functions_df[functions_df["Sphere_3"].notna()].copy()
        df_to_process = functions_df[functions_df["Sphere_3"].isna()].copy()
        if not df_already_processed.empty:
            log(
                f"Найдено {len(df_already_processed)} функций с уже заполненной 'Sphere_3'. Они будут пропущены."
            )
    else:
        log("Колонка 'Sphere_3' не найдена. Все функции будут обработаны.")
        df_to_process = functions_df.copy()

    if df_to_process.empty:
        log("Не найдено новых функций для обработки. Процесс завершен.")
        write_dataframe(functions_df, config.output_path, index=False)
        return SphereAnalysisResult(processed_count=0, skipped_count=len(functions_df), output_path=config.output_path)

    sphere_rows = spheres_df.to_dict("records")
    function_rows = df_to_process.to_dict("records")
    sphere_exact, sphere_norm = build_sphere_indexes(sphere_rows)

    clients: List["openai.AsyncOpenAI"] = []
    semaphore = asyncio.Semaphore(config.concurrent_requests)
    try:
        if config.ai_mode == "Онлайн":
            client = openai.AsyncOpenAI(api_key=config.api_key)
            clients.append(client)
            log(f"Режим: OpenAI. Модель: {config.model}")
        else:
            num_servers = len(config.local_servers)
            concurrency_per_server = max(1, config.concurrent_requests // max(num_servers, 1))
            log(
                f"Режим: Локальный. Серверов: {num_servers}. Запросов на сервер: {concurrency_per_server}"
            )
            for url in config.local_servers:
                clients.append(
                    openai.AsyncOpenAI(base_url=prepare_api_base_url(url), api_key="not-needed")
                )
    except Exception as exc:  # pragma: no cover - network
        for client in clients:
            await client.close()
        raise

    if not clients:
        raise ValueError("Не удалось создать ни одного клиента ИИ.")
    client_cycle = itertools.cycle(clients)

    embed_client = openai.AsyncOpenAI(
        base_url=prepare_api_base_url(config.embed_server),
        api_key="not-needed",
    )
    embed_semaphore = asyncio.Semaphore(32)

    try:
        log(f"Получение эмбеддингов для {len(sphere_rows)} Сфер...")
        emit("reset", ("embed_spheres", len(sphere_rows)))
        sphere_corpus = [
            f"Сфера: {row.get(SPHERE_NAME_COL, '')}. Описание: {row.get(SPHERE_DESC_COL, '')}. "
            f"Деятельность: {row.get(SPHERE_ACTIVITIES_COL, '')}"
            for row in sphere_rows
        ]
        sphere_embeddings_list: List[List[float]] = []
        for i in range(0, len(sphere_corpus), 64):
            if stop_event.is_set():
                raise InterruptedError()
            batch = sphere_corpus[i : i + 64]
            embeds = await async_get_embedding_batch(embed_client, config.embed_model, batch, embed_semaphore, stop_event)
            if embeds:
                sphere_embeddings_list.extend(embeds)
            emit("embed_spheres", len(batch))
        if not sphere_embeddings_list:
            raise RuntimeError("Не удалось получить эмбеддинги для списка сфер.")
        sphere_embeddings = np.array(sphere_embeddings_list, dtype=np.float32)

        log(f"Получение эмбеддингов для {len(function_rows)} Функций...")
        emit("reset", ("embed_funcs", len(function_rows)))
        func_corpus = [
            f"Орган: {row.get(FUNC_GO_COL, '')}. Функция: {row.get(FUNC_TEXT_COL, '')}"
            for row in function_rows
        ]
        function_embeddings: List[Optional[np.ndarray]] = []
        for i in range(0, len(func_corpus), 64):
            if stop_event.is_set():
                raise InterruptedError()
            batch = func_corpus[i : i + 64]
            embeds = await async_get_embedding_batch(embed_client, config.embed_model, batch, embed_semaphore, stop_event)
            if embeds:
                function_embeddings.extend(np.array(embed, dtype=np.float32) for embed in embeds)
            else:
                function_embeddings.extend([None] * len(batch))
            emit("embed_funcs", len(batch))
        log("Векторизация завершена.")

        log(f"Начинаем классификацию {len(function_rows)} функций...")
        emit("reset", ("classify", len(function_rows)))
        queue: asyncio.Queue[Tuple[int, dict, Optional[np.ndarray]]] = asyncio.Queue()
        for i, item in enumerate(function_rows):
            queue.put_nowait((i, item, function_embeddings[i]))

        results: List[Optional[dict]] = [None] * len(function_rows)
        tasks = [
            asyncio.create_task(
                worker_task(
                    next(client_cycle),
                    semaphore,
                    config,
                    queue,
                    results,
                    sphere_rows,
                    sphere_embeddings,
                    set(sphere_exact.keys()),
                    sphere_norm,
                    stop_event,
                    progress_callback,
                )
            )
            for _ in range(config.concurrent_requests)
        ]
        await queue.join()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if stop_event.is_set():
            raise InterruptedError()

        log("Обработка результатов классификации...")
        output_rows = []
        for i, func_row in enumerate(function_rows):
            result_item = results[i] or {}
            sphere_name = "NO_MATCH"
            if result_item.get("status") == "OK":
                sphere_name = result_item.get("assigned_name", "NO_MATCH")
            elif result_item:
                sphere_name = result_item.get("status", "UNKNOWN_ERROR")
            new_row = func_row.copy()
            new_row["Sphere"] = sphere_name
            output_rows.append(new_row)

        output_df = pd.DataFrame(output_rows)
        log("ШАГ 1 завершен.")

        log("Объединение ранее обработанных и новых результатов...")
        combined_df = pd.concat([df_already_processed, output_df], ignore_index=True)

        log("ШАГ 2: ДОБАВЛЕНИЕ ИЕРАРХИЧЕСКИХ СФЕР (Sphere_2, Sphere_3)")
        final_df = add_hierarchy_spheres(combined_df)

        if FUNC_ID_COL in functions_df.columns and FUNC_ID_COL in final_df.columns:
            try:
                final_df = (
                    final_df.drop_duplicates(subset=[FUNC_ID_COL], keep="last")
                    .set_index(FUNC_ID_COL)
                    .loc[functions_df[FUNC_ID_COL]]
                    .reset_index()
                )
            except KeyError:  # pragma: no cover - inconsistent data
                log("Не удалось полностью восстановить исходный порядок строк.")

        log(f"Сохранение итогового результата в файл '{config.output_path}'...")
        write_dataframe(final_df, config.output_path, index=False)
        log("Все готово! Итоговый файл успешно сохранен.")
        return SphereAnalysisResult(
            processed_count=len(function_rows),
            skipped_count=len(df_already_processed),
            output_path=config.output_path,
        )
    finally:
        for client in clients:
            await client.close()
        await embed_client.close()


def run_sphere_analysis(
    config: SphereConfig,
    progress_callback: Optional[Callback],
    stop_event: threading.Event,
    log: LogFunc,
) -> SphereAnalysisResult:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_analysis_logic(config, progress_callback, stop_event, log))
    except InterruptedError:
        raise
    finally:
        loop.close()


__all__ = [
    "SphereConfig",
    "SphereAnalysisResult",
    "run_sphere_analysis",
    "add_hierarchy_spheres",
    "SYSTEM_PROMPT_TEMPLATE",
]
