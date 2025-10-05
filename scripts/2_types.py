# -*- coding: utf-8 -*-
"""
Комплексный анализатор типов государственных функций v4.5 (Дополнено)
======================================================================================
Архитектура v4.5 (Дополнено):
- ДОПОЛНЕНИЕ: Скрипт теперь проверяет наличие колонки 'Type' во входном файле.
  Если колонка существует, анализируются только те строки, где значение в ней пустое.
  Строки с уже заполненным типом игнорируются и переносятся в итоговый файл без изменений.
- ИСПРАВЛЕНИЕ: [CRITICAL] В функции _call_api_with_backoff имя параметра 'max_tokens'
  заменено на 'max_completion_tokens' в соответствии с последними обновлениями OpenAI API.
  Это устраняет ошибку "400 Bad Request" с сообщением "Unsupported parameter".
- ИСПРАВЛЕНИЕ: Устранена ошибка KeyError, возникавшая при форматировании системного
  промпта из-за конфликта фигурных скобок в примерах JSON.
- INFO: Сохранены улучшения по обработке ошибок API и парсингу JSON из предыдущих версий.
"""
import re
import os
import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Dict, Tuple, Any, Optional
import logging
import sys
import time
import asyncio
import threading
import queue
from dataclasses import dataclass
import itertools
import json

# --- ЗАВИСИМОСТИ ---
try:
    import torch
except ImportError:
    print("="*80); print("ОШИБКА: Не найдена библиотека torch."); print("Пожалуйста, установите ее командой: pip install torch"); print("="*80)
    torch = None

try:
    import sv_ttk
except ImportError:
    print("="*80); print("ПРЕДУПРЕЖДЕНИЕ: Не найдена тема оформления 'sv-ttk'."); print("Интерфейс будет стандартным. Для улучшения вида установите тему: pip install sv-ttk"); print("="*80)
    sv_ttk = None

try:
    import openai
except ImportError:
    print("="*80); print("ОШИБКА: Не найдена библиотека openai."); print("Пожалуйста, установите ее командой: pip install openai"); print("="*80)
    openai = None
    exit()

# ---- ПРОВЕРКА ВЕРСИИ OPENAI ----
try:
    from importlib.metadata import version
    openai_version = version('openai')
    if tuple(map(int, openai_version.split('.'))) < (1, 0, 0):
        raise ImportError(f"Устаревшая версия библиотеки OpenAI ({openai_version}). Требуется >= 1.0.0")
except ImportError as e:
    error_message = (
        f"ОШИБКА: {e}\n\n"
        "Для работы этого скрипта требуется версия openai 1.0.0 или новее.\n\n"
        "Пожалуйста, обновите ее, выполнив в терминале команду:\n"
        "pip install --upgrade openai"
    )
    print("="*80); print(error_message); print("="*80)
    try:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("Ошибка версии OpenAI", error_message); root.destroy()
    except tk.TclError: pass
    exit()
except Exception as e:
    print(f"Не удалось проверить версию OpenAI: {e}")


# --- Конфигурация по умолчанию ---
DEFAULT_NEEDS_REVIEW_LABEL = "НЕОПРЕДЕЛЕНО"
ALL_CATEGORIES = ["Стратегические", "Регулятивные", "Реализационные", "Контрольные", "Общие"]
DEFAULT_EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-8B-GGUF"
DEFAULT_EMBEDDING_SERVER = "http://localhost:1234"
DEFAULT_EMBEDDING_BATCH_SIZE = 64

# ---- Онлайн конфиг по умолчанию ----
DEFAULT_ONLINE_MODEL = "gpt-4o-mini"
DEFAULT_ONLINE_CONCURRENT = 30
DEFAULT_ONLINE_RETRIES = 5
DEFAULT_ONLINE_TEMP = 1.0
DEFAULT_ONLINE_MAX_TOKENS = 15

# ---- Локальный конфиг по умолчанию ----
DEFAULT_LOCAL_MODEL = "локальная-модель/имя-gguf"
DEFAULT_LOCAL_CONCURRENT = 4
DEFAULT_LOCAL_RETRIES = 3
DEFAULT_LOCAL_TEMP = 1.0
DEFAULT_LOCAL_MAX_TOKENS = 256


# ---- Константы для колонок и статусов ----
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


# --- Системные промпты по умолчанию ---
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

# --- Глобальные переменные для асинхронной части ---
progress_callback_async = None
stop_event_async = threading.Event()

# ---------------------------
# (Вспомогательные утилиты)
# ---------------------------
def prepare_api_base_url(url: str) -> str:
    """Готовит URL для OpenAI-совместимого API."""
    url = url.strip().rstrip('/')
    if not url: return url
    if not (url.startswith("http://") or url.startswith("https://")): url = "http://" + url
    if not url.endswith('/v1'): url += "/v1"
    return url

