#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Итоговый комплексный классификатор функций по сферам v3.3
================================================================================
Автор: Gemini (на основе запросов пользователя Кастер)
Версия: 3.3 (18.08.2025) - Изменена логика пропуска

Архитектура v3.3:
- ИЗМЕНЕНИЕ ЛОГИКИ: Пропуск строк теперь осуществляется по наличию значения в
  колонке 'Sphere_3', а не 'Sphere'. Это позволяет точнее до-обрабатывать файлы,
  где основная классификация прошла, а иерархия — нет.
- ДОПОЛНЕНИЕ: Скрипт по-прежнему пропускает уже обработанные строки,
  но теперь на основе наличия финального уровня иерархии.
- ИСПРАВЛЕНИЕ API (Локальные модели): Параметр 'response_format' сделан
  условным, чтобы избежать ошибки "400 Bad Request".
- ИСПРАВЛЕНИЕ РЕГИСТРА: Названия иерархических сфер (Sphere_2, Sphere_3)
  форматируются в нормальный регистр (Capitalize).
"""

import sys
import re
import json
import time
import random
import logging
import os
from pathlib import Path
from queue import Queue, Empty
from threading import Thread
import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Dict, Any, Optional
import asyncio
import itertools

# --- ЗАВИСИСИМОСТИ ---
try:
    import sv_ttk
except ImportError:
    print("=" * 80)
    print("ПРЕДУПРЕЖДЕНИЕ: Не найдена тема оформления 'sv-ttk'.")
    print(
        "Интерфейс будет стандартным. Для улучшения вида установите тему: pip install sv-ttk"
    )
    print("=" * 80)
    sv_ttk = None

try:
    import openai
except ImportError:
    print("=" * 80)
    print("ОШИБКА: Не найдена библиотека openai.")
    print("Пожалуйста, установите ее командой: pip install openai")
    print("=" * 80)
    openai = None
    exit()

# ---- ПРОВЕРКА ВЕРСИИ OPENAI ----
try:
    from importlib.metadata import version

    openai_version = version("openai")
    if tuple(map(int, openai_version.split("."))) < (1, 0, 0):
        raise ImportError(
            f"Устаревшая версия библиотеки OpenAI ({openai_version}). Требуется >= 1.0.0"
        )
except ImportError as e:
    error_message = (
        f"ОШИБКА: {e}\n\n"
        "Для работы этого скрипта требуется версия openai 1.0.0 или новее.\n\n"
        "Пожалуйста, обновите ее, выполнив в терминале команду:\n"
        "pip install --upgrade openai"
    )
    print("=" * 80)
    print(error_message)
    print("=" * 80)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Ошибка версии OpenAI", error_message)
        root.destroy()
    except tk.TclError:
        pass
    exit()
except Exception as e:
    print(f"Не удалось проверить версию OpenAI: {e}")


# === СПРАВОЧНИК ДЛЯ ИЕРАРХИИ СФЕР (Sphere_2, Sphere_3) ===
SPHERES_MAP = {
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

# --- Названия колонок в исходных файлах ---
FUNC_ID_COL = "ID"
FUNC_GO_COL = "Исполняющий ГО"
FUNC_TEXT_COL = "FunctionText"
SPHERE_NAME_COL = "Название сферы"
SPHERE_DESC_COL = "Описание сферы"
SPHERE_ACTIVITIES_COL = "Виды деятельности"

# --- Системный промпт по умолчанию ---
SYSTEM_PROMPT_TEMPLATE = """You are an expert in classifying the functions of government bodies. Your task is to select the ONE most suitable sphere from the provided list for the given function.

CRITICAL RULES:
1. To understand the function's essence, always analyze it based on the 'action - subject - purpose' structure. Consider the role of the government body and do not rely solely on keywords.
2. If no sphere is a direct and logical match, you MUST return "NO_MATCH".
3. Otherwise, return the answer as a SINGLE JSON line without any explanations. The value for the "name" key MUST be in Russian, taken directly from the provided list of spheres.

RESPONSE EXAMPLES:
- Match found: {"name":"02.1 ВООРУЖЕННЫЕ СИЛЫ"}
- No match found: {"name":"NO_MATCH"}"""

# --- Глобальные переменные для асинхронной части ---
progress_callback_async = None
stop_event_async = asyncio.Event()


# ---------------------------
# (Вспомогательные утилиты)
# ---------------------------


def prepare_api_base_url(url: str) -> str:
    """Готовит URL для OpenAI-совместимого API."""
    url = url.strip().rstrip("/")
    if not url:
        return url
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def load_df(file_path: str) -> pd.DataFrame:
    """Загружает данные из Excel или CSV."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    file_path_lower = file_path.lower()
    if file_path_lower.endswith(".xlsx"):
        df = pd.read_excel(file_path, dtype=str, engine="openpyxl")
    elif file_path_lower.endswith(".csv"):
        try:
            df = pd.read_csv(file_path, sep=";", encoding="utf-8-sig", dtype=str)
        except Exception:
            try:
                df = pd.read_csv(file_path, sep=",", encoding="utf-8", dtype=str)
            except Exception as e:
                raise RuntimeError(
                    f"Не удалось прочитать CSV '{file_path}'. Проверьте кодировку (UTF-8) и разделитель. Ошибка: {e}"
                )
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла: {file_path}. Используйте .xlsx или .csv."
        )

    df.fillna("", inplace=True)
    return df


def require_cols(df: pd.DataFrame, cols: list, label: str):
    """Проверяет наличие обязательных колонок в DataFrame."""
    miss = [c for c in cols if c and c not in df.columns]
    if miss:
        raise ValueError(
            f"В файле '{label}' отсутствуют необходимые колонки: {', '.join(miss)}"
        )


def normalize_name_key(s: str) -> str:
    """Нормализует строку для сопоставления."""
    return " ".join(str(s).strip().split()).upper()


def build_sphere_indexes(sphere_rows: list) -> tuple[dict, dict]:
    """Создает словари для быстрого поиска сфер."""
    exact_map, norm_map = {}, {}
    for r in sphere_rows:
        name = str(r.get(SPHERE_NAME_COL, "")).strip()
        if not name:
            continue
        meta = {
            "name": name,
            "desc": str(r.get(SPHERE_DESC_COL, "")).strip(),
            "activities": str(r.get(SPHERE_ACTIVITIES_COL, "")).strip(),
        }
        exact_map[name] = meta
        norm_map[normalize_name_key(name)] = meta
    return exact_map, norm_map


def format_spheres_for_prompt(sphere_rows: list) -> str:
    """Форматирует список сфер для включения в промпт к LLM."""
    blocks = []
    for item in sphere_rows:
        nm = str(item.get(SPHERE_NAME_COL, ""))
        ds = str(item.get(SPHERE_DESC_COL, ""))
        acts = str(item.get(SPHERE_ACTIVITIES_COL, ""))
        blocks.append(
            f"Название сферы: {nm}\nОписание: {ds}\nВиды деятельности: {acts}"
        )
    return "\n---\n".join(blocks)