def read_any(path: str) -> pd.DataFrame:
    """Читает данные из CSV или XLSX файла."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Файл не найден: {path}")
    if path.lower().endswith('.xlsx'):
        return pd.read_excel(path)
    if path.lower().endswith('.csv'):
        try: return pd.read_csv(path, sep=',')
        except (pd.errors.ParserError, ValueError):
            try: return pd.read_csv(path, sep=';')
            except (pd.errors.ParserError, ValueError):
                return pd.read_csv(path, sep=None, engine='python')
    raise ValueError(f"Неподдерживаемый формат файла: {path}")

# ---------------------------------------------
# Основная логика обработки данных (асинхронная)
# ---------------------------------------------

async def _call_api_with_backoff(client: openai.AsyncOpenAI, model: str, messages: List[dict], temperature: float, semaphore: asyncio.Semaphore, max_tokens: int):
    """Внутренняя асинхронная функция для вызова API с контролем нагрузки, остановкой и детальной обработкой ошибок."""
    try:
        async with semaphore:
            if stop_event_async.is_set(): return "ОСТАНОВЛЕНО"
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_tokens  # <-- ИСПРАВЛЕНИЕ: Переименован параметр API
            )
            return resp.choices[0].message.content.strip()
    except openai.APIStatusError as e:
        error_details = f"Код: {e.status_code}. Ответ: {e.response.text}"
        logging.error(f"Ошибка статуса API: {error_details}")
        return f"ОШИБКА API: {error_details}"
    except openai.APIConnectionError as e:
        logging.error(f"Ошибка подключения к API: {e.__cause__}")
        return f"ОШИБКА ПОДКЛЮЧЕНИЯ: {e.__cause__}"
    except openai.RateLimitError as e:
        logging.error(f"Превышен лимит запросов к API: {e}")
        return "ОШИБКА API: Превышен лимит запросов."
    except openai.AuthenticationError as e:
        logging.error(f"Ошибка аутентификации API: {e}")
        return "ОШИБКА API: Неверный ключ API."
    except Exception as e:
        logging.error(f"Неизвестная ошибка вызова API: {e}", exc_info=True)
        return f"ОШИБКА API: {e}"

async def async_get_embeddings(client: openai.AsyncOpenAI, model: str, texts: List[str], semaphore: asyncio.Semaphore) -> Optional[List[List[float]]]:
    """Асинхронно получает эмбеддинги для списка текстов."""
    try:
        async with semaphore:
            if stop_event_async.is_set(): return None
            resp = await client.embeddings.create(model=model, input=texts, timeout=120.0)
            return [item.embedding for item in resp.data]
    except Exception as e:
        print(f"Ошибка получения эмбеддингов: {e}", flush=True)
        return None

def _parse_json_verdict(response_text: str) -> str:
    """Извлекает вердикт из JSON-ответа модели с повышенной надежностью."""
    try:
        # Ищем JSON-объект в тексте
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            parsed_json = json.loads(json_match.group(0))
            # Убедимся, что результат парсинга - это словарь
            if isinstance(parsed_json, dict):
                return parsed_json.get("verdict", "").strip()
            else:
                # Логируем случай, когда JSON валиден, но не является объектом
                logging.warning(f"Ожидался JSON-объект, но получен другой тип: {type(parsed_json)}. Ответ: {response_text}")
                return ""
        return ""
    except (json.JSONDecodeError, AttributeError):
        # Ошибка парсинга JSON или доступа к атрибутам
        return ""

async def call_ai_1_for_classification(
        client: openai.AsyncOpenAI, row: pd.Series, semaphore: asyncio.Semaphore,
        prompts: dict, model: str, temperature: float, max_tokens: int,
        suggestions: Optional[List[str]] = None,
        previous_ai2_reason: str = ""
) -> Tuple[str, str, str]:
    """Асинхронный ИИ-Классификатор (ИИ1)."""
    if stop_event_async.is_set(): return row[COL_ID], "ОСТАНОВЛЕНО", ""
    text = row[COL_TEXT]

    prompt_template = prompts['refinement'] if previous_ai2_reason else prompts['initial']

    suggestion_text = ""
    if suggestions:
        unique_suggestions = sorted(list(set(suggestions)))
        suggestion_text = (
            "\n\nSIMILARITY ANALYSIS: Based on semantic proximity to verified examples, "
            f"the most likely types are: {', '.join(unique_suggestions)}. "
            "Please consider this information when making your verdict."
        )

    system_prompt = prompt_template.format(embedding_suggestions=suggestion_text)

    response_text = await _call_api_with_backoff(
        client, model,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": text}],
        temperature=temperature, semaphore=semaphore, max_tokens=max_tokens
    )

    if progress_callback_async: progress_callback_async('ai1', 1)
    if response_text == "ОСТАНОВЛЕНО": return row[COL_ID], response_text, ""

    verdict = _parse_json_verdict(response_text).capitalize()
    if not verdict or verdict not in ALL_CATEGORIES:
        verdict = DEFAULT_NEEDS_REVIEW_LABEL
    return row[COL_ID], verdict, ""

async def call_ai_2_for_verification(client: openai.AsyncOpenAI, row: pd.Series, semaphore: asyncio.Semaphore, system_prompt: str, model: str, temperature: float, max_tokens: int) -> Tuple[str, str, str]:
    """Асинхронный ИИ-Верификатор (ИИ2)."""
    if stop_event_async.is_set(): return row[COL_ID], "ОСТАНОВЛЕНО", ""
    text = row[COL_TEXT]
    proposed_type = row[COL_TYPE]
    prompt = f"Function:\n{text}\n\nProposed type: {proposed_type}"

    response_text = await _call_api_with_backoff(
        client, model,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        temperature=temperature, semaphore=semaphore, max_tokens=max_tokens
    )

    if progress_callback_async: progress_callback_async('ai2', 1)
    if response_text == "ОСТАНОВЛЕНО": return row[COL_ID], response_text, ""

    verdict_from_json = _parse_json_verdict(response_text)
    verdict = "ВЕРНО" if verdict_from_json == "ВЕРНО" else "НЕ_ВЕРНО"
    reason = "Классификация отклонена" if verdict == "НЕ_ВЕРНО" else ""

    return row[COL_ID], verdict, reason

async def call_ai_3_for_final_decision(client: openai.AsyncOpenAI, row: pd.Series, semaphore: asyncio.Semaphore, system_prompt: str, model: str, temperature: float, max_tokens: int) -> Tuple[str, str, str]:
    """Асинхронный ИИ-Арбитр (ИИ3)."""
    if stop_event_async.is_set(): return row[COL_ID], "ОСТАНОВЛЕНО", ""
    prompt = (
        f"INITIAL FUNCTION: {row[COL_TEXT]}\n\n"
        f"---- ANALYSIS ----\n"
        f"AI1 proposed type: {row[COL_TYPE]}\n"
        f"AI2 verdict: DISAGREED\n\n"
        f"---- TASK ----\n"
        f"Analyze the situation and make the final decision."
    )

    response_text = await _call_api_with_backoff(
        client, model,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        temperature=temperature, semaphore=semaphore, max_tokens=max_tokens
    )

    if progress_callback_async: progress_callback_async('ai3', 1)
    if response_text == "ОСТАНОВЛЕНО": return row[COL_ID], response_text, ""

    final_type = _parse_json_verdict(response_text).capitalize()
    if not final_type or final_type not in ALL_CATEGORIES:
        final_type = DEFAULT_NEEDS_REVIEW_LABEL

    return row[COL_ID], final_type, ""

async def process_unresolved_with_ai_loop(client_sem_pairs: List[Tuple[openai.AsyncOpenAI, asyncio.Semaphore]], df_to_process: pd.DataFrame, config: dict, suggestions_map: Dict[Any, List[str]]) -> pd.DataFrame:
    """Итеративно обрабатывает функции, используя цикл согласования ИИ1-ИИ2 и арбитраж ИИ3."""
    client_sem_cycle = itertools.cycle(client_sem_pairs)
    model_name = config['model']
    max_tokens = config['max_tokens']

    for col in [COL_TYPE, COL_LABELED_BY, COL_AI1_REASON, COL_AI2_REASON, COL_AI3_REASON, COL_FINAL_LABEL]:
        if col not in df_to_process.columns:
            df_to_process[col] = ""

    df_to_process[COL_TYPE] = DEFAULT_NEEDS_REVIEW_LABEL
    df_to_process[COL_LABELED_BY] = STATUS_AI_PENDING
    df_to_process[COL_FINAL_LABEL] = DEFAULT_NEEDS_REVIEW_LABEL

    resolved_functions_list = []
    current_unresolved_df = df_to_process.copy()

    if current_unresolved_df.empty: return pd.DataFrame(columns=df_to_process.columns)

    for iteration in range(1, config['max_retries'] + 1):
        if stop_event_async.is_set(): break
        if progress_callback_async: progress_callback_async('status', f"Итерация ИИ №{iteration}/{config['max_retries']}. Функций в работе: {len(current_unresolved_df)}")
        if current_unresolved_df.empty: break

        # --- Этап 1: ИИ1 Классификация ---
        if progress_callback_async: progress_callback_async('reset', ('ai1', len(current_unresolved_df)))
        ai1_tasks = []
        for _, row in current_unresolved_df.iterrows():
            client, semaphore = next(client_sem_cycle)
            ai1_tasks.append(call_ai_1_for_classification(
                client, row, semaphore,
                prompts={'initial': config['prompt1_initial'], 'refinement': config['prompt1_refinement']},
                model=model_name, temperature=config['temperature'], max_tokens=max_tokens,
                suggestions=suggestions_map.get(row[COL_ID]),
                previous_ai2_reason=row.get(COL_AI2_REASON, "")
            ))
        ai1_results = await asyncio.gather(*ai1_tasks)
        if stop_event_async.is_set(): break

        ai1_updates = {id_val: (new_type, reason) for id_val, new_type, reason in ai1_results}
        for idx, row in current_unresolved_df.iterrows():
            new_type, reason = ai1_updates.get(row[COL_ID], (DEFAULT_NEEDS_REVIEW_LABEL, ""))
            current_unresolved_df.loc[idx, COL_TYPE] = new_type
            current_unresolved_df.loc[idx, COL_AI1_REASON] = reason

        # --- Этап 2: ИИ2 Верификация ---
        if progress_callback_async: progress_callback_async('reset', ('ai2', len(current_unresolved_df)))
        ai2_tasks = []
        for _, row in current_unresolved_df.iterrows():
            client, semaphore = next(client_sem_cycle)
            ai2_tasks.append(call_ai_2_for_verification(client, row, semaphore, config['prompt2'], model_name, config['temperature'], max_tokens))
        ai2_results = await asyncio.gather(*ai2_tasks)
        if stop_event_async.is_set(): break

        next_iteration_unresolved_list = []
        for id_val, verdict, reason in ai2_results:
            row_idx_series = current_unresolved_df[current_unresolved_df[COL_ID] == id_val].index
            if row_idx_series.empty: continue
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

    # --- Этап 3: ИИ3 Арбитраж (если остались неразрешенные) ---
    if not stop_event_async.is_set() and not current_unresolved_df.empty:
        if progress_callback_async: progress_callback_async('status', f"Запуск ИИ3 (арбитра) для {len(current_unresolved_df)} функций...")
        if progress_callback_async: progress_callback_async('reset', ('ai3', len(current_unresolved_df)))

        ai3_tasks = []
        for _, row in current_unresolved_df.iterrows():
            client, semaphore = next(client_sem_cycle)
            ai3_tasks.append(call_ai_3_for_final_decision(client, row, semaphore, config['prompt3'], model_name, config['temperature'], max_tokens))
        ai3_results = await asyncio.gather(*ai3_tasks)

        if not stop_event_async.is_set():
            for id_val, final_type, final_reason in ai3_results:
                row_idx_series = current_unresolved_df[current_unresolved_df[COL_ID] == id_val].index
                if row_idx_series.empty: continue
                row_idx = row_idx_series[0]

                current_unresolved_df.loc[row_idx, COL_TYPE] = final_type
                current_unresolved_df.loc[row_idx, COL_AI3_REASON] = final_reason
                current_unresolved_df.loc[row_idx, COL_LABELED_BY] = STATUS_AI_FINALIZED
                current_unresolved_df.loc[row_idx, COL_FINAL_LABEL] = final_type
            resolved_functions_list.extend(current_unresolved_df.to_dict('records'))

    return pd.DataFrame(resolved_functions_list) if resolved_functions_list else pd.DataFrame(columns=df_to_process.columns)

# ---------------------------------------------
# Класс приложения с графическим интерфейсом
# ---------------------------------------------

class FunctionTypologyApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Анализатор типов функций v4.5 (Дополнено)")
        self.root.geometry("1200x950")

        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)

        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        self.stage_start_times: Dict[str, float] = {}
        self.etr_labels: Dict[str, tk.Label] = {}
        self.stage_progress: Dict[str, int] = {}

        self._build_ui()

    def log(self, message: str, to_terminal: bool = False):
        self.ui_queue.put(('log', message))
        if to_terminal: print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def set_progress(self, bar_name: str, value: Any):
        self.ui_queue.put(('progress', (bar_name, value)))

    def set_status(self, text: str):
        self.ui_queue.put(('status', text))

    def _drain_ui_queue(self):
        try:
            while True:
                command, value = self.ui_queue.get_nowait()
                if command == 'status':
                    self.status_label.config(text=str(value)[:200])
                elif command == 'log':
                    self.log_text.config(state="normal")
                    self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {value}\n")
                    self.log_text.config(state="disabled")
                    self.log_text.see(tk.END)
                elif command == 'progress':
                    bar_name, data = value
                    if bar_name == 'reset':
                        bar_key, total = data
                        self.progress_bars[bar_key]['maximum'] = max(1, total)
                        self.progress_bars[bar_key]['value'] = 0
                        self.stage_progress[bar_key] = 0
                        self.stage_start_times[bar_key] = time.time()
                        self.etr_labels[bar_key].config(text="ETR: Вычисление...")
                    else:
                        if bar_name not in self.stage_progress:
                            self.stage_progress[bar_name] = 0
                        self.stage_progress[bar_name] += data

                        current = self.stage_progress[bar_name]
                        total = self.progress_bars[bar_name]['maximum']

                        self.progress_bars[bar_name]['value'] = current

                        if bar_name in self.etr_labels:
                            if current >= total:
                                self.etr_labels[bar_name].config(text="ETR: Завершено")
                            elif current > 2 and bar_name in self.stage_start_times:
                                time_elapsed = time.time() - self.stage_start_times[bar_name]
                                items_per_second = current / time_elapsed
                                items_remaining = total - current
                                if items_per_second > 0:
                                    etr_seconds = items_remaining / items_per_second
                                    etr_min, etr_sec = divmod(int(etr_seconds), 60)
                                    self.etr_labels[bar_name].config(text=f"ETR: {etr_min:02d}:{etr_sec:02d}")
                elif command == 'worker_done':
                    self._on_worker_finished()

        except queue.Empty: pass
        finally: self.root.after(100, self._drain_ui_queue)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=450)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))

        # --- Этап 1: Файлы ---
        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Файлы")
        files_frame.pack(fill=tk.X, pady=5)
        self.gold_path_var = tk.StringVar(); self.input_path_var = tk.StringVar(); self.output_path_var = tk.StringVar()
        self._create_file_row(files_frame, "Золотой стандарт:", self.gold_path_var, self._choose_gold_file)
        self._create_file_row(files_frame, "Файл для обработки:", self.input_path_var, self._choose_input_file)
        self._create_file_row(files_frame, "Сохранить результат в:", self.output_path_var, self._choose_output_file)

        # --- Этап 2: Эмбеддинги ---
        embed_frame = ttk.LabelFrame(left_panel, text="Этап 2: Помощь эмбеддингов (Опционально)")
        embed_frame.pack(fill=tk.X, pady=5)
        self.use_embeddings_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(embed_frame, text="Включить подсказки от эмбеддингов", variable=self.use_embeddings_var).pack(fill=tk.X, padx=5, pady=5)
        self.embed_server_var = tk.StringVar(value=DEFAULT_EMBEDDING_SERVER); self.embed_model_var = tk.StringVar(value=DEFAULT_EMBEDDING_MODEL_NAME)
        self._create_entry_row(embed_frame, "Сервер эмбеддингов:", self.embed_server_var)
        self._create_entry_row(embed_frame, "Модель эмбеддингов:", self.embed_model_var)

        # --- Этап 3: Настройки ИИ ---
        ai_frame = ttk.LabelFrame(left_panel, text="Этап 3: Настройки ИИ")
        ai_frame.pack(fill=tk.X, pady=5)

        self.ai_mode_var = tk.StringVar(value="Онлайн")
        mode_frame = ttk.Frame(ai_frame)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(mode_frame, text="Онлайн (OpenAI API)", variable=self.ai_mode_var, value="Онлайн", command=self._on_ai_mode_change).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="Локальная (LM Studio)", variable=self.ai_mode_var, value="Локальная", command=self._on_ai_mode_change).pack(side=tk.LEFT, padx=10)

        # -- Фрейм для Онлайн настроек --
        self.online_frame = ttk.Frame(ai_frame)
        self.online_frame.pack(fill=tk.X, padx=5, pady=2)

        self.api_key_var = tk.StringVar()
        self.online_model_var = tk.StringVar(value=DEFAULT_ONLINE_MODEL)
        self.online_concurrent_var = tk.IntVar(value=DEFAULT_ONLINE_CONCURRENT)
        self.online_retries_var = tk.IntVar(value=DEFAULT_ONLINE_RETRIES)
        self.online_temp_var = tk.DoubleVar(value=DEFAULT_ONLINE_TEMP)
        self.online_tokens_var = tk.IntVar(value=DEFAULT_ONLINE_MAX_TOKENS)

        self._create_entry_row(self.online_frame, "OpenAI API Key:", self.api_key_var, show="*")
        self._create_entry_row(self.online_frame, "Модель:", self.online_model_var)
        self._create_entry_row(self.online_frame, "Параллельных запросов:", self.online_concurrent_var)
        self._create_entry_row(self.online_frame, "Макс. итераций ИИ:", self.online_retries_var)
        self._create_entry_row(self.online_frame, "Температура (0.0-2.0):", self.online_temp_var)
        self._create_entry_row(self.online_frame, "Макс. токенов:", self.online_tokens_var)

        # -- Фрейм для Локальных настроек --
        self.local_frame = ttk.Frame(ai_frame)

        self.local_model_var = tk.StringVar(value=DEFAULT_LOCAL_MODEL)
        self.local_concurrent_var = tk.IntVar(value=DEFAULT_LOCAL_CONCURRENT)
        self.local_retries_var = tk.IntVar(value=DEFAULT_LOCAL_RETRIES)
        self.local_temp_var = tk.DoubleVar(value=DEFAULT_LOCAL_TEMP)
        self.local_tokens_var = tk.IntVar(value=DEFAULT_LOCAL_MAX_TOKENS)

        self._create_entry_row(self.local_frame, "Модель:", self.local_model_var)
        self._create_entry_row(self.local_frame, "Параллельных запросов:", self.local_concurrent_var)
        self._create_entry_row(self.local_frame, "Макс. итераций ИИ:", self.local_retries_var)
        self._create_entry_row(self.local_frame, "Температура (0.0-2.0):", self.local_temp_var)
        self._create_entry_row(self.local_frame, "Макс. токенов:", self.local_tokens_var)

        ttk.Label(self.local_frame, text="Адреса серверов (каждый с новой строки):").pack(anchor='w', pady=(8,0), padx=5)
        self.local_servers_text = tk.Text(self.local_frame, height=3, wrap=tk.WORD)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=(2,5), padx=5)
        self.local_servers_text.insert("1.0", "http://localhost:1234\n")

        self._on_ai_mode_change()

        # --- Этап 4: Управление и прогресс ---
        controls_frame = ttk.LabelFrame(left_panel, text="Этап 4: Управление и прогресс")
        controls_frame.pack(fill=tk.X, pady=5)
        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(btn_row, text="Старт", command=self._on_start, style="Accent.TButton"); self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(btn_row, text="Стоп", command=self._on_stop, state=tk.DISABLED); self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        self.progress_bars = {}; self.progress_labels = {}
        all_bars = [('embed', 'Эмбеддинги'), ('ai1', 'ИИ1 Классификация'), ('ai2', 'ИИ2 Верификация'), ('ai3', 'ИИ3 Арбитраж')]
        for key, text in all_bars:
            label_frame = ttk.Frame(controls_frame)
            label_frame.pack(fill=tk.X, padx=5, pady=(8,0))
            self.progress_labels[key] = ttk.Label(label_frame, text=f"{text}:")
            self.progress_labels[key].pack(side=tk.LEFT)
            self.etr_labels[key] = ttk.Label(label_frame, text="ETR: --:--")
            self.etr_labels[key].pack(side=tk.RIGHT)
            self.progress_bars[key] = ttk.Progressbar(controls_frame)
            self.progress_bars[key].pack(fill=tk.X, padx=5, pady=(2,5))

        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        prompts_pane = ttk.Notebook(right_panel)
        prompts_pane.pack(fill=tk.BOTH, expand=True, pady=5)
        self.prompt1_text = self._create_prompt_tab(prompts_pane, "Промпт ИИ1 (Классификатор)", SYS_PROMPT_1_TEMPLATE)
        self.prompt1_refine_text = self._create_prompt_tab(prompts_pane, "Промпт ИИ1 (Уточнение)", SYS_PROMPT_1_REFINEMENT_TEMPLATE)
        self.prompt2_text = self._create_prompt_tab(prompts_pane, "Промпт ИИ2 (Верификатор)", SYS_PROMPT_2_TEMPLATE)
        self.prompt3_text = self._create_prompt_tab(prompts_pane, "Промпт ИИ3 (Арбитр)", SYS_PROMPT_3_TEMPLATE)

        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5,0))
        self.status_label = ttk.Label(status_frame, text="Готово"); self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00"); self.timer_label.pack(side=tk.RIGHT)
        self.log_text = tk.Text(status_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(5,0))

    def _on_ai_mode_change(self):
        mode = self.ai_mode_var.get()
        if mode == "Онлайн":
            self.local_frame.pack_forget()
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            self.online_frame.pack_forget()
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    def _create_file_row(self, parent, label, var, cmd):
        row = ttk.Frame(parent); row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    def _create_entry_row(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent); row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

    def _create_prompt_tab(self, notebook, title, content):
        frame = ttk.Frame(notebook, padding=10); notebook.add(frame, text=title)
        text_widget = tk.Text(frame, wrap=tk.WORD, height=10); text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert("1.0", content)
        return text_widget

    def _choose_file(self, var, title, is_save=False):
        opts = {'title': title, 'filetypes': [("Excel/CSV", "*.xlsx *.csv"), ("Все файлы", "*.*")]}
        path = filedialog.asksaveasfilename(**opts, defaultextension=".xlsx") if is_save else filedialog.askopenfilename(**opts)
        if path: var.set(path)

    def _choose_gold_file(self): self._choose_file(self.gold_path_var, "Выберите файл с золотым стандартом")
    def _choose_input_file(self): self._choose_file(self.input_path_var, "Выберите файл для обработки")
    def _choose_output_file(self): self._choose_file(self.output_path_var, "Укажите, куда сохранить результат", is_save=True)

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("В процессе", "Анализ уже запущен.")
            return

        try:
            config = {
                'gold_path': self.gold_path_var.get(), 'input_path': self.input_path_var.get(), 'output_path': self.output_path_var.get(),
                'ai_mode': self.ai_mode_var.get(),
                'prompt1_initial': self.prompt1_text.get("1.0", tk.END),
                'prompt1_refinement': self.prompt1_refine_text.get("1.0", tk.END),
                'prompt2': self.prompt2_text.get("1.0", tk.END),
                'prompt3': self.prompt3_text.get("1.0", tk.END),
                'use_embeddings': self.use_embeddings_var.get(),
                'embed_server': self.embed_server_var.get(),
                'embed_model': self.embed_model_var.get(),
            }

            if config['ai_mode'] == 'Онлайн':
                if not self.api_key_var.get():
                    raise ValueError("В режиме 'Онлайн' необходимо указать OpenAI API Key.")
                config['api_key'] = self.api_key_var.get()
                config['model'] = self.online_model_var.get()
                config['concurrent_requests'] = self.online_concurrent_var.get()
                config['max_retries'] = self.online_retries_var.get()
                config['temperature'] = self.online_temp_var.get()
                config['max_tokens'] = self.online_tokens_var.get()
            else: # Локальная
                local_servers_raw = self.local_servers_text.get("1.0", tk.END).strip().splitlines()
                config['local_servers'] = [s.strip() for s in local_servers_raw if s.strip()]
                if not config['local_servers']:
                    raise ValueError("В режиме 'Локальная' необходимо указать хотя бы один адрес сервера.")
                config['model'] = self.local_model_var.get()
                config['concurrent_requests'] = self.local_concurrent_var.get()
                config['max_retries'] = self.local_retries_var.get()
                config['temperature'] = self.local_temp_var.get()
                config['max_tokens'] = self.local_tokens_var.get()

            if not all([config['gold_path'], config['input_path'], config['output_path']]):
                raise ValueError("Необходимо указать все три пути к файлам.")
            if config['concurrent_requests'] <= 0 or config['max_retries'] <= 0 or config.get('max_tokens', 0) <= 0:
                raise ValueError("Параллельные запросы, макс. итерации и макс. токены должны быть > 0.")
            if not (0.0 <= config['temperature'] <= 2.0):
                raise ValueError("Температура должна быть в диапазоне от 0.0 до 2.0.")
            if config['use_embeddings']:
                if torch is None: raise ValueError("Библиотека 'torch' не найдена.")
                if not config['embed_server']: raise ValueError("Если включена помощь эмбеддингов, необходимо указать адрес сервера.")
        except (ValueError, tk.TclError) as e:
            messagebox.showerror("Ошибка валидации", str(e))
            return

        self.start_btn.config(state=tk.DISABLED); self.stop_btn.config(state=tk.NORMAL)
        self.log_text.config(state="normal"); self.log_text.delete("1.0", tk.END); self.log_text.config(state="disabled")
        for bar in self.progress_bars.values(): bar['value'] = 0
        self._stop_flag.clear(); stop_event_async.clear()
        self._timer_running = True; self._start_time = time.time()
        self._worker_thread = threading.Thread(target=self._worker_main, args=(config,), daemon=True)
        self._worker_thread.start()

    def _on_stop(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self.log("Запрошена остановка... Завершаю текущие операции.", to_terminal=True)
            self._stop_flag.set(); stop_event_async.set()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_worker_finished(self):
        self.start_btn.config(state=tk.NORMAL); self.stop_btn.config(state=tk.DISABLED)
        self._timer_running = False
        if self._stop_flag.is_set(): self.set_status("Процесс остановлен пользователем.")
        else: self.set_status("Готово.")

    def _tick_ui(self):
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(500, self._tick_ui)

    def _worker_main(self, config: dict):
        global progress_callback_async, stop_event_async
        stop_event_async = self._stop_flag
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

        def callback_handler(command, data):
            if command == 'status':
                self.ui_queue.put(('status', data))
            else:
                self.ui_queue.put(('progress', (command, data)))
        progress_callback_async = callback_handler

        def run_async_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try: loop.run_until_complete(self._analysis_logic(config))
            finally: loop.close()
        try: run_async_loop()
        except Exception as e:
            self.log(f"Произошла критическая ошибка в рабочем потоке: {e}", to_terminal=True)
            import traceback; traceback.print_exc()
        finally: self.ui_queue.put(('worker_done', None))

    async def _analysis_logic(self, config: dict):
        clients = []
        client_sem_pairs = []
        try:
            # Экранируем фигурные скобки, чтобы избежать конфликта с .format()
            json_instruction_classifier = "\nNow, analyze the following function. Respond strictly in a JSON format like {{\"verdict\": \"YOUR_VERDICT\"}}. The verdict value MUST be in Russian. For example: {{\"verdict\": \"Регулятивные\"}}."
            json_instruction_auditor = "\nReview the proposed classification. Respond strictly in a JSON format: {{\"verdict\": \"ВЕРНО\"}} or {{\"verdict\": \"НЕ_ВЕРНО\"}}. The verdict value MUST be in Russian."
            json_instruction_arbiter = "\nAnalyze the disputed situation. Respond strictly in a JSON format like {{\"verdict\": \"YOUR_VERDICT\"}}. The verdict value MUST be in Russian. For example: {{\"verdict\": \"Реализационные\"}}."

            config['prompt1_initial'] += json_instruction_classifier
            config['prompt1_refinement'] += json_instruction_classifier.replace("Now, analyze the following function.", "")
            config['prompt2'] += json_instruction_auditor
            config['prompt3'] += json_instruction_arbiter

            if config['ai_mode'] == 'Онлайн':
                client = openai.AsyncOpenAI(api_key=config['api_key'])
                clients.append(client)
                semaphore = asyncio.Semaphore(config['concurrent_requests'])
                client_sem_pairs.append((client, semaphore))
                self.log(f"Режим ИИ: Онлайн. Модель: {config['model']}", to_terminal=True)
            else:
                num_servers = len(config['local_servers'])
                concurrency_per_server = max(1, config['concurrent_requests'] // num_servers)
                self.log(f"Режим ИИ: Локальный. Серверов: {num_servers}. Запросов на сервер: {concurrency_per_server}", to_terminal=True)
                for url in config['local_servers']:
                    client = openai.AsyncOpenAI(base_url=prepare_api_base_url(url), api_key="not-needed")
                    clients.append(client)
                    semaphore = asyncio.Semaphore(concurrency_per_server)
                    client_sem_pairs.append((client, semaphore))

            if not clients: raise ValueError("Не удалось создать ни одного клиента ИИ.")

            df_gold = read_any(config['gold_path'])
            df_gold.dropna(subset=[COL_TEXT, COL_TYPE], inplace=True)
            df_gold_trusted = df_gold[df_gold[COL_LABELED_BY].isin(TRUSTED_STATUSES)].copy()
            if len(df_gold_trusted) < 2:
                raise ValueError("Недостаточно доверенных данных в золотом стандарте (нужно хотя бы 2).")

            self.log("Few-shot-примеры отключены.", to_terminal=True)

            df_inp = read_any(config['input_path'])
            if COL_TEXT not in df_inp.columns:
                raise ValueError(f"В файле для обработки отсутствует обязательная колонка '{COL_TEXT}'.")

            id_was_added = False
            if COL_ID not in df_inp.columns:
                self.log(f"Колонка '{COL_ID}' не найдена. Создаю ее на основе индекса строк.", to_terminal=True)
                df_inp.reset_index(inplace=True); df_inp.rename(columns={'index': COL_ID}, inplace=True); id_was_added = True

            df_inp.dropna(subset=[COL_TEXT], inplace=True)

            # --- ИЗМЕНЕНИЕ: Разделение данных на уже обработанные и те, что требуют анализа ---
            df_already_processed = pd.DataFrame()
            df_to_process_ai = pd.DataFrame()

            # Проверяем, существует ли колонка 'Type'
            if 'Type' in df_inp.columns:
                # Приводим пустые строки и прочие "пустые" значения к pd.NA для统一обработки
                df_inp['Type'] = df_inp['Type'].apply(lambda x: str(x).strip())
                df_inp['Type'].replace(['', 'nan', 'None'], pd.NA, inplace=True)

                # Отбираем строки, где 'Type' уже заполнен
                df_already_processed = df_inp[df_inp['Type'].notna()].copy()

                # Отбираем строки, где 'Type' пуст
                df_to_process_ai = df_inp[df_inp['Type'].isna()].copy()

                if not df_already_processed.empty:
                    self.log(f"Найдено {len(df_already_processed)} строк с уже заполненным типом. Они будут пропущены.", to_terminal=True)
            else:
                # Если колонки 'Type' нет, все строки отправляются на обработку
                self.log("Колонка 'Type' не найдена во входном файле. Все строки будут обработаны.", to_terminal=True)
                df_to_process_ai = df_inp.copy()
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            if df_to_process_ai.empty:
                self.log("В файле нет новых функций для обработки. Анализ завершен.", to_terminal=True)
                df_inp.to_excel(config['output_path'], index=False)
                return

            suggestions_map = {}
            if config['use_embeddings']:
                self.log("--- ЭТАП ПОМОЩИ ЭМБЕДДИНГОВ АКТИВИРОВАН ---", to_terminal=True)
                embed_client = openai.AsyncOpenAI(base_url=prepare_api_base_url(config['embed_server']), api_key="not-needed")
                embed_semaphore = asyncio.Semaphore(config['concurrent_requests'])
                try:
                    self.log(f"Получение эмбеддингов для {len(df_gold_trusted)} функций из золотого стандарта...", to_terminal=True)
                    progress_callback_async('reset', ('embed', len(df_gold_trusted)))
                    gold_texts = df_gold_trusted[COL_TEXT].fillna("").astype(str).tolist()
                    gold_labels = df_gold_trusted[COL_TYPE].tolist()
                    gold_batches = [gold_texts[i:i + DEFAULT_EMBEDDING_BATCH_SIZE] for i in range(0, len(gold_texts), DEFAULT_EMBEDDING_BATCH_SIZE)]
                    gold_embeddings = []
                    for batch in gold_batches:
                        if self._stop_flag.is_set(): break
                        result = await async_get_embeddings(embed_client, config['embed_model'], batch, embed_semaphore)
                        if result: gold_embeddings.extend(result)
                        progress_callback_async('embed', len(batch))

                    if not gold_embeddings: raise ValueError("Не удалось получить эмбеддинги для золотого стандарта. Проверьте сервер.")
                    gold_embeddings_tensor = torch.nn.functional.normalize(torch.tensor(gold_embeddings, dtype=torch.float32), p=2, dim=1)
                    self.log(f"Успешно получено {len(gold_embeddings)} эмбеддингов для стандарта.", to_terminal=True)

                    total_to_embed = len(df_to_process_ai)
                    self.log(f"Получение эмбеддингов для {total_to_embed} функций из входного файла...", to_terminal=True)
                    input_texts = df_to_process_ai[COL_TEXT].fillna("").astype(str).tolist()
                    input_ids = df_to_process_ai[COL_ID].tolist()
                    input_embeddings = []
                    input_batches = [input_texts[i:i + DEFAULT_EMBEDDING_BATCH_SIZE] for i in range(0, len(input_texts), DEFAULT_EMBEDDING_BATCH_SIZE)]
                    progress_callback_async('reset', ('embed', total_to_embed))
                    for batch in input_batches:
                        if self._stop_flag.is_set(): break
                        result = await async_get_embeddings(embed_client, config['embed_model'], batch, embed_semaphore)
                        if result: input_embeddings.extend(result)
                        progress_callback_async('embed', len(batch))

                    if not input_embeddings:
                        self.log("ПРЕДУПРЕЖДЕНИЕ: Не удалось получить эмбеддинги для входного файла. Продолжаю без подсказок.", to_terminal=True)
                    else:
                        self.log(f"Успешно получено {len(input_embeddings)} эмбеддингов для входа.", to_terminal=True)
                        input_embeddings_tensor = torch.nn.functional.normalize(torch.tensor(input_embeddings, dtype=torch.float32), p=2, dim=1)
                        sim_matrix = input_embeddings_tensor @ gold_embeddings_tensor.T
                        _, top_k_indices = torch.topk(sim_matrix, k=2, dim=1)
                        for i, top_indices in enumerate(top_k_indices.tolist()):
                            suggestions_map[input_ids[i]] = [gold_labels[idx] for idx in top_indices]
                        self.log("Карта подсказок на основе семантической близости создана.", to_terminal=True)
                finally:
                    await embed_client.close()

            self.log(f"Найдено {len(df_to_process_ai)} функций для обработки ИИ. Запуск анализа...", to_terminal=True)
            processed_by_ai_df = await process_unresolved_with_ai_loop(client_sem_pairs, df_to_process_ai, config, suggestions_map)

            if self._stop_flag.is_set():
                self.log("Анализ остановлен. Результаты могут быть неполными.", to_terminal=True)
                return

            # --- ИЗМЕНЕНИЕ: Объединение старых и новых результатов ---
            self.log("Формирование итогового файла...", to_terminal=True)

            final_df_list = []

            # Добавляем строки, которые не обрабатывались
            if not df_already_processed.empty:
                final_df_list.append(df_already_processed)

            # Обновляем строки, которые были обработаны, и добавляем их
            if not processed_by_ai_df.empty:
                id_to_results = processed_by_ai_df.set_index(COL_ID).to_dict('index')

                # Создаем копию, чтобы избежать SettingWithCopyWarning
                df_newly_processed = df_to_process_ai.copy()

                # Обновляем колонку 'Type' и другие возможные колонки
                df_newly_processed['Type'] = df_newly_processed[COL_ID].map(lambda x: id_to_results.get(x, {}).get(COL_FINAL_LABEL))
                # Можно добавить и другие колонки с результатами, если нужно
                # df_newly_processed[COL_LABELED_BY] = df_newly_processed[COL_ID].map(lambda x: id_to_results.get(x, {}).get(COL_LABELED_BY))

                final_df_list.append(df_newly_processed)

            if not final_df_list:
                self.log("Нет данных для сохранения.", to_terminal=True)
                return

            # Собираем итоговый DataFrame
            final_result_df = pd.concat(final_df_list, ignore_index=True)

            # Восстанавливаем исходный порядок строк
            if COL_ID in df_inp.columns and COL_ID in final_result_df.columns:
                final_result_df = final_result_df.set_index(COL_ID).loc[df_inp[COL_ID]].reset_index()

            if id_was_added and COL_ID in final_result_df.columns:
                final_result_df = final_result_df.drop(columns=[COL_ID])
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            self.log(f"Сохранение итогового файла в: {config['output_path']}", to_terminal=True)
            final_result_df.to_excel(config['output_path'], index=False)
            self.log(f"Анализ успешно завершен!", to_terminal=True)
        except Exception as e:
            self.log(f"Ошибка в процессе анализа: {e}", to_terminal=True)
            self.ui_queue.put(('status', f"Ошибка: {e}"))
        finally:
            for client in clients: await client.close()

if __name__ == "__main__":
    if openai is None: sys.exit(1)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    root = tk.Tk()
    app = FunctionTypologyApp(root)
    root.mainloop()