def build_prompt(
    system_prompt: str, gov_body: str, function_text: str, spheres_text_filtered: str
) -> List[Dict[str, str]]:
    """Создает промпт в формате сообщений для LLM."""
    user_content = f"""
ДАННЫЕ ДЛЯ АНАЛИЗА:
Государственный орган: "{gov_body}"
Текст функции: "{function_text}"

СПИСОК РЕЛЕВАНТНЫХ СФЕР:
---
{spheres_text_filtered}
---"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def parse_name_from_model(content: str) -> str:
    """Извлекает название сферы из JSON-ответа модели."""
    s = (content or "").strip()
    try:
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict) and "name" in obj:
                return str(obj["name"]).strip()
    except Exception:
        logging.warning(f"Не удалось распарсить JSON из ответа модели: {s}")

    if "NO_MATCH" in s.upper():
        return "NO_MATCH"
    return s.splitlines()[0].strip()  # Фоллбэк


# ---------------------------------------------
# Логика обработки данных (асинхронная и воркеры)
# ---------------------------------------------


async def async_get_embedding_batch(
    client: openai.AsyncOpenAI,
    model: str,
    texts: List[str],
    semaphore: asyncio.Semaphore,
) -> Optional[List[List[float]]]:
    """Асинхронно получает эмбеддинги для списка текстов."""
    try:
        async with semaphore:
            if stop_event_async.is_set():
                return None
            valid_texts = [t if t and t.strip() else " " for t in texts]
            resp = await client.embeddings.create(
                model=model, input=valid_texts, timeout=180.0
            )
            return [item.embedding for item in resp.data]
    except Exception as e:
        logging.error(f"Ошибка получения эмбеддингов: {e}")
        return None


async def _call_api_with_backoff(
    client: openai.AsyncOpenAI,
    model: str,
    messages: List[dict],
    temperature: float,
    semaphore: asyncio.Semaphore,
    max_tokens: int,
    max_retries: int,
    run_config: dict,
):
    """Внутренняя асинхронная функция для вызова API с контролем нагрузки и повторными попытками."""
    last_exc = None

    # Формируем параметры запроса
    api_params = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }

    # Добавляем response_format только для режима "Онлайн"
    if run_config.get("ai_mode") == "Онлайн":
        api_params["response_format"] = {"type": "json_object"}

    for attempt in range(max_retries):
        if stop_event_async.is_set():
            return "ОСТАНОВЛЕНО"
        try:
            async with semaphore:
                # Используем **api_params для передачи всех параметров
                resp = await client.chat.completions.create(**api_params)
                return resp.choices[0].message.content.strip()
        except openai.APIStatusError as e:
            last_exc = e
            error_details = f"Код: {e.status_code}. Ответ: {e.response.text}"
            logging.warning(
                f"Попытка API {attempt + 1}/{max_retries} не удалась. Ошибка статуса API: {error_details}. Повтор..."
            )
        except Exception as e:
            last_exc = e
            logging.warning(
                f"Попытка API {attempt + 1}/{max_retries} не удалась. Общая ошибка: {e}. Повтор..."
            )

        if attempt < max_retries - 1:
            await asyncio.sleep(min(60, (2**attempt) + random.random()))

    logging.error(f"Все попытки вызова API исчерпаны. Последняя ошибка: {last_exc}")
    return f"ОШИБКА API: {last_exc}"


async def classify_one_async(
    client: openai.AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    run_config: dict,
    func_item: dict,
    spheres_prompt_filtered: str,
    valid_names_exact: set,
    valid_names_norm_map: dict,
) -> dict:
    """Выполняет классификацию одной функции, отправляя асинхронный запрос к LLM."""
    gov_body = str(func_item.get(FUNC_GO_COL, "")).strip()
    func_text = str(func_item.get(FUNC_TEXT_COL, "")).strip()

    messages = build_prompt(
        run_config["system_prompt"], gov_body, func_text, spheres_prompt_filtered
    )

    content = await _call_api_with_backoff(
        client,
        run_config["model"],
        messages,
        run_config["temperature"],
        semaphore,
        run_config["max_tokens"],
        run_config["max_retries"],
        run_config,
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
        key = normalize_name_key(name)
        meta = valid_names_norm_map.get(key)
        if meta:
            status = "OK"
            matched_name = meta["name"]
    return {"assigned_name": matched_name, "status": status}


async def worker_task(
    client: openai.AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    run_config: dict,
    queue: asyncio.Queue,
    results: list,
    sphere_rows: list,
    sphere_embeddings: np.ndarray,
    valid_names_exact: set,
    valid_names_norm: dict,
):
    """Основной цикл работы асинхронного воркера."""
    while not stop_event_async.is_set():
        try:
            idx, func_item, func_embedding = await queue.get()
        except asyncio.CancelledError:
            break

        if func_embedding is None:
            results[idx] = {"assigned_name": "INVALID_INPUT", "status": "INVALID_INPUT"}
            if progress_callback_async:
                progress_callback_async("classify", 1)
            queue.task_done()
            continue

        similarities = np.dot(sphere_embeddings, func_embedding) / (
            np.linalg.norm(sphere_embeddings, axis=1) * np.linalg.norm(func_embedding)
        )
        sorted_indices = np.argsort(similarities)[::-1]
        top_candidate_indices = sorted_indices[: run_config["top_k_filter"]]

        final_res = {"assigned_name": "NO_MATCH", "status": "NO_MATCH"}
        batch_size = run_config["classification_batch_size"]

        for i in range(0, len(top_candidate_indices), batch_size):
            if stop_event_async.is_set():
                final_res = {"assigned_name": "STOPPED", "status": "STOPPED"}
                break

            batch_indices = top_candidate_indices[i : i + batch_size]
            if not len(batch_indices):
                break

            filtered_sphere_rows = [sphere_rows[j] for j in batch_indices]
            spheres_prompt_short = format_spheres_for_prompt(filtered_sphere_rows)

            batch_res = await classify_one_async(
                client,
                semaphore,
                run_config,
                func_item,
                spheres_prompt_short,
                valid_names_exact,
                valid_names_norm,
            )

            if batch_res.get("status") == "OK":
                final_res = batch_res
                if progress_callback_async:
                    log_msg = f"ID {func_item.get(FUNC_ID_COL, 'N/A')}: ✔️ Найдено совпадение '{final_res.get('assigned_name')}' в порции #{i // batch_size + 1}."
                    progress_callback_async("log", log_msg)
                break
            else:
                if progress_callback_async:
                    log_msg = f"ID {func_item.get(FUNC_ID_COL, 'N/A')}: ❌ NO_MATCH в порции #{i // batch_size + 1}. Пробую следующих кандидатов..."
                    progress_callback_async("log", log_msg)

        results[idx] = final_res
        if progress_callback_async:
            progress_callback_async("classify", 1)
        queue.task_done()


def add_hierarchy_spheres(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет столбцы Sphere_2 и Sphere_3 на основе иерархии."""
    if "Sphere" not in df.columns:
        # Если нет колонки Sphere, создаем ее, чтобы избежать ошибок
        df["Sphere"] = ""

    # Работаем с копией, чтобы избежать предупреждений
    df_copy = df.copy()

    df_copy["temp_code"] = df_copy["Sphere"].astype(str).str.split(" ").str[0]
    df_copy["Sphere_2_code"] = df_copy["temp_code"].str.slice(0, 4)
    df_copy["Sphere_3_code"] = df_copy["temp_code"].str.slice(0, 2)

    df_copy["Sphere_2"] = (
        df_copy["Sphere_2_code"]
        .map(SPHERES_MAP)
        .fillna("")
        .astype(str)
        .apply(lambda x: x.capitalize())
    )
    df_copy["Sphere_3"] = (
        df_copy["Sphere_3_code"]
        .map(SPHERES_MAP)
        .fillna("")
        .astype(str)
        .apply(lambda x: x.capitalize())
    )

    df_copy["Sphere_2"] = df_copy.apply(
        lambda row: f"{row['Sphere_2_code']} {row['Sphere_2']}"
        if row["Sphere_2"]
        else "",
        axis=1,
    )
    df_copy["Sphere_3"] = df_copy.apply(
        lambda row: f"{row['Sphere_3_code']} {row['Sphere_3']}"
        if row["Sphere_3"]
        else "",
        axis=1,
    )

    return df_copy.drop(columns=["temp_code", "Sphere_2_code", "Sphere_3_code"])


# ---------------------------------------------
# Класс приложения с графическим интерфейсом
# ---------------------------------------------


class SphereClassifierApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Классификатор функций по сферам v3.3")
        self.root.geometry("1200x900")

        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = Queue()
        self.root.after(100, self._drain_ui_queue)

        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)
        self._worker_thread: Optional[Thread] = None

        self.stage_start_times: Dict[str, float] = {}
        self.etr_labels: Dict[str, tk.Label] = {}
        self.stage_progress: Dict[str, int] = {}

        self._build_ui()

    def log(self, message: str, to_terminal: bool = False):
        self.ui_queue.put(("log", message))
        if to_terminal:
            logging.info(message)

    def _drain_ui_queue(self):
        try:
            while True:
                command, value = self.ui_queue.get_nowait()
                if command == "status":
                    self.status_label.config(text=str(value)[:200])
                elif command == "log":
                    self.log_text.config(state="normal")
                    self.log_text.insert(
                        tk.END, f"[{time.strftime('%H:%M:%S')}] {value}\n"
                    )
                    self.log_text.config(state="disabled")
                    self.log_text.see(tk.END)
                elif command == "progress":
                    bar_name, data = value
                    if bar_name == "reset":
                        bar_key, total = data
                        self.progress_bars[bar_key]["maximum"] = max(1, total)
                        self.progress_bars[bar_key]["value"] = 0
                        self.stage_progress[bar_key] = 0
                        self.stage_start_times[bar_key] = time.time()
                        self.etr_labels[bar_key].config(text="ETR: Вычисление...")
                    else:
                        if bar_name not in self.stage_progress:
                            self.stage_progress[bar_name] = 0
                        self.stage_progress[bar_name] += data
                        current = self.stage_progress[bar_name]
                        total = self.progress_bars[bar_name]["maximum"]
                        self.progress_bars[bar_name]["value"] = current

                        if bar_name in self.etr_labels:
                            if current >= total:
                                self.etr_labels[bar_name].config(text="ETR: Завершено")
                            elif current > 2 and bar_name in self.stage_start_times:
                                elapsed = time.time() - self.stage_start_times[bar_name]
                                ips = current / elapsed
                                remaining = total - current
                                if ips > 0:
                                    etr_sec = int(remaining / ips)
                                    self.etr_labels[bar_name].config(
                                        text=f"ETR: {time.strftime('%M:%S', time.gmtime(etr_sec))}"
                                    )
                elif command == "worker_done":
                    self._on_worker_finished()
        except Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=450)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))

        # --- Файлы ---
        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Файлы")
        files_frame.pack(fill=tk.X, pady=5)
        self.spheres_path_var = tk.StringVar()
        self.functions_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self._create_file_row(
            files_frame,
            "Файл со сферами:",
            self.spheres_path_var,
            self._choose_spheres_file,
        )
        self._create_file_row(
            files_frame,
            "Файл с функциями:",
            self.functions_path_var,
            self._choose_functions_file,
        )
        self._create_file_row(
            files_frame,
            "Итоговый файл:",
            self.output_path_var,
            self._choose_output_file,
        )

        # --- Настройки ИИ ---
        ai_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки ИИ")
        ai_frame.pack(fill=tk.X, pady=5)
        self.ai_mode_var = tk.StringVar(value="Онлайн")
        mode_frame = ttk.Frame(ai_frame)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(
            mode_frame,
            text="Онлайн (OpenAI API)",
            variable=self.ai_mode_var,
            value="Онлайн",
            command=self._on_ai_mode_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="Локальная (LM Studio)",
            variable=self.ai_mode_var,
            value="Локальная",
            command=self._on_ai_mode_change,
        ).pack(side=tk.LEFT, padx=10)

        # -- Фрейм для Онлайн настроек --
        self.online_frame = ttk.Frame(ai_frame)
        self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        self.api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.online_model_var = tk.StringVar(value="gpt-4-turbo")
        self.online_concurrent_var = tk.IntVar(value=30)
        self._create_entry_row(
            self.online_frame, "OpenAI API Key:", self.api_key_var, show="*"
        )
        self._create_entry_row(self.online_frame, "Модель:", self.online_model_var)
        self._create_entry_row(
            self.online_frame, "Параллельных запросов:", self.online_concurrent_var
        )

        # -- Фрейм для Локальных настроек --
        self.local_frame = ttk.Frame(ai_frame)
        self.local_model_var = tk.StringVar(value="google/gemma-3-12b")
        self.local_concurrent_var = tk.IntVar(value=5)
        self._create_entry_row(self.local_frame, "Модель:", self.local_model_var)
        self._create_entry_row(
            self.local_frame, "Параллельных запросов:", self.local_concurrent_var
        )
        ttk.Label(
            self.local_frame, text="Адреса серверов (каждый с новой строки):"
        ).pack(anchor="w", pady=(8, 0), padx=5)
        self.local_servers_text = tk.Text(self.local_frame, height=3, wrap=tk.WORD)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=(2, 5), padx=5)
        self.local_servers_text.insert("1.0", "http://192.168.8.200:1234\n")
        self._on_ai_mode_change()

        # --- Общие настройки ---
        common_settings_frame = ttk.LabelFrame(
            left_panel, text="Этап 3: Параметры обработки"
        )
        common_settings_frame.pack(fill=tk.X, pady=5)
        self.embed_server_var = tk.StringVar(value="http://192.168.8.200:1234")
        self.embed_model_var = tk.StringVar(value="Qwen/Qwen3-Embedding-8B-GGUF")
        self.top_k_var = tk.IntVar(value=90)
        self.batch_size_var = tk.IntVar(value=20)
        self.retries_var = tk.IntVar(value=5)
        self.temp_var = tk.DoubleVar(value=1.0)
        self.max_tokens_var = tk.IntVar(value=4096)

        self._create_entry_row(
            common_settings_frame, "Сервер эмбеддингов:", self.embed_server_var
        )
        self._create_entry_row(
            common_settings_frame, "Модель эмбеддингов:", self.embed_model_var
        )
        self._create_entry_row(
            common_settings_frame, "Кандидатов для LLM (Top-K):", self.top_k_var
        )
        self._create_entry_row(
            common_settings_frame, "Размер порции для LLM:", self.batch_size_var
        )
        self._create_entry_row(
            common_settings_frame, "Макс. попыток API:", self.retries_var
        )
        self._create_entry_row(
            common_settings_frame, "Температура (0.0-2.0):", self.temp_var
        )
        self._create_entry_row(
            common_settings_frame, "Макс. токенов:", self.max_tokens_var
        )

        # --- Управление и прогресс ---
        controls_frame = ttk.LabelFrame(
            left_panel, text="Этап 4: Управление и прогресс"
        )
        controls_frame.pack(fill=tk.X, pady=5)
        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(
            btn_row, text="Старт", command=self._on_start, style="Accent.TButton"
        )
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(
            btn_row, text="Стоп", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        self.progress_bars = {}
        self.progress_labels = {}
        all_bars = [
            ("embed_spheres", "Векторизация Сфер"),
            ("embed_funcs", "Векторизация Функций"),
            ("classify", "Классификация"),
        ]
        for key, text in all_bars:
            frame = ttk.Frame(controls_frame)
            frame.pack(fill=tk.X, padx=5, pady=(8, 0))
            ttk.Label(frame, text=f"{text}:").pack(side=tk.LEFT)
            self.etr_labels[key] = ttk.Label(frame, text="ETR: --:--")
            self.etr_labels[key].pack(side=tk.RIGHT)
            self.progress_bars[key] = ttk.Progressbar(controls_frame)
            self.progress_bars[key].pack(fill=tk.X, padx=5, pady=(2, 5))

        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        prompts_pane = ttk.Notebook(right_panel)
        prompts_pane.pack(fill=tk.BOTH, expand=True, pady=5)
        self.system_prompt_text = self._create_prompt_tab(
            prompts_pane, "Системный промпт", SYSTEM_PROMPT_TEMPLATE
        )

        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.status_label = ttk.Label(status_frame, text="Готово")
        self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)
        self.log_text = tk.Text(status_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

    def _on_ai_mode_change(self):
        if self.ai_mode_var.get() == "Онлайн":
            self.local_frame.pack_forget()
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            self.online_frame.pack_forget()
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    def _create_file_row(self, parent, label, var, cmd):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    def _create_entry_row(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )

    def _create_prompt_tab(self, notebook, title, content):
        frame = ttk.Frame(notebook, padding=10)
        notebook.add(frame, text=title)
        text = tk.Text(frame, wrap=tk.WORD, height=10)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", content)
        return text

    def _choose_file(self, var, title, is_save=False):
        opts = {
            "title": title,
            "filetypes": [("Excel/CSV", "*.xlsx *.csv"), ("Все файлы", "*.*")],
        }
        path = (
            filedialog.asksaveasfilename(**opts, defaultextension=".xlsx")
            if is_save
            else filedialog.askopenfilename(**opts)
        )
        if path:
            var.set(path)

    def _choose_spheres_file(self):
        self._choose_file(self.spheres_path_var, "Выберите файл со сферами")

    def _choose_functions_file(self):
        self._choose_file(self.functions_path_var, "Выберите файл с функциями")

    def _choose_output_file(self):
        self._choose_file(self.output_path_var, "Укажите итоговый файл", is_save=True)

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("В процессе", "Обработка уже запущена.")
            return

        try:
            config = {
                "spheres_path": self.spheres_path_var.get(),
                "functions_path": self.functions_path_var.get(),
                "output_path": self.output_path_var.get(),
                "ai_mode": self.ai_mode_var.get(),
                "system_prompt": self.system_prompt_text.get("1.0", tk.END),
                "embed_server": self.embed_server_var.get(),
                "embed_model": self.embed_model_var.get(),
                "top_k_filter": self.top_k_var.get(),
                "classification_batch_size": self.batch_size_var.get(),
                "max_retries": self.retries_var.get(),
                "temperature": self.temp_var.get(),
                "max_tokens": self.max_tokens_var.get(),
            }

            if config["ai_mode"] == "Онлайн":
                if not self.api_key_var.get():
                    raise ValueError("В режиме 'Онлайн' нужен OpenAI API Key.")
                config["api_key"] = self.api_key_var.get()
                config["model"] = self.online_model_var.get()
                config["concurrent_requests"] = self.online_concurrent_var.get()
            else:  # Локальная
                local_servers_raw = (
                    self.local_servers_text.get("1.0", tk.END).strip().splitlines()
                )
                config["local_servers"] = [
                    s.strip() for s in local_servers_raw if s.strip()
                ]
                if not config["local_servers"]:
                    raise ValueError(
                        "В режиме 'Локальная' нужен хотя бы один адрес сервера."
                    )
                config["model"] = self.local_model_var.get()
                config["concurrent_requests"] = self.local_concurrent_var.get()

            if not all(
                [
                    config["spheres_path"],
                    config["functions_path"],
                    config["output_path"],
                ]
            ):
                raise ValueError("Необходимо указать все три пути к файлам.")
            if (
                config["concurrent_requests"] <= 0
                or config["top_k_filter"] <= 0
                or config["classification_batch_size"] <= 0
            ):
                raise ValueError(
                    "Параметры параллельных запросов, Top-K и размер порции должны быть > 0."
                )
        except (ValueError, tk.TclError) as e:
            messagebox.showerror("Ошибка валидации", str(e))
            return

        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")
        for bar in self.progress_bars.values():
            bar["value"] = 0
        stop_event_async.clear()
        self._timer_running = True
        self._start_time = time.time()
        self._worker_thread = Thread(
            target=self._worker_main, args=(config,), daemon=True
        )
        self._worker_thread.start()

    def _on_stop(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self.log(
                "Запрошена остановка... Завершаю текущие операции.", to_terminal=True
            )
            stop_event_async.set()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_worker_finished(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._timer_running = False
        if stop_event_async.is_set():
            self.status_label.config(text="Процесс остановлен пользователем.")
        else:
            self.status_label.config(text="Готово.")

    def _tick_ui(self):
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(
                text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}"
            )
        self.root.after(500, self._tick_ui)

    def _worker_main(self, config: dict):
        global progress_callback_async

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(threadName)s - %(message)s",
            handlers=[
                logging.FileHandler("script_errors.log", mode="w", encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )

        def callback_handler(command, data):
            if command == "log":
                self.ui_queue.put(("log", data))
            elif command == "status":
                self.ui_queue.put(("status", data))
            else:  # progress bars
                self.ui_queue.put(("progress", (command, data)))

        progress_callback_async = callback_handler

        try:
            asyncio.run(self._analysis_logic(config))
        except Exception as e:
            logging.critical(f"Произошла критическая ошибка, работа остановлена: {e}")
            import traceback

            logging.error(traceback.format_exc())
            self.log(f"Критическая ошибка: {e}", to_terminal=False)
        finally:
            self.ui_queue.put(("worker_done", None))

    async def _analysis_logic(self, config: dict):
        clients = []
        try:
            self.log("ШАГ 1: ЗАПУСК LLM-КЛАССИФИКАЦИИ", to_terminal=True)
            self.log("Загрузка и подготовка данных...")
            spheres_df = load_df(config["spheres_path"])
            functions_df = load_df(config["functions_path"])

            require_cols(
                spheres_df,
                [SPHERE_NAME_COL, SPHERE_DESC_COL, SPHERE_ACTIVITIES_COL],
                "Сферы",
            )
            require_cols(functions_df, [FUNC_GO_COL, FUNC_TEXT_COL], "Функции")

            if FUNC_ID_COL not in functions_df.columns:
                self.log(
                    f"Колонка '{FUNC_ID_COL}' не найдена. Создаю ее на основе индекса.",
                    to_terminal=True,
                )
                functions_df.reset_index(inplace=True)
                functions_df.rename(columns={"index": FUNC_ID_COL}, inplace=True)

            # --- ИЗМЕНЕНИЕ: Разделение функций по наличию данных в 'Sphere_3' ---
            df_already_processed = pd.DataFrame()
            df_to_process = pd.DataFrame()

            if "Sphere_3" in functions_df.columns:
                functions_df["Sphere_3"] = (
                    functions_df["Sphere_3"].astype(str).str.strip()
                )
                # Считаем пустыми '', 'nan', 'None'
                functions_df["Sphere_3"].replace(
                    ["", "nan", "None"], pd.NA, inplace=True
                )

                df_already_processed = functions_df[
                    functions_df["Sphere_3"].notna()
                ].copy()
                df_to_process = functions_df[functions_df["Sphere_3"].isna()].copy()

                if not df_already_processed.empty:
                    self.log(
                        f"Найдено {len(df_already_processed)} функций с уже заполненной 'Sphere_3'. Они будут пропущены.",
                        to_terminal=True,
                    )
            else:
                self.log(
                    "Колонка 'Sphere_3' не найдена. Все функции будут обработаны.",
                    to_terminal=True,
                )
                df_to_process = functions_df.copy()

            if df_to_process.empty:
                self.log(
                    "Не найдено новых функций для обработки. Процесс завершен.",
                    to_terminal=True,
                )
                # Просто сохраняем исходный файл, так как делать нечего
                functions_df.to_excel(config["output_path"], index=False)
                return
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            sphere_rows = spheres_df.to_dict("records")
            # --- ИЗМЕНЕНИЕ: Используем отфильтрованный DataFrame ---
            function_rows = df_to_process.to_dict("records")
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            sphere_exact, sphere_norm = build_sphere_indexes(sphere_rows)

            if config["ai_mode"] == "Онлайн":
                client = openai.AsyncOpenAI(api_key=config["api_key"])
                clients.append(client)
                semaphore = asyncio.Semaphore(config["concurrent_requests"])
                self.log(f"Режим: OpenAI. Модель: {config['model']}", to_terminal=True)
            else:
                num_servers = len(config["local_servers"])
                concurrency_per_server = max(
                    1, config["concurrent_requests"] // num_servers
                )
                self.log(
                    f"Режим: Локальный. Серверов: {num_servers}. Запросов на сервер: {concurrency_per_server}",
                    to_terminal=True,
                )
                for url in config["local_servers"]:
                    client = openai.AsyncOpenAI(
                        base_url=prepare_api_base_url(url), api_key="not-needed"
                    )
                    clients.append(client)
                semaphore = asyncio.Semaphore(config["concurrent_requests"])
            client_cycle = itertools.cycle(clients)

            embed_client = openai.AsyncOpenAI(
                base_url=prepare_api_base_url(config["embed_server"]),
                api_key="not-needed",
            )
            embed_semaphore = asyncio.Semaphore(32)

            self.log(
                f"Получение эмбеддингов для {len(sphere_rows)} Сфер...",
                to_terminal=True,
            )
            progress_callback_async("reset", ("embed_spheres", len(sphere_rows)))
            sphere_corpus = [
                f"Сфера: {r.get(SPHERE_NAME_COL, '')}. Описание: {r.get(SPHERE_DESC_COL, '')}. Деятельность: {r.get(SPHERE_ACTIVITIES_COL, '')}"
                for r in sphere_rows
            ]
            sphere_embeddings_list = []
            for i in range(0, len(sphere_corpus), 64):
                if stop_event_async.is_set():
                    return
                batch_texts = sphere_corpus[i : i + 64]
                batch_embeds = await async_get_embedding_batch(
                    embed_client, config["embed_model"], batch_texts, embed_semaphore
                )
                if batch_embeds:
                    sphere_embeddings_list.extend(batch_embeds)
                progress_callback_async("embed_spheres", len(batch_texts))
            sphere_embeddings = np.array(sphere_embeddings_list)

            self.log(
                f"Получение эмбеддингов для {len(function_rows)} Функций...",
                to_terminal=True,
            )
            progress_callback_async("reset", ("embed_funcs", len(function_rows)))
            func_corpus = [
                f"Орган: {r.get(FUNC_GO_COL, '')}. Функция: {r.get(FUNC_TEXT_COL, '')}"
                for r in function_rows
            ]
            function_embeddings = []
            for i in range(0, len(func_corpus), 64):
                if stop_event_async.is_set():
                    return
                batch_texts = func_corpus[i : i + 64]
                batch_embeds = await async_get_embedding_batch(
                    embed_client, config["embed_model"], batch_texts, embed_semaphore
                )
                if batch_embeds:
                    function_embeddings.extend(batch_embeds)
                else:
                    function_embeddings.extend([None] * len(batch_texts))
                progress_callback_async("embed_funcs", len(batch_texts))

            await embed_client.close()
            self.log("Векторизация завершена.", to_terminal=True)

            self.log(
                f"Начинаем классификацию {len(function_rows)} функций...",
                to_terminal=True,
            )
            progress_callback_async("reset", ("classify", len(function_rows)))

            q = asyncio.Queue()
            for i, item in enumerate(function_rows):
                q.put_nowait((i, item, function_embeddings[i]))

            results = [None] * len(function_rows)

            tasks = [
                asyncio.create_task(
                    worker_task(
                        next(client_cycle),
                        semaphore,
                        config,
                        q,
                        results,
                        sphere_rows,
                        sphere_embeddings,
                        set(sphere_exact.keys()),
                        sphere_norm,
                    )
                )
                for _ in range(config["concurrent_requests"])
            ]
            await q.join()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            if stop_event_async.is_set():
                self.log("Процесс остановлен.", to_terminal=True)
                return

            self.log("Обработка результатов классификации...")
            output_rows = []
            for i, func_row in enumerate(function_rows):
                result_item = results[i]
                sphere_name = "NO_MATCH"
                if result_item and result_item.get("status") == "OK":
                    sphere_name = result_item.get("assigned_name", "ERROR")
                elif result_item:
                    sphere_name = result_item.get("status", "UNKNOWN_ERROR")
                new_row = func_row.copy()
                new_row["Sphere"] = sphere_name
                output_rows.append(new_row)

            output_df = pd.DataFrame(output_rows)
            self.log("ШАГ 1 завершен.", to_terminal=True)

            # --- ИЗМЕНЕНИЕ: Объединение старых и новых результатов ---
            self.log("Объединение ранее обработанных и новых результатов...")
            combined_df = pd.concat(
                [df_already_processed, output_df], ignore_index=True
            )
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            self.log(
                "ШАГ 2: ДОБАВЛЕНИЕ ИЕРАРХИЧЕСКИХ СФЕР (Sphere_2, Sphere_3)",
                to_terminal=True,
            )
            # --- ИЗМЕНЕНИЕ: Применяем иерархию к объединенному DataFrame ---
            # Эта функция перезапишет Sphere_2 и Sphere_3 для всех строк, обеспечивая консистентность
            final_df = add_hierarchy_spheres(combined_df)

            # --- ИЗМЕНЕНИЕ: Восстановление исходного порядка строк ---
            if FUNC_ID_COL in functions_df.columns and FUNC_ID_COL in final_df.columns:
                try:
                    # Убираем дубликаты ID из combined_df, если они вдруг появились, оставляя последнюю (обновленную) запись
                    final_df.drop_duplicates(
                        subset=[FUNC_ID_COL], keep="last", inplace=True
                    )
                    final_df = (
                        final_df.set_index(FUNC_ID_COL)
                        .loc[functions_df[FUNC_ID_COL]]
                        .reset_index()
                    )
                except KeyError:
                    self.log(
                        "Не удалось полностью восстановить исходный порядок строк. Результат может быть отсортирован иначе.",
                        to_terminal=True,
                    )
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            self.log(
                f"Сохранение итогового результата в файл '{config['output_path']}'...",
                to_terminal=True,
            )
            final_df.to_excel(config["output_path"], index=False)

            self.log("Все готово! Итоговый файл успешно сохранен.", to_terminal=True)

        except Exception as e:
            self.log(f"Ошибка в процессе анализа: {e}", to_terminal=True)
        finally:
            for client in clients:
                await client.close()


if __name__ == "__main__":
    if openai is None:
        sys.exit(1)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    root = tk.Tk()
    app = SphereClassifierApp(root)
    root.mainloop()
