import os
import json
import threading
import queue
import time
import re
import sys
import subprocess
import inspect
from pathlib import Path
from collections import defaultdict, namedtuple
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import logging
from logging.handlers import RotatingFileHandler

import numpy as np
import pandas as pd
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import torch
from transformers import AutoModel, AutoTokenizer

import nltk
from nltk.corpus import stopwords
import pymorphy2
import httpx
from openai import (
    OpenAI,
    APIConnectionError,
    APIStatusError,
    RateLimitError,
    AuthenticationError,
)

if not hasattr(inspect, "getargspec"):
    # Python 3.11+ shim for deprecated inspect.getargspec used by pymorphy2.
    ArgSpec = namedtuple("ArgSpec", ["args", "varargs", "keywords", "defaults"])

    def _compat_getargspec(func: Callable[..., Any]) -> ArgSpec:
        spec = inspect.getfullargspec(func)
        return ArgSpec(spec.args, spec.varargs, spec.varkw, spec.defaults)

    inspect.getargspec = _compat_getargspec  # type: ignore[attr-defined]


LOGGER = logging.getLogger("hierarchy_gui")
LOGS_DIR = Path(__file__).resolve().parent / "logs"
LOG_FILE = LOGS_DIR / "hierarchy_gui.log"
if not LOGGER.handlers:
    LOGGER.setLevel(logging.DEBUG)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        LOGS_DIR = Path.cwd()
    LOG_FILE = LOGS_DIR / "hierarchy_gui.log"
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)
    LOGGER.addHandler(file_handler)
    LOGGER.propagate = False

LOG_LEVELS: Dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "SUCCESS": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _emit_log(level: str, message: str) -> None:
    LOGGER.log(LOG_LEVELS.get(level.upper(), logging.INFO), message)


SYSTEM_PROMPT = (
    "System Ты — аналитик функций государственных органов. Используй методологию "
    "«Иерархический анализ функций ГО», версия 1.4. Запрещено выводить исходный код "
    "или задавать вопросы. Обязательные правила: • Колонка «Ссылается на» никогда не "
    "пустая: либо ID функции ЦГО, либо ровно отсутствует.• Никаких цветовых заливок, "
    "выделений шрифтом и прочего форматирования. Алгоритм:\n"
    "1. Прочитай входной Excel (столбцы: ID функции, Центральный ГО, Подведомственный ГО, "
    "Полный текст функции).\n"
    "2. Определи центральный орган (central_org) как единственное значение столбца "
    "«Центральный ГО».\n"
    "3. Предобработай текст и создай эмбеддинги RuBERT-base.\n"
    "4. Для каждой функции ПГО найди максимальное косинусное сходство с функциями ЦГО.\n"
    "5. Если сходство ≥ 0,50 → запиши ID соответствующей функции ЦГО в «Ссылается на»; "
    "иначе запиши отсутствует.\n"
    "6. В строках ЦГО собери список ПГО, сославшихся на данную функцию, и заполни столбец "
    "«Реализуется (только для ЦГО)». У строк ПГО эта ячейка остаётся пустой.\n"
    "7. Создай лист Report со столбцами: ID функции, Полный текст функции, Исполнитель, "
    "Ссылается на, Реализуется (только для ЦГО).\n"
    "8. Создай скрытый лист log и запиши {SubID, CentralID, simScore, topKeywords, "
    "reason} для каждой строки.\n"
    "9. Сохрани Excel-файл и заверши работу без дополнительных сообщений."
)

PERSONA_TITLE = "Аналитик функций ГО"
DEFAULT_MODEL_NAME = "sberbank-ai/ruBert-base"
DEFAULT_THRESHOLD = 0.50
DEFAULT_BATCH_SIZE = 16
DEFAULT_EMBEDDING_WORKERS = 8
DEFAULT_AI_WORKERS = 8
DEFAULT_AI_MODEL = "gpt-4o-mini"
EMBED_MODE_LOCAL = "local"
EMBED_MODE_REMOTE = "remote"
WORKERS_PER_LOCAL_SERVER = 4

DEFAULT_AI_TEMPERATURE = 1.0
DEFAULT_AI_MAX_TOKENS = 150
DEFAULT_AI_SYSTEM_PROMPT = (
    "You are a senior analyst verifying whether a subordinate government function "
    "is correctly linked to a parent (central) government function. Study both "
    "descriptions carefully and decide if the proposed parent function truly covers "
    'the subordinate one. Respond strictly in JSON: {"verdict": "CORRECT"} '
    'if the parent is appropriate, otherwise {"verdict": "NOT_CORRECT"}.'
)

PROFILE_DIR_NAME = "profiles"
DEFAULT_PROFILE_NAME = "default_profile.json"


def ensure_stopwords_loaded() -> None:
    try:
        nltk.data.find("corpora/stopwords")
    except LookupError:
        nltk.download("stopwords", quiet=True)


def cosine_similarity(matrix_a: np.ndarray, matrix_b: np.ndarray) -> np.ndarray:
    a_norms = np.linalg.norm(matrix_a, axis=1, keepdims=True)
    b_norms = np.linalg.norm(matrix_b, axis=1, keepdims=True)
    a_norms[a_norms == 0] = 1e-12
    b_norms[b_norms == 0] = 1e-12
    a_norm = matrix_a / a_norms
    b_norm = matrix_b / b_norms
    return np.matmul(a_norm, b_norm.T)


class TextNormalizer:
    def __init__(self) -> None:
        ensure_stopwords_loaded()
        self._stopwords = set(stopwords.words("russian"))
        self._morph = pymorphy2.MorphAnalyzer()

    def normalize(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        lowered = text.lower()
        cleaned = "".join(ch if ch.isalpha() or ch.isspace() else " " for ch in lowered)
        tokens = [token for token in cleaned.split() if token not in self._stopwords]
        lemmas = []
        for token in tokens:
            try:
                lemma = self._morph.parse(token)[0].normal_form
            except Exception:
                lemma = token
            lemmas.append(lemma)
        return " ".join(lemmas)


class RuBertEmbedder:
    _model_lock = threading.Lock()
    _shared_tokenizer: Optional[AutoTokenizer] = None
    _shared_model: Optional[AutoModel] = None

    def __init__(
        self, model_name: str = DEFAULT_MODEL_NAME, device: Optional[str] = None
    ) -> None:
        self.model_name = model_name
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        with self._model_lock:
            if (
                RuBertEmbedder._shared_tokenizer is None
                or RuBertEmbedder._shared_model is None
            ):
                RuBertEmbedder._shared_tokenizer = AutoTokenizer.from_pretrained(
                    model_name
                )
                RuBertEmbedder._shared_model = AutoModel.from_pretrained(model_name)
        self._tokenizer = RuBertEmbedder._shared_tokenizer
        self._model = RuBertEmbedder._shared_model.to(self.device)
        self._model.eval()

    def encode(
        self, texts: List[str], batch_size: int = DEFAULT_BATCH_SIZE
    ) -> np.ndarray:
        embeddings: List[np.ndarray] = []
        with torch.no_grad():
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                tokens = self._tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                )
                tokens = {k: v.to(self.device) for k, v in tokens.items()}
                outputs = self._model(**tokens)
                hidden = outputs.last_hidden_state
                attention_mask = tokens["attention_mask"].unsqueeze(-1)
                masked_hidden = hidden * attention_mask
                summed = masked_hidden.sum(dim=1)
                counts = attention_mask.sum(dim=1).clamp(min=1)
                mean_pooled = summed / counts
                embeddings.append(mean_pooled.cpu().numpy())
        if not embeddings:
            return np.empty((0, self._model.config.hidden_size))
        return np.vstack(embeddings)


def prepare_api_base_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return cleaned
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "http://" + cleaned
    cleaned = cleaned.rstrip("/")
    if not cleaned.endswith("/v1"):
        cleaned += "/v1"
    return cleaned


@dataclass
class AnalysisConfig:
    input_path: str
    output_dir: str
    col_id: str
    col_superior: str
    col_executor: str
    col_level: str
    col_text: str
    threshold: float
    embed_mode: str
    local_model_name: str
    remote_server_url: str
    remote_model_name: str
    remote_api_key: str
    embed_batch_size: int
    embed_workers: int
    ai_verification_enabled: bool
    verification_mode: str
    ai_system_prompt: str
    ai_temperature: float
    ai_max_tokens: int
    ai_use_json_mode: bool
    ai_model_name: str
    ai_workers: int
    ai_api_key: str
    local_servers: List[str]
    local_ai_model_name: str


@dataclass
class AnalysisResult:
    report_df: pd.DataFrame
    log_df: pd.DataFrame
    metadata: Dict[str, Any]


class HierarchyAnalyzer:
    def __init__(
        self,
        config: AnalysisConfig,
        log_callback: Callable[[Any], None],
        progress_callback: Callable[[int, int], None],
        stop_event: threading.Event,
    ) -> None:
        self.config = config
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.stop_event = stop_event
        self.normalizer = TextNormalizer()
        self.remote_embed_client: Optional[OpenAI] = None
        self.embedder: Optional[RuBertEmbedder] = None
        self.ai_enabled = config.ai_verification_enabled
        self.ai_verification_stats: Dict[str, Any] = {}

        if config.embed_mode == EMBED_MODE_REMOTE:
            self._log(
                f"{PERSONA_TITLE}: Использую внешний сервис эмбеддингов {config.remote_server_url}."
            )
            prepared_url = prepare_api_base_url(config.remote_server_url)
            if not prepared_url:
                raise ValueError("Не указан адрес сервера эмбеддингов.")
            http_timeout = httpx.Timeout(30.0, connect=10.0, read=30.0, write=30.0)
            self.remote_embed_client = OpenAI(
                base_url=prepared_url,
                api_key=config.remote_api_key or "not-needed",
                http_client=httpx.Client(timeout=http_timeout),
            )
            self.active_model_name = config.remote_model_name
        else:
            self._log(
                f"{PERSONA_TITLE}: Загружаю локальную модель эмбеддингов {config.local_model_name}."
            )
            self.embedder = RuBertEmbedder(model_name=config.local_model_name)
            self.active_model_name = config.local_model_name
        self.zero_norm_indices: List[int] = []
        self.embedding_validation_stats: Dict[str, Any] = {}
        self.duplicate_vector_indices: List[int] = []

    def _log(self, message: str, level: str = "INFO") -> None:
        _emit_log(level, message)
        payload = {
            "level": level.upper(),
            "message": message,
            "time": time.strftime("%H:%M:%S"),
        }
        try:
            self.log_callback(payload)
        except Exception:
            self.log_callback(message)  # type: ignore[arg-type]

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("Процесс остановлен пользователем.")

    def _load_dataframe(self) -> pd.DataFrame:
        self._log(f"{PERSONA_TITLE}: Загружаю файл {self.config.input_path}.")
        df = pd.read_excel(self.config.input_path)
        missing = [
            column
            for column in [
                self.config.col_id,
                self.config.col_superior,
                self.config.col_executor,
                self.config.col_level,
                self.config.col_text,
            ]
            if column not in df.columns
        ]
        if missing:
            raise ValueError(f"Не найдены обязательные столбцы: {', '.join(missing)}.")
        return df

    def _sort_by_level(self, df: pd.DataFrame) -> pd.DataFrame:
        level_series = df[self.config.col_level].copy()

        def to_numeric(val: Any) -> float:
            try:
                return float(val)
            except Exception:
                return float("inf")

        numeric_levels = level_series.apply(to_numeric)
        df_sorted = df.assign(_numeric_level=numeric_levels).sort_values(
            by=["_numeric_level", self.config.col_level, self.config.col_id]
        )
        return df_sorted.reset_index(drop=True)

    def _preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        self._log(
            f"{PERSONA_TITLE}: Выполняю лемматизацию и удаление стоп-слов."
        )
        processed_texts = []
        for idx, text in enumerate(df[self.config.col_text].astype(str)):
            self._check_stop()
            normalized = self.normalizer.normalize(text)
            processed_texts.append(normalized)
            if (idx + 1) % 50 == 0:
                self._log(
                    f"{PERSONA_TITLE}: Предобработка выполнена для {idx + 1} записей."
                )
        return df.assign(_processed_text=processed_texts)

    def _embed(self, texts: List[str]) -> np.ndarray:
        self._log(
            f"{PERSONA_TITLE}: Формирую эмбеддинги {self.active_model_name}."
        )
        if self.config.embed_mode == EMBED_MODE_REMOTE:
            embeddings = self._remote_embed_with_workers(texts)
        else:
            if not self.embedder:
                raise ValueError("Локальная модель эмбеддингов не инициализирована.")
            embeddings = self.embedder.encode(
                texts, batch_size=self.config.embed_batch_size
            )
        self._log(
            f"{PERSONA_TITLE}: Получено {embeddings.shape[0]} векторных представлений."
        )
        return embeddings

    def _remote_embed_with_workers(self, texts: List[str]) -> np.ndarray:
        if not self.remote_embed_client:
            raise ValueError("Клиент удалённых эмбеддингов не инициализирован.")

        valid_rows = [
            (idx, text)
            for idx, text in enumerate(texts)
            if isinstance(text, str) and text.strip()
        ]
        if not valid_rows:
            raise ValueError("После предобработки не осталось текстов для эмбеддингов.")

        batches = [
            valid_rows[i : i + self.config.embed_batch_size]
            for i in range(0, len(valid_rows), self.config.embed_batch_size)
        ]
        total_batches = len(batches)
        total_items = len(valid_rows)
        self._log(
            f"{PERSONA_TITLE}: Планирую {total_items} текстов для удалённых эмбеддингов "
            f"в {total_batches} батчах (batch_size={self.config.embed_batch_size}, "
            f"workers={self.config.embed_workers}).",
            level="DEBUG",
        )
        task_queue: "queue.Queue[List[Tuple[int, str]]]" = queue.Queue()
        for batch in batches:
            task_queue.put(batch)

        results: Dict[int, List[float]] = {}
        errors: List[str] = []
        lock = threading.Lock()

        def worker() -> None:
            while True:
                if self.stop_event.is_set():
                    break
                try:
                    batch = task_queue.get_nowait()
                except queue.Empty:
                    break

                indices = [item[0] for item in batch]
                payload = [item[1] for item in batch]
                batch_label = f"{indices[0]}..{indices[-1]}" if len(indices) > 1 else f"{indices[0]}"
                self._log(
                    f"{PERSONA_TITLE}: Воркер {threading.current_thread().name} обрабатывает индексы {batch_label} "
                    f"(размер батча {len(indices)}).",
                    level="DEBUG",
                )
                try:
                    vectors = self._request_remote_embeddings(payload)
                    if vectors is None:
                        with lock:
                            errors.append(
                                f"Не удалось получить эмбеддинги для индексов {indices[:3]}"
                            )
                        continue
                    if len(vectors) != len(indices):
                        with lock:
                            errors.append(
                                f"Размер ответа {len(vectors)} не совпадает с количеством запросов {len(indices)}."
                            )
                        continue
                    with lock:
                        for idx, vector in zip(indices, vectors):
                            results[idx] = vector
                finally:
                    task_queue.task_done()

        threads = []
        worker_count = max(1, self.config.embed_workers)
        for _ in range(worker_count):
            thread = threading.Thread(target=worker, daemon=True)
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()

        if self.stop_event.is_set():
            raise RuntimeError("Процесс остановлен пользователем.")

        missing = {idx for idx, _ in valid_rows} - set(results.keys())
        if missing:
            raise ValueError(
                f"Не удалось получить эмбеддинги для {len(missing)} строк: {sorted(list(missing))[:5]}"
            )
        if not results:
            raise ValueError("Клиент эмбеддингов вернул пустой результат.")
        if errors:
            self._log(
                f"{PERSONA_TITLE}: Предупреждение эмбеддингов — {'; '.join(errors[:3])}.",
                level="WARNING",
            )

        vector_dim = len(next(iter(results.values())))
        embeddings = np.zeros((len(texts), vector_dim), dtype=np.float32)
        for idx, vector in results.items():
            embeddings[idx] = np.asarray(vector, dtype=np.float32)
        empty_indices = [idx for idx, text in enumerate(texts) if not text.strip()]
        if empty_indices:
            self._log(
                f"{PERSONA_TITLE}: {len(empty_indices)} строк с пустым текстом получили нулевые векторы.",
                level="WARNING",
            )
        return embeddings

    def _request_remote_embeddings(
        self, texts: List[str]
    ) -> Optional[List[List[float]]]:
        if not self.remote_embed_client:
            return None
        last_error = ""
        for attempt in range(3):
            if self.stop_event.is_set():
                return None
            try:
                start = time.time()
                self._log(
                    f"{PERSONA_TITLE}: Запрос эмбеддингов (batch={len(texts)}, попытка {attempt + 1}/3).",
                    level="DEBUG",
                )
                response = self.remote_embed_client.embeddings.create(
                    model=self.config.remote_model_name,
                    input=texts,
                )
                elapsed = time.time() - start
                self._log(
                    f"{PERSONA_TITLE}: Эмбеддинги получены за {elapsed:.2f} с (batch={len(texts)}).",
                    level="DEBUG",
                )
                return [item.embedding for item in response.data]
            except Exception as error:
                last_error = str(error)
                self._log(
                    f"{PERSONA_TITLE}: Ошибка запроса эмбеддингов (batch={len(texts)}, попытка {attempt + 1}/3): {error}",
                    level="WARNING",
                )
                time.sleep(1.0)
        self._log(
            f"{PERSONA_TITLE}: Ошибка запроса эмбеддингов — {last_error}",
            level="ERROR",
        )
        return None

    @staticmethod
    def _append_reason(existing: str, addition: str) -> str:
        if not addition:
            return existing
        if not existing:
            return addition
        if addition in existing:
            return existing
        return f"{existing}; {addition}"

    def _run_ai_verification(
        self,
        df_processed: pd.DataFrame,
        best_parent_row_indices: List[Optional[int]],
        best_parent_ids: List[Optional[Any]],
        sim_scores: List[Optional[float]],
        reasons: List[str],
    ) -> None:
        candidates: List[Dict[str, Any]] = []
        candidate_lookup: Dict[int, Dict[str, Any]] = {}
        for idx, parent_idx in enumerate(best_parent_row_indices):
            if parent_idx is None:
                continue
            score = sim_scores[idx]
            if score is None or score < self.config.threshold:
                continue
            child_id = df_processed.iloc[idx][self.config.col_id]
            parent_id = df_processed.iloc[parent_idx][self.config.col_id]
            candidates.append(
                {
                    "child_idx": idx,
                    "parent_idx": parent_idx,
                    "child_id": child_id,
                    "parent_id": parent_id,
                    "score": score,
                }
            )
            candidate_lookup[idx] = candidates[-1]

        total = len(candidates)
        if total == 0:
            self._log(
                f"{PERSONA_TITLE}: AI-верификация не требуется — нет кандидатов, превышающих порог."
            )
            self.ai_verification_stats = {
                "checked": 0,
                "confirmed": 0,
                "rejected": 0,
                "errors": 0,
            }
            return

        self._log(
            f"{PERSONA_TITLE}: Запускаю AI-верификацию {total} пар (порог {self.config.threshold:.2f})."
        )
        try:
            clients = self._build_ai_clients()
        except Exception as error:
            self._log(
                f"{PERSONA_TITLE}: Не удалось инициализировать AI-клиентов: {error}. Пары будут считаться подтверждёнными.",
                level="ERROR",
            )
            self.ai_verification_stats = {
                "checked": total,
                "confirmed": total,
                "rejected": 0,
                "errors": total,
            }
            return

        if not clients:
            self._log(
                f"{PERSONA_TITLE}: Нет доступных AI-клиентов. Пары считаются подтверждёнными.",
                level="WARNING",
            )
            self.ai_verification_stats = {
                "checked": total,
                "confirmed": total,
                "rejected": 0,
                "errors": total,
            }
            return

        task_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        for candidate in candidates:
            task_queue.put(candidate)

        confirmed: Dict[int, str] = {}
        rejected: Dict[int, str] = {}
        errors: Dict[int, str] = {}
        lock = threading.Lock()
        progress_state = {
            "completed": 0,
            "next_checkpoint": max(1, total // 5),
            "lock": threading.Lock(),
        }

        threads: List[threading.Thread] = []
        mode = self.config.verification_mode
        if mode == "online":
            model_name = self.config.ai_model_name
            worker_count = max(1, self.config.ai_workers)
            client = clients[0]
            for _ in range(worker_count):
                thread = threading.Thread(
                    target=self._verification_worker,
                    args=(
                        task_queue,
                        confirmed,
                        rejected,
                        errors,
                        lock,
                        df_processed,
                        client,
                        model_name,
                        progress_state,
                        total,
                    ),
                    daemon=True,
                )
                threads.append(thread)
        else:
            model_name = self.config.local_ai_model_name
            for client in clients:
                for _ in range(WORKERS_PER_LOCAL_SERVER):
                    thread = threading.Thread(
                        target=self._verification_worker,
                        args=(
                            task_queue,
                            confirmed,
                            rejected,
                            errors,
                            lock,
                            df_processed,
                            client,
                            model_name,
                            progress_state,
                            total,
                        ),
                        daemon=True,
                    )
                    threads.append(thread)

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if self.stop_event.is_set():
            raise RuntimeError("Процесс остановлен пользователем.")

        rejected_count = len(rejected)
        error_count = len(errors)
        confirmed_count = len(confirmed)
        assumed_confirmed = total - rejected_count

        for child_idx, message in confirmed.items():
            reason_text = message or "AI-вердикт: CORRECT"
            reasons[child_idx] = self._append_reason(
                reasons[child_idx], f"AI: {reason_text}"
            )
            self._log_ai_decision(
                df_processed, child_idx, candidate_lookup, "CORRECT", reason_text
            )

        for child_idx, message in rejected.items():
            best_parent_ids[child_idx] = None
            update_reason = message or "AI-вердикт: NOT_CORRECT"
            reasons[child_idx] = self._append_reason(
                reasons[child_idx], f"AI: {update_reason}"
            )
            self._log_ai_decision(
                df_processed, child_idx, candidate_lookup, "NOT_CORRECT", update_reason
            )

        for child_idx, message in errors.items():
            reason = (
                message
                if message
                else "AI-проверка не вернула валидный ответ, ссылка сохранена."
            )
            reasons[child_idx] = self._append_reason(
                reasons[child_idx], f"AI: {reason}"
            )
            self._log_ai_decision(
                df_processed, child_idx, candidate_lookup, "ERROR", reason
            )

        self.ai_verification_stats = {
            "checked": total,
            "confirmed": assumed_confirmed,
            "confirmed_llm": confirmed_count,
            "rejected": rejected_count,
            "errors": error_count,
        }
        self._log(
            f"{PERSONA_TITLE}: AI-верификация завершена — подтверждено {assumed_confirmed} (LLM OK: {confirmed_count}), отклонено {rejected_count}, с ошибками {error_count}."
        )

    def _build_ai_clients(self) -> List[OpenAI]:
        mode = self.config.verification_mode
        clients: List[OpenAI] = []
        if mode == "online":
            if not self.config.ai_api_key:
                raise ValueError("Не указан ключ OpenAI API.")
            clients.append(
                OpenAI(
                    api_key=self.config.ai_api_key,
                    http_client=httpx.Client(timeout=60.0),
                )
            )
        else:
            prepared_servers = [
                prepare_api_base_url(server) for server in self.config.local_servers
            ]
            filtered = [server for server in prepared_servers if server]
            if not filtered:
                raise ValueError("Не заданы адреса локальных серверов.")
            for server in filtered:
                clients.append(
                    OpenAI(
                        base_url=server,
                        api_key="not-needed",
                        http_client=httpx.Client(timeout=60.0),
                    )
                )
        return clients

    def _verification_worker(
        self,
        task_queue: "queue.Queue[Dict[str, Any]]",
        confirmed: Dict[int, str],
        rejected: Dict[int, str],
        errors: Dict[int, str],
        lock: threading.Lock,
        df_processed: pd.DataFrame,
        client: OpenAI,
        model_name: str,
        progress_state: Dict[str, Any],
        total: int,
    ) -> None:
        while True:
            if self.stop_event.is_set():
                break
            try:
                item = task_queue.get_nowait()
            except queue.Empty:
                break

            child_idx = item["child_idx"]
            parent_idx = item["parent_idx"]
            child_row = df_processed.iloc[child_idx]
            parent_row = df_processed.iloc[parent_idx]

            status, message = self._get_llm_verdict(
                client=client,
                model=model_name,
                system_prompt=self.config.ai_system_prompt,
                child_executor=str(child_row[self.config.col_executor]),
                child_text=str(child_row[self.config.col_text]),
                parent_executor=str(parent_row[self.config.col_executor]),
                parent_text=str(parent_row[self.config.col_text]),
                use_json_mode=self.config.ai_use_json_mode,
                temperature=self.config.ai_temperature,
                max_tokens=self.config.ai_max_tokens,
                mode=self.config.verification_mode,
            )

            with lock:
                if status == "CORRECT":
                    confirmed[child_idx] = message or ""
                elif status == "NOT_CORRECT":
                    rejected[child_idx] = message or "AI-вердикт: NOT_CORRECT"
                else:
                    errors[child_idx] = message or f"AI-статус: {status}"

            task_queue.task_done()
            self._report_ai_progress(progress_state, total)

    def _report_ai_progress(self, progress_state: Dict[str, Any], total: int) -> None:
        with progress_state["lock"]:
            progress_state["completed"] += 1
            completed = progress_state["completed"]
            checkpoint = progress_state["next_checkpoint"]
            if completed >= checkpoint or completed >= total:
                self._log(
                    f"{PERSONA_TITLE}: AI-верификация {completed}/{total}."
                )
                step = max(1, total // 5)
                progress_state["next_checkpoint"] = min(total, completed + step)

    def _log_ai_decision(
        self,
        df_processed: pd.DataFrame,
        child_idx: int,
        candidate_lookup: Dict[int, Dict[str, Any]],
        verdict: str,
        message: str,
    ) -> None:
        try:
            child_row = df_processed.iloc[child_idx]
        except IndexError:
            return
        child_id = str(child_row.get(self.config.col_id, "")).strip()
        child_executor = str(child_row.get(self.config.col_executor, "")).strip()

        candidate = candidate_lookup.get(child_idx)
        parent_idx = candidate.get("parent_idx") if candidate else None
        parent_row = (
            df_processed.iloc[parent_idx]
            if parent_idx is not None and 0 <= parent_idx < len(df_processed)
            else None
        )
        parent_id = ""
        parent_executor = ""
        if parent_row is not None:
            parent_id = str(parent_row.get(self.config.col_id, "")).strip()
            parent_executor = str(parent_row.get(self.config.col_executor, "")).strip()
        score = candidate.get("score") if candidate else None

        action = {
            "CORRECT": "подтвердил",
            "NOT_CORRECT": "отклонил",
            "ERROR": "не обработал",
        }.get(verdict, verdict.lower())

        child_label = child_id or "<без ID>"
        if child_executor:
            child_label = f"{child_label} ({child_executor})"
        parts: List[str] = [
            f"{PERSONA_TITLE}: AI {action} связь",
            child_label,
        ]
        if parent_id:
            parent_label = parent_id
            if parent_executor:
                parent_label = f"{parent_label} ({parent_executor})"
            parts.append(f"→ {parent_label}")
        if isinstance(score, (int, float)):
            parts.append(f"[sim {score:.3f}]")
        if message:
            parts.append(f"— {message}")
        level_map = {"CORRECT": "SUCCESS", "NOT_CORRECT": "WARNING", "ERROR": "ERROR"}
        level = level_map.get(verdict, "INFO")
        self._log(" ".join(parts), level=level)

    def _get_llm_verdict(
        self,
        client: OpenAI,
        model: str,
        system_prompt: str,
        child_executor: str,
        child_text: str,
        parent_executor: str,
        parent_text: str,
        use_json_mode: bool,
        temperature: float,
        max_tokens: int,
        mode: str,
    ) -> Tuple[str, Optional[str]]:
        if self.stop_event.is_set():
            return "STOPPED", None

        user_prompt = (
            f"Subordinate agency: {child_executor}\n"
            f"Subordinate function:\n{child_text}\n\n"
            f"Proposed parent agency: {parent_executor}\n"
            f"Parent function:\n{parent_text}\n\n"
            "Decide if the parent function fully covers the subordinate function."
        )
        request_params: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if use_json_mode and mode == "online":
            request_params["response_format"] = {"type": "json_object"}

        last_error = "Empty response"
        for _ in range(3):
            if self.stop_event.is_set():
                return "STOPPED", None
            try:
                response = client.chat.completions.create(**request_params)
                content = response.choices[0].message.content or ""
                match = re.search(r"\{.*\}", content, re.DOTALL)
                if not match:
                    last_error = f"Не найден JSON в ответе: {content}"
                    continue
                try:
                    payload = json.loads(match.group(0))
                except json.JSONDecodeError as decode_error:
                    last_error = f"Ошибка JSON: {decode_error}"
                    continue

                verdict = payload.get("verdict")
                explanation = payload.get("reason") or payload.get("explanation")
                if verdict in ("CORRECT", "NOT_CORRECT"):
                    return verdict, explanation
                last_error = f"Неизвестное значение verdict: {verdict}"
            except AuthenticationError as error:
                detail = ""
                if getattr(error, "body", None):
                    detail = error.body.get("message", "")
                return "AUTH_ERROR", detail or str(error)
            except APIStatusError as error:
                status_code = getattr(error, "status_code", "?")
                response_text = (
                    error.response.text if getattr(error, "response", None) else ""
                )
                last_error = f"APIStatusError {status_code}: {response_text}"
            except (APIConnectionError, RateLimitError) as error:
                last_error = str(error)
                time.sleep(1.0)
            except Exception as error:
                last_error = str(error)
        return "ERROR", last_error

    def _validate_embeddings(
        self, df: pd.DataFrame, embeddings: np.ndarray
    ) -> Dict[str, Any]:
        if embeddings.ndim != 2 or embeddings.shape[0] != len(df):
            raise ValueError("Полученные эмбеддинги не соответствуют количеству строк.")

        if not np.isfinite(embeddings).all():
            raise ValueError(
                "Обнаружены некорректные значения (NaN/Inf) в эмбеддингах."
            )

        norms = np.linalg.norm(embeddings, axis=1)
        zero_norm_indices = np.where(norms < 1e-8)[0].tolist()

        rounded = np.round(embeddings, 6)
        seen: Dict[bytes, int] = {}
        duplicate_pairs: List[Tuple[int, int]] = []
        for idx, vector in enumerate(rounded):
            key = np.ascontiguousarray(vector).tobytes()
            if key in seen:
                duplicate_pairs.append((seen[key], idx))
            else:
                seen[key] = idx
        duplicate_indices = sorted({i for pair in duplicate_pairs for i in pair})

        stats = {
            "vector_count": int(embeddings.shape[0]),
            "vector_dim": int(embeddings.shape[1]),
            "min_norm": float(norms.min()) if norms.size else 0.0,
            "max_norm": float(norms.max()) if norms.size else 0.0,
            "avg_norm": float(norms.mean()) if norms.size else 0.0,
            "zero_norm_count": int(len(zero_norm_indices)),
            "duplicate_pair_count": int(len(duplicate_pairs)),
            "duplicate_function_count": int(len(duplicate_indices)),
        }

        self.zero_norm_indices = zero_norm_indices
        self.embedding_validation_stats = stats
        self.duplicate_vector_indices = duplicate_indices

        self._log(
            f"{PERSONA_TITLE}: Валидация эмбеддингов — {stats['vector_count']} векторов, "
            f"размерность {stats['vector_dim']}, средняя норма {stats['avg_norm']:.4f}."
        )
        if zero_norm_indices:
            sample = [df.iloc[idx][self.config.col_id] for idx in zero_norm_indices[:5]]
            self._log(
                f"{PERSONA_TITLE}: Предупреждение — {len(zero_norm_indices)} функций с нулевой нормой вектора "
                f"(например: {', '.join(map(str, sample))}).",
                level="WARNING",
            )
        if duplicate_pairs:
            examples = []
            for first, second in duplicate_pairs[:3]:
                id_a = df.iloc[first][self.config.col_id]
                id_b = df.iloc[second][self.config.col_id]
                examples.append(f"{id_a}↔{id_b}")
            self._log(
                f"{PERSONA_TITLE}: Обнаружено {len(duplicate_pairs)} пар идентичных векторов "
                f"(примеры: {', '.join(examples)}).",
                level="WARNING",
            )
        return stats

    def _iter_levels(self, df: pd.DataFrame) -> List[Tuple[Any, Any]]:
        level_values = df[self.config.col_level].dropna().tolist()

        def sort_key(value: Any) -> Tuple[int, float, str]:
            try:
                numeric = float(value)
                return (0, numeric, str(value))
            except Exception:
                return (1, float("inf"), str(value))

        ordered = sorted(set(level_values), key=sort_key)
        pairs: List[Tuple[Any, Any]] = []
        for idx in range(1, len(ordered)):
            pairs.append((ordered[idx - 1], ordered[idx]))
        return pairs

    def run(self) -> AnalysisResult:
        start_time = time.time()
        self._log(
            f"{PERSONA_TITLE}: Старт анализа. Соблюдаю системный промпт методологии."
        )
        self._log(f"{PERSONA_TITLE}: {SYSTEM_PROMPT}")
        self.zero_norm_indices = []
        self.embedding_validation_stats = {}
        self.duplicate_vector_indices = []

        df = self._load_dataframe()
        df_sorted = self._sort_by_level(df)
        df_processed = self._preprocess(df_sorted)

        embeddings = self._embed(df_processed["_processed_text"].tolist())
        if embeddings.shape[0] != len(df_processed):
            raise ValueError("Количество эмбеддингов не совпадает с количеством строк.")
        self._validate_embeddings(df_processed, embeddings)

        id_col = self.config.col_id
        superior_col = self.config.col_superior
        executor_col = self.config.col_executor
        level_col = self.config.col_level

        df_processed = df_processed.assign(_embedding=list(embeddings))

        sim_scores: List[Optional[float]] = [None] * len(df_processed)
        reasons: List[str] = [""] * len(df_processed)
        best_parent_ids: List[Optional[Any]] = [None] * len(df_processed)
        best_parent_row_indices: List[Optional[int]] = [None] * len(df_processed)

        level_pairs = self._iter_levels(df_processed)
        level_to_indices: Dict[Any, List[int]] = {}
        for idx, level_value in enumerate(df_processed[level_col]):
            level_to_indices.setdefault(level_value, []).append(idx)

        total_children = sum(
            len(level_to_indices.get(child_level, [])) for _, child_level in level_pairs
        )
        processed_children = 0
        self.progress_callback(processed_children, max(total_children, 1))

        for parent_level, child_level in level_pairs:
            parent_indices = level_to_indices.get(parent_level, [])
            child_indices = level_to_indices.get(child_level, [])
            if not parent_indices or not child_indices:
                continue

            parent_embeddings = np.vstack(
                [df_processed.loc[idx, "_embedding"] for idx in parent_indices]
            )
            child_embeddings = np.vstack(
                [df_processed.loc[idx, "_embedding"] for idx in child_indices]
            )
            similarity_matrix = cosine_similarity(child_embeddings, parent_embeddings)

            for local_child_pos, row_idx in enumerate(child_indices):
                self._check_stop()
                child_row = df_processed.loc[row_idx]
                child_superior = child_row[superior_col]
                candidate_pairs = []
                for local_parent_pos, parent_idx in enumerate(parent_indices):
                    parent_row = df_processed.loc[parent_idx]
                    if parent_row[executor_col] == child_superior:
                        candidate_pairs.append(
                            (local_parent_pos, parent_idx, parent_row[id_col])
                        )

                if not candidate_pairs:
                    sim_scores[row_idx] = None
                    reasons[row_idx] = (
                        f"Нет родительских функций на уровне {parent_level} для "
                        f"органа {child_superior}."
                    )
                    processed_children += 1
                    self.progress_callback(processed_children, max(total_children, 1))
                    continue

                best_score = -1.0
                best_parent_idx = None
                for local_parent_pos, parent_idx, parent_id in candidate_pairs:
                    score = similarity_matrix[local_child_pos, local_parent_pos]
                    if score > best_score:
                        best_score = score
                        best_parent_idx = parent_idx

                sim_scores[row_idx] = float(best_score)
                parent_id_value = (
                    df_processed.loc[best_parent_idx, id_col]
                    if best_parent_idx is not None
                    else None
                )
                if (
                    best_parent_idx is not None
                    and best_score >= self.config.threshold
                    and parent_id_value is not None
                ):
                    best_parent_ids[row_idx] = parent_id_value
                    best_parent_row_indices[row_idx] = best_parent_idx
                    reasons[row_idx] = (
                        f"Сходство {best_score:.4f} превышает порог {self.config.threshold:.2f}; "
                        f"выбран родитель {parent_id_value}."
                    )
                else:
                    if parent_id_value is None:
                        reasons[row_idx] = "Не удалось подобрать родительскую функцию."
                    else:
                        reasons[row_idx] = (
                            f"Сходство {best_score:.4f} ниже порога {self.config.threshold:.2f}."
                        )
                processed_children += 1
                self.progress_callback(processed_children, max(total_children, 1))

        if self.ai_enabled:
            self._run_ai_verification(
                df_processed,
                best_parent_row_indices,
                best_parent_ids,
                sim_scores,
                reasons,
            )

        report_df = df_processed.copy()
        report_df["Ссылается на"] = "отсутствует"
        report_df["Реализуется (только для ЦГО)"] = ""

        for idx, parent_id_candidate in enumerate(best_parent_ids):
            if parent_id_candidate:
                report_df.at[idx, "Ссылается на"] = parent_id_candidate
            else:
                report_df.at[idx, "Ссылается на"] = "отсутствует"
                if sim_scores[idx] is None:
                    reasons[idx] = (
                        reasons[idx] or "Нет подходящих родителей на смежном уровне."
                    )

        backlinks: Dict[Any, List[Any]] = defaultdict(list)
        for idx, parent_id_candidate in enumerate(best_parent_ids):
            if parent_id_candidate:
                child_id_value = report_df.iloc[idx][id_col]
                backlinks[parent_id_candidate].append(child_id_value)

        for parent_id, children_ids in backlinks.items():
            parent_positions = report_df.index[report_df[id_col] == parent_id].tolist()
            for pos in parent_positions:
                report_df.at[pos, "Реализуется (только для ЦГО)"] = "; ".join(
                    children_ids
                )

        if self.zero_norm_indices:
            message = (
                "Вектор функции имеет нулевую норму; требуется экспертная проверка."
            )
            for idx in self.zero_norm_indices:
                if 0 <= idx < len(report_df):
                    report_df.at[idx, "Ссылается на"] = "отсутствует"
                    if reasons[idx]:
                        reasons[idx] = f"{reasons[idx]} {message}"
                    else:
                        reasons[idx] = message
                    sim_scores[idx] = None

        if self.duplicate_vector_indices:
            dup_message = "Вектор функции идентичен по содержанию другой записи; сравнение требует проверки."
            for idx in self.duplicate_vector_indices:
                if 0 <= idx < len(report_df):
                    if reasons[idx]:
                        reasons[idx] = f"{reasons[idx]} {dup_message}"
                    else:
                        reasons[idx] = dup_message

        top_level_indices = []
        if level_pairs:
            top_level = level_pairs[0][0]
            top_level_indices = level_to_indices.get(top_level, [])
        for idx in top_level_indices:
            report_df.at[idx, "Ссылается на"] = "отсутствует"
            reasons[idx] = "Базовый уровень иерархии."
            sim_scores[idx] = None

        if not level_pairs:
            for idx in range(len(report_df)):
                report_df.at[idx, "Ссылается на"] = "отсутствует"
                reasons[idx] = reasons[idx] or "Базовый уровень иерархии."
                sim_scores[idx] = None

        missing_level_mask = report_df[level_col].isna()
        for idx in report_df.index[missing_level_mask]:
            report_df.at[idx, "Ссылается на"] = "отсутствует"
            reasons[idx] = "Нет значения уровня; необходима ручная проверка."
            sim_scores[idx] = None

        report_df = report_df.drop(
            columns=["_processed_text", "_embedding", "_numeric_level"],
            errors="ignore",
        )
        log_df = report_df.copy()
        log_df["simScore"] = [
            np.nan if score is None else float(score) for score in sim_scores
        ]
        log_df["reason"] = reasons

        end_time = time.time()
        duration = end_time - start_time
        metadata = {
            "persona": PERSONA_TITLE,
            "system_prompt": SYSTEM_PROMPT,
            "threshold": self.config.threshold,
            "analysis_seconds": round(duration, 2),
            "input_path": self.config.input_path,
            "embed_mode": self.config.embed_mode,
            "embed_model": self.active_model_name,
            "embed_server": self.config.remote_server_url
            if self.config.embed_mode == EMBED_MODE_REMOTE
            else "",
            "embed_batch_size": self.config.embed_batch_size,
            "embed_workers": self.config.embed_workers
            if self.config.embed_mode == EMBED_MODE_REMOTE
            else 0,
            "embedding_validation": json.dumps(
                self.embedding_validation_stats, ensure_ascii=False
            ),
            "ai_enabled": self.ai_enabled,
            "ai_mode": self.config.verification_mode if self.ai_enabled else "",
            "ai_model": (
                self.config.ai_model_name
                if self.ai_enabled and self.config.verification_mode == "online"
                else (self.config.local_ai_model_name if self.ai_enabled else "")
            ),
            "ai_pairs_checked": int(self.ai_verification_stats.get("checked", 0))
            if self.ai_enabled
            else 0,
            "ai_pairs_confirmed": int(self.ai_verification_stats.get("confirmed", 0))
            if self.ai_enabled
            else 0,
            "ai_pairs_llm_confirmed": int(
                self.ai_verification_stats.get("confirmed_llm", 0)
            )
            if self.ai_enabled
            else 0,
            "ai_pairs_rejected": int(self.ai_verification_stats.get("rejected", 0))
            if self.ai_enabled
            else 0,
            "ai_pairs_errors": int(self.ai_verification_stats.get("errors", 0))
            if self.ai_enabled
            else 0,
        }

        self._log(
            f"{PERSONA_TITLE}: Анализ завершен за {duration:.2f} секунд.",
            level="SUCCESS",
        )
        return AnalysisResult(report_df=report_df, log_df=log_df, metadata=metadata)


class HierarchyAnalyzerApp:
    SECTION_PAD = {"padx": 0, "pady": 16}
    CONTROL_PAD = {"padx": 12, "pady": 8}
    INLINE_PAD = {"padx": 8, "pady": 4}

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Иерархический анализ функций")
        self.root.geometry("960x720")
        self.root.minsize(880, 640)

        self._init_style()

        self.log_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.is_running = False
        self.run_start_time: Optional[float] = None
        self.last_output_dir: Optional[str] = None
        self.last_outputs: Dict[str, str] = {}
        self.loaded_profile_path: Optional[str] = None

        self._build_ui()
        self._load_profile(auto=True)
        self.root.after(100, self._poll_queue)

    def _init_style(self) -> None:
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(size=11)
        heading_font = tkfont.nametofont("TkHeadingFont")
        heading_font.configure(size=12, weight="bold")
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(size=11)

        self.root.option_add("*Font", default_font.name)
        self.root.option_add("*TCombobox*Listbox*Font", default_font.name)
        self.root.option_add("*Text*Font", text_font.name)

        style = ttk.Style(self.root)
        self.style = style
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self._heading_font = heading_font
        self._themable_text_widgets: List[tk.Text] = []

        self.palettes = {
            "dark": {
                "accent": "#4F46E5",
                "accent_hover": "#6366F1",
                "accent_pressed": "#4338CA",
                "on_accent": "#FFFFFF",
                "background": "#0B1120",
                "surface": "#111827",
                "surface_active": "#1F2937",
                "surface_pressed": "#0F172A",
                "secondary_bg": "#1F2937",
                "secondary_fg": "#CBD5F5",
                "secondary_hover": "#2D3748",
                "text": "#E2E8F0",
                "text_muted": "#94A3B8",
                "progress_trough": "#1F2937",
                "entry_bg": "#0F172A",
                "scroll_trough": "#111827",
                "success": "#10B981",
                "warning": "#FACC15",
                "error": "#DC2626",
            },
            "light": {
                "accent": "#2563EB",
                "accent_hover": "#1D4ED8",
                "accent_pressed": "#1E40AF",
                "on_accent": "#FFFFFF",
                "background": "#F8FAFC",
                "surface": "#FFFFFF",
                "surface_active": "#E2E8F0",
                "surface_pressed": "#CBD5F5",
                "secondary_bg": "#E2E8F0",
                "secondary_fg": "#1F2937",
                "secondary_hover": "#CBD5F5",
                "text": "#1F2937",
                "text_muted": "#64748B",
                "progress_trough": "#E2E8F0",
                "entry_bg": "#FFFFFF",
                "scroll_trough": "#E5E7EB",
                "success": "#047857",
                "warning": "#CA8A04",
                "error": "#DC2626",
            },
        }

        self.theme_mode = "dark"
        self.current_palette: Dict[str, str] = {}
        self._apply_palette(self.theme_mode)

    def _apply_palette(self, mode: str) -> None:
        palette = self.palettes.get(mode, self.palettes["dark"])
        self.theme_mode = mode
        self.current_palette = palette

        self.accent_color = palette["accent"]
        self.accent_hover = palette["accent_hover"]
        self.accent_pressed = palette["accent_pressed"]
        self.surface_color = palette["surface"]
        self.background_color = palette["background"]
        self.success_color = palette["success"]
        self.warning_color = palette["warning"]
        self.error_color = palette["error"]

        style = self.style
        self.root.configure(bg=self.background_color)

        style.configure("App.TFrame", background=self.background_color)
        style.configure("Card.TFrame", background=self.surface_color, relief="flat")
        style.configure(
            "Card.TLabelframe",
            background=self.surface_color,
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=self.surface_color,
            foreground=palette["text"],
            font=self._heading_font,
        )
        style.configure("TLabel", background=self.surface_color, foreground=palette["text"])
        style.configure("App.TLabel", background=self.background_color, foreground=palette["text"])
        style.configure("TNotebook", background=self.background_color, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 8), foreground=palette["text_muted"])
        style.map(
            "TNotebook.Tab",
            foreground=[("selected", palette["text"])],
            background=[("selected", self.surface_color), ("!selected", self.background_color)],
        )

        style.configure(
            "TButton",
            padding=(10, 8),
            background=self.surface_color,
            foreground=palette["text"],
            focusthickness=1,
            focuscolor=self.accent_color,
        )
        style.map(
            "TButton",
            background=[("active", palette["surface_active"]), ("pressed", palette["surface_pressed"])],
        )
        style.configure(
            "Accent.TButton",
            background=self.accent_color,
            foreground=palette["on_accent"],
            padding=(14, 10),
        )
        style.map(
            "Accent.TButton",
            background=[("active", self.accent_hover), ("pressed", self.accent_pressed)],
            foreground=[("disabled", palette["text_muted"])],
        )
        style.configure(
            "Secondary.TButton",
            background=palette["secondary_bg"],
            foreground=palette["secondary_fg"],
            padding=(12, 8),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", palette["secondary_hover"])],
        )

        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=palette["progress_trough"],
            bordercolor=palette["progress_trough"],
            lightcolor=self.accent_color,
            darkcolor=self.accent_color,
            background=self.accent_color,
        )

        style.configure(
            "Log.Treeview",
            background=self.surface_color,
            foreground=palette["text"],
            fieldbackground=self.surface_color,
            rowheight=28,
        )
        style.configure(
            "Vertical.TScrollbar",
            troughcolor=palette["scroll_trough"],
            bordercolor=palette["scroll_trough"],
            arrowsize=14,
        )

        entry_background = palette["entry_bg"]
        style.configure(
            "TEntry",
            fieldbackground=entry_background,
            foreground=palette["text"],
            insertcolor=palette["text"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=entry_background,
            foreground=palette["text"],
            arrowsize=14,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", entry_background)],
            foreground=[("disabled", palette["text_muted"])],
        )

        self.root.option_add("*TEntry*FieldBackground", entry_background)
        self.root.option_add("*TEntry*Foreground", palette["text"])
        self.root.option_add("*Background", self.background_color)

        self._refresh_text_widgets()
        self._refresh_log_tags()

        if hasattr(self, "theme_toggle"):
            self.theme_toggle.config(text=self._theme_button_label())

    def _refresh_log_tags(self) -> None:
        if not hasattr(self, "log_tree"):
            return
        self.log_tree.tag_configure("INFO", foreground=self.current_palette.get("text", "#FFFFFF"))
        self.log_tree.tag_configure("SUCCESS", foreground=self.success_color)
        self.log_tree.tag_configure("WARNING", foreground=self.warning_color)
        self.log_tree.tag_configure("ERROR", foreground=self.error_color)

    def _refresh_text_widgets(self) -> None:
        palette = getattr(self, "current_palette", None)
        if not palette:
            return
        for widget in getattr(self, "_themable_text_widgets", []):
            try:
                widget.configure(
                    bg=palette["surface"],
                    fg=palette["text"],
                    insertbackground=palette["text"],
                )
            except tk.TclError:
                continue

    def _register_themable_text(self, widget: tk.Text) -> None:
        widget.configure(relief=tk.FLAT, highlightthickness=0, borderwidth=0)
        self._themable_text_widgets.append(widget)
        self._refresh_text_widgets()

    def _theme_button_label(self) -> str:
        return f"Тема: {'Тёмная' if self.theme_mode == 'dark' else 'Светлая'}"

    def _toggle_theme(self) -> None:
        new_mode = "light" if self.theme_mode == "dark" else "dark"
        self._apply_palette(new_mode)

    def _build_ui(self) -> None:
        section_pad = self.SECTION_PAD
        control_pad = self.CONTROL_PAD
        inline_pad = self.INLINE_PAD

        main_frame = ttk.Frame(self.root, padding=24, style="App.TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(main_frame, style="App.TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 18))
        self.theme_toggle = ttk.Button(
            toolbar,
            text=self._theme_button_label(),
            command=self._toggle_theme,
            style="Secondary.TButton",
        )
        self.theme_toggle.pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 18))

        data_tab = ttk.Frame(self.notebook, padding=16, style="App.TFrame")
        embed_tab = ttk.Frame(self.notebook, padding=16, style="App.TFrame")
        ai_tab = ttk.Frame(self.notebook, padding=16, style="App.TFrame")
        log_tab = ttk.Frame(self.notebook, padding=16, style="App.TFrame")

        self.notebook.add(data_tab, text="Данные")
        self.notebook.add(embed_tab, text="Эмбеддинги")
        self.notebook.add(ai_tab, text="AI-проверка")
        self.notebook.add(log_tab, text="Журнал")

        self.input_path_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self.col_id_var = tk.StringVar(value="ID")
        self.col_superior_var = tk.StringVar(value="Вышестоящий ГО")
        self.col_executor_var = tk.StringVar(value="Исполняющий ГО")
        self.col_level_var = tk.StringVar(value="Level")
        self.col_text_var = tk.StringVar(value="FunctionText")
        self.embed_mode_var = tk.StringVar(value=EMBED_MODE_LOCAL)
        self.local_model_var = tk.StringVar(value=DEFAULT_MODEL_NAME)
        self.remote_url_var = tk.StringVar(value="http://localhost:1234")
        self.remote_model_var = tk.StringVar(value="text-embedding-ada-002")
        self.remote_api_key_var = tk.StringVar(value="")
        self.batch_size_var = tk.StringVar(value=str(DEFAULT_BATCH_SIZE))
        self.embed_workers_var = tk.StringVar(value=str(DEFAULT_EMBEDDING_WORKERS))
        self.ai_enabled_var = tk.BooleanVar(value=False)
        self.verification_mode_var = tk.StringVar(value="online")
        self.ai_api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.ai_model_var = tk.StringVar(value=DEFAULT_AI_MODEL)
        self.ai_workers_var = tk.StringVar(value=str(DEFAULT_AI_WORKERS))
        self.local_ai_model_var = tk.StringVar(value="local-model/gguf-model")
        self.ai_temperature_var = tk.StringVar(value=f"{DEFAULT_AI_TEMPERATURE:.1f}")
        self.ai_max_tokens_var = tk.StringVar(value=str(DEFAULT_AI_MAX_TOKENS))
        self.ai_json_mode_var = tk.BooleanVar(value=True)
        self.threshold_var = tk.StringVar(value=f"{DEFAULT_THRESHOLD:.2f}")

        file_frame = ttk.LabelFrame(
            data_tab,
            text="Исходные данные",
            padding=16,
            style="Card.TLabelframe",
        )
        file_frame.pack(fill=tk.X, **section_pad)
        ttk.Label(file_frame, text="Файл Excel:").grid(
            row=0, column=0, sticky=tk.W, **inline_pad
        )
        ttk.Entry(file_frame, textvariable=self.input_path_var, width=60).grid(
            row=0, column=1, sticky=tk.W, **inline_pad
        )
        ttk.Button(file_frame, text="Обзор…", command=self._choose_input_file).grid(
            row=0, column=2, **inline_pad
        )
        ttk.Label(file_frame, text="Каталог выгрузки:").grid(
            row=1, column=0, sticky=tk.W, **inline_pad
        )
        ttk.Entry(file_frame, textvariable=self.output_dir_var, width=60).grid(
            row=1, column=1, sticky=tk.W, **inline_pad
        )
        ttk.Button(file_frame, text="Обзор…", command=self._choose_output_dir).grid(
            row=1, column=2, **inline_pad
        )
        file_frame.grid_columnconfigure(1, weight=1)

        profile_frame = ttk.Frame(data_tab, style="App.TFrame")
        profile_frame.pack(fill=tk.X, **section_pad)
        ttk.Button(
            profile_frame, text="Загрузить профиль…", command=self._load_profile_dialog
        ).pack(side=tk.LEFT, **inline_pad)
        ttk.Button(
            profile_frame, text="Сохранить профиль…", command=self._save_profile_dialog
        ).pack(side=tk.LEFT, **inline_pad)

        columns_frame = ttk.LabelFrame(
            data_tab,
            text="Сопоставление столбцов",
            padding=16,
            style="Card.TLabelframe",
        )
        columns_frame.pack(fill=tk.X, **section_pad)
        ttk.Label(columns_frame, text="ID:").grid(
            row=0, column=0, sticky=tk.W, **inline_pad
        )
        ttk.Entry(columns_frame, textvariable=self.col_id_var, width=25).grid(
            row=0, column=1, **inline_pad
        )
        ttk.Label(columns_frame, text="Вышестоящий ГО:").grid(
            row=0, column=2, sticky=tk.W, **inline_pad
        )
        ttk.Entry(columns_frame, textvariable=self.col_superior_var, width=25).grid(
            row=0, column=3, **inline_pad
        )
        ttk.Label(columns_frame, text="Исполняющий ГО:").grid(
            row=1, column=0, sticky=tk.W, **inline_pad
        )
        ttk.Entry(columns_frame, textvariable=self.col_executor_var, width=25).grid(
            row=1, column=1, **inline_pad
        )
        ttk.Label(columns_frame, text="Уровень:").grid(
            row=1, column=2, sticky=tk.W, **inline_pad
        )
        ttk.Entry(columns_frame, textvariable=self.col_level_var, width=25).grid(
            row=1, column=3, **inline_pad
        )
        ttk.Label(columns_frame, text="Текст функции:").grid(
            row=2, column=0, sticky=tk.W, **inline_pad
        )
        ttk.Entry(columns_frame, textvariable=self.col_text_var, width=58).grid(
            row=2, column=1, columnspan=3, **inline_pad
        )
        columns_frame.grid_columnconfigure(1, weight=1)
        columns_frame.grid_columnconfigure(3, weight=1)

        threshold_frame = ttk.Frame(data_tab, style="App.TFrame")
        threshold_frame.pack(fill=tk.X, **section_pad)
        ttk.Label(
            threshold_frame,
            text="Порог косинусного сходства:",
            style="App.TLabel",
        ).pack(
            side=tk.LEFT, **inline_pad
        )
        ttk.Entry(threshold_frame, textvariable=self.threshold_var, width=8).pack(
            side=tk.LEFT, padx=12
        )

        embed_frame = ttk.LabelFrame(
            embed_tab,
            text="Настройки эмбеддингов",
            padding=16,
            style="Card.TLabelframe",
        )
        embed_frame.pack(fill=tk.BOTH, expand=True, **section_pad)
        ttk.Radiobutton(
            embed_frame,
            text="Локальная модель (RuBERT)",
            variable=self.embed_mode_var,
            value=EMBED_MODE_LOCAL,
            command=self._update_embed_controls,
        ).grid(row=0, column=0, sticky=tk.W, columnspan=2, **inline_pad)
        ttk.Radiobutton(
            embed_frame,
            text="Внешний сервис (OpenAI-совместимый API)",
            variable=self.embed_mode_var,
            value=EMBED_MODE_REMOTE,
            command=self._update_embed_controls,
        ).grid(row=0, column=2, sticky=tk.W, columnspan=2, **inline_pad)
        ttk.Label(embed_frame, text="Локальная модель:").grid(
            row=1, column=0, sticky=tk.W, **inline_pad
        )
        self.local_model_entry = ttk.Entry(
            embed_frame, textvariable=self.local_model_var, width=42
        )
        self.local_model_entry.grid(row=1, column=1, sticky=tk.W, **inline_pad)
        ttk.Label(embed_frame, text="Размер батча:").grid(
            row=1, column=2, sticky=tk.W, **inline_pad
        )
        ttk.Entry(embed_frame, textvariable=self.batch_size_var, width=8).grid(
            row=1, column=3, sticky=tk.W, **inline_pad
        )
        ttk.Label(embed_frame, text="URL сервера:").grid(
            row=2, column=0, sticky=tk.W, **inline_pad
        )
        self.remote_url_entry = ttk.Entry(
            embed_frame, textvariable=self.remote_url_var, width=42
        )
        self.remote_url_entry.grid(row=2, column=1, sticky=tk.W, **inline_pad)
        ttk.Label(embed_frame, text="Модель сервера:").grid(
            row=2, column=2, sticky=tk.W, **inline_pad
        )
        self.remote_model_entry = ttk.Entry(
            embed_frame, textvariable=self.remote_model_var, width=20
        )
        self.remote_model_entry.grid(row=2, column=3, sticky=tk.W, **inline_pad)
        ttk.Label(embed_frame, text="API ключ:").grid(
            row=3, column=0, sticky=tk.W, **inline_pad
        )
        self.remote_api_key_entry = ttk.Entry(
            embed_frame, textvariable=self.remote_api_key_var, width=42, show="*"
        )
        self.remote_api_key_entry.grid(row=3, column=1, sticky=tk.W, **inline_pad)
        ttk.Label(embed_frame, text="Воркеров:").grid(
            row=3, column=2, sticky=tk.W, **inline_pad
        )
        self.embed_workers_entry = ttk.Entry(
            embed_frame, textvariable=self.embed_workers_var, width=8
        )
        self.embed_workers_entry.grid(row=3, column=3, sticky=tk.W, **inline_pad)
        for column in range(4):
            embed_frame.grid_columnconfigure(
                column, weight=1 if column in (1, 3) else 0
            )

        ai_frame = ttk.LabelFrame(
            ai_tab,
            text="AI-верификация",
            padding=16,
            style="Card.TLabelframe",
        )
        ai_frame.pack(fill=tk.BOTH, expand=True, **section_pad)
        ttk.Checkbutton(
            ai_frame,
            text="Включить AI-верификацию",
            variable=self.ai_enabled_var,
            command=self._on_ai_enable_change,
        ).pack(anchor=tk.W, **inline_pad)

        self.ai_mode_frame = ttk.Frame(ai_frame, style="Card.TFrame")
        self.ai_mode_frame.pack(fill=tk.X, **control_pad)
        ttk.Radiobutton(
            self.ai_mode_frame,
            text="Облачный сервис (OpenAI API)",
            variable=self.verification_mode_var,
            value="online",
            command=self._on_verification_mode_change,
        ).pack(side=tk.LEFT, **inline_pad)
        ttk.Radiobutton(
            self.ai_mode_frame,
            text="Локальные серверы (LM Studio)",
            variable=self.verification_mode_var,
            value="local",
            command=self._on_verification_mode_change,
        ).pack(side=tk.LEFT, **inline_pad)

        self.online_frame = ttk.Frame(ai_frame, style="Card.TFrame")
        ttk.Label(self.online_frame, text="API ключ:").grid(
            row=0, column=0, sticky=tk.W, **inline_pad
        )
        self.ai_api_key_entry = ttk.Entry(
            self.online_frame, textvariable=self.ai_api_key_var, width=42, show="*"
        )
        self.ai_api_key_entry.grid(row=0, column=1, sticky=tk.W, **inline_pad)
        ttk.Label(self.online_frame, text="Модель:").grid(
            row=1, column=0, sticky=tk.W, **inline_pad
        )
        self.ai_model_entry = ttk.Entry(
            self.online_frame, textvariable=self.ai_model_var, width=42
        )
        self.ai_model_entry.grid(row=1, column=1, sticky=tk.W, **inline_pad)
        ttk.Label(self.online_frame, text="Воркеров:").grid(
            row=2, column=0, sticky=tk.W, **inline_pad
        )
        self.ai_workers_entry = ttk.Entry(
            self.online_frame, textvariable=self.ai_workers_var, width=12
        )
        self.ai_workers_entry.grid(row=2, column=1, sticky=tk.W, **inline_pad)
        self.online_frame.grid_columnconfigure(1, weight=1)

        self.local_frame = ttk.Frame(ai_frame, style="Card.TFrame")
        ttk.Label(self.local_frame, text="Локальная модель:").grid(
            row=0, column=0, sticky=tk.W, **inline_pad
        )
        self.local_ai_model_entry = ttk.Entry(
            self.local_frame, textvariable=self.local_ai_model_var, width=42
        )
        self.local_ai_model_entry.grid(row=0, column=1, sticky=tk.W, **inline_pad)
        ttk.Label(
            self.local_frame,
            text=f"По {WORKERS_PER_LOCAL_SERVER} потока(ов) на сервер.",
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=inline_pad["padx"], pady=(0, inline_pad["pady"]))
        ttk.Label(self.local_frame, text="Адреса серверов (по одному на строку):").grid(
            row=2, column=0, columnspan=2, sticky=tk.W, padx=inline_pad["padx"], pady=(0, inline_pad["pady"])
        )
        self.local_servers_text = tk.Text(self.local_frame, height=3, width=44)
        self.local_servers_text.grid(
            row=3, column=0, columnspan=2, sticky=tk.W + tk.E, **inline_pad
        )
        self.local_servers_text.insert(tk.END, "http://localhost:1234")
        self._register_themable_text(self.local_servers_text)
        self.local_frame.grid_columnconfigure(0, weight=0)
        self.local_frame.grid_columnconfigure(1, weight=1)

        self.common_ai_frame = ttk.Frame(ai_frame, style="Card.TFrame")
        ttk.Label(self.common_ai_frame, text="Температура:").grid(
            row=0, column=0, sticky=tk.W, **inline_pad
        )
        ttk.Entry(
            self.common_ai_frame, textvariable=self.ai_temperature_var, width=10
        ).grid(row=0, column=1, sticky=tk.W, **inline_pad)
        ttk.Label(self.common_ai_frame, text="Макс. токенов:").grid(
            row=0, column=2, sticky=tk.W, **inline_pad
        )
        ttk.Entry(
            self.common_ai_frame, textvariable=self.ai_max_tokens_var, width=10
        ).grid(row=0, column=3, sticky=tk.W, **inline_pad)
        ttk.Checkbutton(
            self.common_ai_frame,
            text="JSON-ответ",
            variable=self.ai_json_mode_var,
        ).grid(row=0, column=4, sticky=tk.W, **inline_pad)
        for col in range(5):
            self.common_ai_frame.grid_columnconfigure(col, weight=1 if col == 1 else 0)

        self.ai_prompt_frame = ttk.LabelFrame(
            ai_frame,
            text="Системный промпт LLM",
            padding=12,
            style="Card.TLabelframe",
        )
        self.ai_prompt_text = tk.Text(self.ai_prompt_frame, height=6, wrap=tk.WORD)
        self.ai_prompt_text.pack(fill=tk.X, expand=True, **inline_pad)
        self.ai_prompt_text.insert(tk.END, DEFAULT_AI_SYSTEM_PROMPT)
        self._register_themable_text(self.ai_prompt_text)

        log_tab.rowconfigure(0, weight=1)
        log_tab.columnconfigure(0, weight=1)
        log_container = ttk.Frame(log_tab, style="Card.TFrame", padding=16)
        log_container.grid(row=0, column=0, sticky=tk.NSEW)

        columns = ("time", "level", "message")
        self.log_tree = ttk.Treeview(
            log_container,
            columns=columns,
            show="headings",
            style="Log.Treeview",
            selectmode="browse",
        )
        self.log_tree.heading("time", text="Время")
        self.log_tree.heading("level", text="Статус")
        self.log_tree.heading("message", text="Сообщение")
        self.log_tree.column("time", width=90, anchor=tk.CENTER)
        self.log_tree.column("level", width=140, anchor=tk.W)
        self.log_tree.column("message", width=640, anchor=tk.W)

        log_scroll = ttk.Scrollbar(
            log_container, orient=tk.VERTICAL, command=self.log_tree.yview
        )
        self.log_tree.configure(yscrollcommand=log_scroll.set)
        self.log_tree.grid(row=0, column=0, sticky=tk.NSEW)
        log_scroll.grid(row=0, column=1, sticky=tk.NS, padx=(12, 0))
        log_container.rowconfigure(0, weight=1)
        log_container.columnconfigure(0, weight=1)

        self._refresh_log_tags()

        control_frame = ttk.Frame(main_frame, style="App.TFrame")
        control_frame.pack(fill=tk.X, pady=(0, 18))
        self.start_button = ttk.Button(
            control_frame,
            text="Старт анализа",
            command=self._start_analysis,
            style="Accent.TButton",
        )
        self.start_button.pack(side=tk.LEFT, padx=12)
        self.stop_button = ttk.Button(
            control_frame,
            text="Остановить",
            command=self._stop_analysis,
            state=tk.DISABLED,
            style="Secondary.TButton",
        )
        self.stop_button.pack(side=tk.LEFT, padx=12)

        progress_frame = ttk.Frame(main_frame, style="Card.TFrame", padding=16)
        progress_frame.pack(fill=tk.X, pady=(0, 18))
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            maximum=1.0,
            variable=self.progress_var,
            length=320,
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(side=tk.LEFT, padx=12)
        self.progress_label = ttk.Label(
            progress_frame, text="0 / 0", style="App.TLabel"
        )
        self.progress_label.pack(side=tk.LEFT, padx=12)
        self.elapsed_var = tk.StringVar(value="0:00 / ETA —")
        self.elapsed_label = ttk.Label(
            progress_frame, textvariable=self.elapsed_var, style="App.TLabel"
        )
        self.elapsed_label.pack(side=tk.LEFT, padx=12)
        self.open_output_button = ttk.Button(
            progress_frame,
            text="Открыть каталог",
            command=self._open_output_dir,
            state=tk.DISABLED,
            style="Secondary.TButton",
        )
        self.open_output_button.pack(side=tk.LEFT, padx=12)

        self.status_var = tk.StringVar(value="Ожидание запуска.")
        status_bar = ttk.Label(
            main_frame,
            textvariable=self.status_var,
            anchor=tk.W,
            style="App.TLabel",
        )
        status_bar.pack(fill=tk.X)

        self._update_embed_controls()
        self._on_ai_enable_change()

    def _choose_input_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Выберите Excel-файл",
            filetypes=[("Excel files", "*.xlsx *.xls")],
        )
        if path:
            self.input_path_var.set(path)

    def _choose_output_dir(self) -> None:
        directory = filedialog.askdirectory(
            title="Выберите каталог для сохранения отчета"
        )
        if directory:
            self.output_dir_var.set(directory)

    def _push_log(self, message: str, level: str = "INFO") -> None:
        _emit_log(level, message)
        self.log_queue.put(
            (
                "log",
                {
                    "level": level.upper(),
                    "message": message,
                    "time": time.strftime("%H:%M:%S"),
                },
            )
        )

    def _handle_analyzer_log(self, payload: Any) -> None:
        if isinstance(payload, dict):
            self.log_queue.put(("log", payload))
        else:
            self._push_log(str(payload))

    def _append_log(self, message: Any) -> None:
        timestamp = time.strftime("%H:%M:%S")
        level = "INFO"
        text = ""

        if isinstance(message, dict):
            text = str(message.get("message", ""))
            level = str(message.get("level", "INFO")).upper()
            timestamp = str(message.get("time", timestamp))
        else:
            text = str(message)
            prefix_match = re.match(r"\[(info|success|warning|error)\]\s*(.*)", text, re.IGNORECASE)
            if prefix_match:
                level = prefix_match.group(1).upper()
                text = prefix_match.group(2)
            else:
                level = self._detect_log_level(text)

        tag = level if level in {"SUCCESS", "WARNING", "ERROR"} else "INFO"
        item_id = self.log_tree.insert(
            "",
            "end",
            values=(timestamp, level, text),
            tags=(tag,),
        )
        self.log_tree.see(item_id)
        max_items = 1000
        children = self.log_tree.get_children()
        if len(children) > max_items:
            for obsolete_id in children[:-max_items]:
                self.log_tree.delete(obsolete_id)

    def _detect_log_level(self, message: str) -> str:
        bracket_match = re.match(r"\[(info|success|warning|error)\]\s*", message, re.IGNORECASE)
        if bracket_match:
            return bracket_match.group(1).upper()
        normalized = message.lower()
        if any(keyword in normalized for keyword in ("ошибка", "error", "failed")):
            return "ERROR"
        if any(keyword in normalized for keyword in ("предупреждение", "warning")):
            return "WARNING"
        if any(
            keyword in normalized
            for keyword in ("готов", "успеш", "сохранен", "сохранён", "заверш")
        ):
            return "SUCCESS"
        return "INFO"

    def _format_seconds(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:d}:{secs:02d}"

    def _update_run_feedback(self, current: int, total: int) -> None:
        total = max(total, 1)
        ratio = min(1.0, current / total)
        self.progress_var.set(ratio)
        self.progress_label.config(text=f"{current} / {total}")
        if not self.run_start_time:
            self.elapsed_var.set("0:00 / ETA —")
            return
        elapsed = time.time() - self.run_start_time
        elapsed_str = self._format_seconds(elapsed)
        eta_str = "—"
        if current > 0 and total > current:
            per_item = elapsed / current
            remaining = max(0.0, per_item * (total - current))
            eta_str = self._format_seconds(remaining)
        elif current >= total:
            eta_str = "0:00"
        self.elapsed_var.set(f"{elapsed_str} / ETA {eta_str}")

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self.log_queue.get_nowait()
                action = item[0]
                if action == "log":
                    self._append_log(item[1])
                elif action == "status":
                    self.status_var.set(item[1])
                elif action == "progress":
                    current, total = item[1]
                    self._update_run_feedback(current, total)
                elif action == "outputs":
                    payload = item[1] or {}
                    self.last_output_dir = payload.get("dir")
                    self.last_outputs = payload.get("paths", {})
                elif action == "finished":
                    success, message = item[1]
                    self.is_running = False
                    self.start_button.config(state=tk.NORMAL)
                    self.stop_button.config(state=tk.DISABLED)
                    self.status_var.set(message)
                    elapsed_display = "0:00"
                    if self.run_start_time:
                        elapsed_display = self._format_seconds(
                            time.time() - self.run_start_time
                        )
                    self.run_start_time = None
                    if success:
                        self.elapsed_var.set(f"{elapsed_display} / Готово")
                        if self.last_output_dir:
                            self.open_output_button.config(state=tk.NORMAL)
                        messagebox.showinfo("Анализ завершен", message)
                    else:
                        self.elapsed_var.set(f"{elapsed_display} / Прервано")
                        self.open_output_button.config(state=tk.DISABLED)
                        messagebox.showerror("Ошибка анализа", message)
                self.log_queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_queue)

    def _start_analysis(self) -> None:
        if self.is_running:
            return
        input_path = self.input_path_var.get().strip()
        output_dir = self.output_dir_var.get().strip() or os.path.dirname(input_path)

        if not input_path:
            messagebox.showwarning("Проверка данных", "Укажите входной Excel-файл.")
            return
        if not os.path.isfile(input_path):
            messagebox.showwarning("Проверка данных", "Файл не найден.")
            return
        if not output_dir:
            messagebox.showwarning("Проверка данных", "Укажите каталог для сохранения.")
            return
        if not os.path.isdir(output_dir):
            messagebox.showwarning("Проверка данных", "Каталог сохранения не найден.")
            return

        try:
            threshold = float(self.threshold_var.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning(
                "Проверка данных", "Порог сходства должен быть числом."
            )
            return

        try:
            batch_size = int(self.batch_size_var.get())
            if batch_size <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Проверка данных", "Размер батча должен быть положительным целым."
            )
            return

        embed_mode = self.embed_mode_var.get()
        remote_url = self.remote_url_var.get().strip()
        remote_model = self.remote_model_var.get().strip()
        remote_api_key = self.remote_api_key_var.get().strip()
        local_model = self.local_model_var.get().strip() or DEFAULT_MODEL_NAME

        if embed_mode == EMBED_MODE_REMOTE and not remote_url:
            messagebox.showwarning(
                "Проверка данных", "Укажите URL сервера эмбеддингов."
            )
            return
        if embed_mode == EMBED_MODE_REMOTE and not remote_model:
            messagebox.showwarning(
                "Проверка данных", "Укажите название модели на внешнем сервере."
            )
            return

        try:
            embed_workers = int(self.embed_workers_var.get())
            if embed_workers <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Проверка данных",
                "Количество воркеров эмбеддингов должно быть положительным целым.",
            )
            return
        if embed_mode != EMBED_MODE_REMOTE:
            embed_workers = max(1, embed_workers)

        ai_enabled = bool(self.ai_enabled_var.get())
        verification_mode = self.verification_mode_var.get()
        ai_system_prompt = DEFAULT_AI_SYSTEM_PROMPT
        ai_temperature = DEFAULT_AI_TEMPERATURE
        ai_max_tokens = DEFAULT_AI_MAX_TOKENS
        ai_use_json_mode = bool(self.ai_json_mode_var.get())
        ai_model_name = self.ai_model_var.get().strip() or DEFAULT_AI_MODEL
        ai_workers = DEFAULT_AI_WORKERS
        ai_api_key = self.ai_api_key_var.get().strip()
        local_servers_list: List[str] = []
        local_ai_model_name = (
            self.local_ai_model_var.get().strip() or "local-model/gguf-model"
        )

        if ai_enabled:
            prompt_text = self.ai_prompt_text.get("1.0", tk.END).strip()
            ai_system_prompt = prompt_text or DEFAULT_AI_SYSTEM_PROMPT
            try:
                ai_temperature = float(self.ai_temperature_var.get().replace(",", "."))
            except ValueError:
                messagebox.showwarning(
                    "Проверка данных", "Температура должна быть числом."
                )
                return
            try:
                ai_max_tokens = int(self.ai_max_tokens_var.get())
                if ai_max_tokens <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "Проверка данных",
                    "Максимальное число токенов должно быть положительным целым.",
                )
                return

            if verification_mode == "online":
                if not ai_api_key:
                    messagebox.showwarning(
                        "Проверка данных", "В режиме онлайн укажите ключ OpenAI API."
                    )
                    return
                try:
                    ai_workers = int(self.ai_workers_var.get())
                    if ai_workers <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showwarning(
                        "Проверка данных",
                        "Количество воркеров AI должно быть положительным целым.",
                    )
                    return
                local_servers_list = []
            else:
                servers_raw = self.local_servers_text.get("1.0", tk.END).splitlines()
                local_servers_list = [
                    server.strip() for server in servers_raw if server.strip()
                ]
                if not local_servers_list:
                    messagebox.showwarning(
                        "Проверка данных",
                        "Укажите хотя бы один адрес локального сервера.",
                    )
                    return
                ai_api_key = ""
                ai_workers = 0

        config = AnalysisConfig(
            input_path=input_path,
            output_dir=output_dir,
            col_id=self.col_id_var.get().strip(),
            col_superior=self.col_superior_var.get().strip(),
            col_executor=self.col_executor_var.get().strip(),
            col_level=self.col_level_var.get().strip(),
            col_text=self.col_text_var.get().strip(),
            threshold=threshold,
            embed_mode=embed_mode,
            local_model_name=local_model,
            remote_server_url=remote_url,
            remote_model_name=remote_model,
            remote_api_key=remote_api_key,
            embed_batch_size=batch_size,
            embed_workers=embed_workers,
            ai_verification_enabled=ai_enabled,
            verification_mode=verification_mode,
            ai_system_prompt=ai_system_prompt,
            ai_temperature=ai_temperature,
            ai_max_tokens=ai_max_tokens,
            ai_use_json_mode=ai_use_json_mode,
            ai_model_name=ai_model_name,
            ai_workers=ai_workers,
            ai_api_key=ai_api_key,
            local_servers=local_servers_list,
            local_ai_model_name=local_ai_model_name,
        )

        self.stop_event.clear()
        self.is_running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_var.set("Анализ выполняется…")
        self.progress_var.set(0.0)
        self.progress_label.config(text="0 / 0")
        self.run_start_time = time.time()
        self.elapsed_var.set("0:00 / ETA —")
        self.last_output_dir = None
        self.last_outputs = {}
        self.open_output_button.config(state=tk.DISABLED)
        self._push_log(f"{PERSONA_TITLE}: Инициирую обработку.")

        def worker() -> None:
            try:
                analyzer = HierarchyAnalyzer(
                    config=config,
                    log_callback=self._handle_analyzer_log,
                    progress_callback=lambda current, total: self.log_queue.put(
                        ("progress", (current, total))
                    ),
                    stop_event=self.stop_event,
                )
                result = analyzer.run()
                output_paths = self._save_outputs(result, config)
                self.log_queue.put(
                    (
                        "outputs",
                        {
                            "dir": config.output_dir,
                            "paths": output_paths,
                        },
                    )
                )
                self.log_queue.put(
                    (
                        "finished",
                        (
                            True,
                            "Готово. Файлы сохранены.",
                        ),
                    )
                )
            except RuntimeError as interruption:
                self._push_log(f"{PERSONA_TITLE}: {interruption}", level="WARNING")
                self.log_queue.put(
                    (
                        "finished",
                        (
                            False,
                            "Обработка остановлена пользователем.",
                        ),
                    )
                )
            except Exception as error:
                self._push_log(f"{PERSONA_TITLE}: Ошибка — {error}", level="ERROR")
                self.log_queue.put(
                    (
                        "finished",
                        (
                            False,
                            "Анализ завершился с ошибкой. Подробности в журнале.",
                        ),
                    )
                )

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _stop_analysis(self) -> None:
        if not self.is_running:
            return
        self.stop_event.set()
        self._push_log(f"{PERSONA_TITLE}: Получен сигнал на остановку.", level="WARNING")

    def _open_output_dir(self) -> None:
        directory = self.last_output_dir or self.output_dir_var.get().strip()
        if not directory:
            messagebox.showinfo(
                "Каталог не найден", "Каталог сохранения ещё не указан."
            )
            return
        if not os.path.isdir(directory):
            messagebox.showerror(
                "Каталог не существует", f"Папка {directory} недоступна."
            )
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(directory)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", directory])
            else:
                subprocess.Popen(["xdg-open", directory])
        except Exception as error:
            messagebox.showerror(
                "Открытие каталога", f"Не удалось открыть каталог: {error}"
            )

    def _profiles_dir(self) -> Path:
        return Path(__file__).resolve().parent / PROFILE_DIR_NAME

    def _default_profile_path(self) -> Path:
        return self._profiles_dir() / DEFAULT_PROFILE_NAME

    def _gather_ui_state(self) -> Dict[str, Any]:
        servers_raw = self.local_servers_text.get("1.0", tk.END).strip().splitlines()
        servers = [line.strip() for line in servers_raw if line.strip()]
        return {
            "input_path": self.input_path_var.get().strip(),
            "output_dir": self.output_dir_var.get().strip(),
            "col_id": self.col_id_var.get().strip(),
            "col_superior": self.col_superior_var.get().strip(),
            "col_executor": self.col_executor_var.get().strip(),
            "col_level": self.col_level_var.get().strip(),
            "col_text": self.col_text_var.get().strip(),
            "threshold": self.threshold_var.get().strip(),
            "embed_mode": self.embed_mode_var.get(),
            "local_model": self.local_model_var.get().strip(),
            "remote_url": self.remote_url_var.get().strip(),
            "remote_model": self.remote_model_var.get().strip(),
            "remote_api_key": self.remote_api_key_var.get(),
            "embed_batch_size": self.batch_size_var.get().strip(),
            "embed_workers": self.embed_workers_var.get().strip(),
            "ai_enabled": bool(self.ai_enabled_var.get()),
            "verification_mode": self.verification_mode_var.get(),
            "ai_api_key": self.ai_api_key_var.get(),
            "ai_model": self.ai_model_var.get().strip(),
            "ai_workers": self.ai_workers_var.get().strip(),
            "local_ai_model": self.local_ai_model_var.get().strip(),
            "ai_temperature": self.ai_temperature_var.get().strip(),
            "ai_max_tokens": self.ai_max_tokens_var.get().strip(),
            "ai_json_mode": bool(self.ai_json_mode_var.get()),
            "local_servers": servers,
            "ai_prompt": self.ai_prompt_text.get("1.0", tk.END).strip(),
        }

    def _apply_ui_state(self, state: Dict[str, Any]) -> None:
        self.input_path_var.set(state.get("input_path", ""))
        self.output_dir_var.set(state.get("output_dir", ""))
        self.col_id_var.set(state.get("col_id", self.col_id_var.get()))
        self.col_superior_var.set(
            state.get("col_superior", self.col_superior_var.get())
        )
        self.col_executor_var.set(
            state.get("col_executor", self.col_executor_var.get())
        )
        self.col_level_var.set(state.get("col_level", self.col_level_var.get()))
        self.col_text_var.set(state.get("col_text", self.col_text_var.get()))
        self.threshold_var.set(state.get("threshold", self.threshold_var.get()))
        self.embed_mode_var.set(state.get("embed_mode", self.embed_mode_var.get()))
        self.local_model_var.set(state.get("local_model", self.local_model_var.get()))
        self.remote_url_var.set(state.get("remote_url", self.remote_url_var.get()))
        self.remote_model_var.set(
            state.get("remote_model", self.remote_model_var.get())
        )
        self.remote_api_key_var.set(
            state.get("remote_api_key", self.remote_api_key_var.get())
        )
        self.batch_size_var.set(
            state.get("embed_batch_size", self.batch_size_var.get())
        )
        self.embed_workers_var.set(
            state.get("embed_workers", self.embed_workers_var.get())
        )
        self.ai_enabled_var.set(
            bool(state.get("ai_enabled", self.ai_enabled_var.get()))
        )
        self.verification_mode_var.set(
            state.get("verification_mode", self.verification_mode_var.get())
        )
        self.ai_api_key_var.set(state.get("ai_api_key", self.ai_api_key_var.get()))
        self.ai_model_var.set(state.get("ai_model", self.ai_model_var.get()))
        self.ai_workers_var.set(state.get("ai_workers", self.ai_workers_var.get()))
        self.local_ai_model_var.set(
            state.get("local_ai_model", self.local_ai_model_var.get())
        )
        self.ai_temperature_var.set(
            state.get("ai_temperature", self.ai_temperature_var.get())
        )
        self.ai_max_tokens_var.set(
            state.get("ai_max_tokens", self.ai_max_tokens_var.get())
        )
        self.ai_json_mode_var.set(
            bool(state.get("ai_json_mode", self.ai_json_mode_var.get()))
        )
        servers = state.get("local_servers", [])
        if isinstance(servers, str):
            servers_text = servers
        else:
            servers_text = "\n".join(servers)
        self.local_servers_text.configure(state=tk.NORMAL)
        self.local_servers_text.delete("1.0", tk.END)
        if servers_text:
            self.local_servers_text.insert(
                "1.0",
                servers_text.strip()
                + ("\n" if not servers_text.endswith("\n") else ""),
            )
        prompt = state.get("ai_prompt", "")
        self.ai_prompt_text.delete("1.0", tk.END)
        self.ai_prompt_text.insert("1.0", prompt or DEFAULT_AI_SYSTEM_PROMPT)
        self._update_embed_controls()
        self._on_ai_enable_change()
        self._on_verification_mode_change()

    def _load_profile_dialog(self) -> None:
        profiles_dir = self._profiles_dir()
        profiles_dir.mkdir(parents=True, exist_ok=True)
        initial_dir = str(profiles_dir)
        default_file = (
            Path(self.loaded_profile_path).name if self.loaded_profile_path else ""
        )
        path = filedialog.askopenfilename(
            title="Загрузить профиль",
            filetypes=[("JSON", "*.json")],
            initialdir=initial_dir,
            initialfile=default_file,
        )
        if path:
            self._load_profile(path=path)

    def _save_profile_dialog(self) -> None:
        data = self._gather_ui_state()
        profiles_dir = self._profiles_dir()
        profiles_dir.mkdir(parents=True, exist_ok=True)
        initial_dir = str(profiles_dir)
        default_name = DEFAULT_PROFILE_NAME
        if self.loaded_profile_path:
            loaded_path = Path(self.loaded_profile_path)
            initial_dir = str(loaded_path.parent)
            default_name = loaded_path.name
        path = filedialog.asksaveasfilename(
            title="Сохранить профиль",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialdir=initial_dir,
            initialfile=default_name,
        )
        if path:
            self._save_profile(path, data)

    def _load_profile(self, path: Optional[str] = None, auto: bool = False) -> None:
        profile_path: Optional[Path]
        if path:
            profile_path = Path(path)
        elif auto:
            default_path = self._default_profile_path()
            profile_path = default_path if default_path.exists() else None
        else:
            profile_path = None
        if not profile_path:
            return
        try:
            content = profile_path.read_text(encoding="utf-8")
            state = json.loads(content)
        except FileNotFoundError:
            if not auto:
                messagebox.showerror(
                    "Загрузка профиля", f"Файл {profile_path} не найден."
                )
            return
        except Exception as error:
            if not auto:
                messagebox.showerror(
                    "Загрузка профиля", f"Не удалось загрузить профиль: {error}"
                )
            return
        self._apply_ui_state(state)
        self.loaded_profile_path = str(profile_path)
        if not auto:
            messagebox.showinfo(
                "Загрузка профиля", f"Профиль загружен: {profile_path.name}"
            )
        default_path = self._default_profile_path()
        if profile_path != default_path:
            try:
                self._write_profile(default_path, state)
            except Exception:
                pass

    def _save_profile(self, path: str, data: Dict[str, Any]) -> None:
        profile_path = Path(path)
        try:
            self._write_profile(profile_path, data)
            self.loaded_profile_path = str(profile_path)
            default_path = self._default_profile_path()
            if profile_path != default_path:
                self._write_profile(default_path, data)
            messagebox.showinfo(
                "Сохранение профиля", f"Профиль сохранён: {profile_path.name}"
            )
        except Exception as error:
            messagebox.showerror(
                "Сохранение профиля", f"Не удалось сохранить профиль: {error}"
            )

    def _write_profile(self, path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _update_embed_controls(self) -> None:
        mode = self.embed_mode_var.get()
        local_state = tk.NORMAL if mode == EMBED_MODE_LOCAL else tk.DISABLED
        remote_state = tk.NORMAL if mode == EMBED_MODE_REMOTE else tk.DISABLED
        self.local_model_entry.configure(state=local_state)
        self.remote_url_entry.configure(state=remote_state)
        self.remote_model_entry.configure(state=remote_state)
        self.remote_api_key_entry.configure(state=remote_state)
        self.embed_workers_entry.configure(state=remote_state)

    def _on_ai_enable_change(self) -> None:
        if self.ai_enabled_var.get():
            if not self.ai_mode_frame.winfo_ismapped():
                self.ai_mode_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
            self._on_verification_mode_change()
            if not self.common_ai_frame.winfo_ismapped():
                self.common_ai_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
            if not self.ai_prompt_frame.winfo_ismapped():
                self.ai_prompt_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
        else:
            for frame in (
                self.ai_mode_frame,
                self.online_frame,
                self.local_frame,
                self.common_ai_frame,
                self.ai_prompt_frame,
            ):
                if frame.winfo_ismapped():
                    frame.pack_forget()

    def _on_verification_mode_change(self) -> None:
        if not self.ai_enabled_var.get():
            self.online_frame.pack_forget()
            self.local_frame.pack_forget()
            return
        mode = self.verification_mode_var.get()
        self.online_frame.pack_forget()
        self.local_frame.pack_forget()
        if mode == "online":
            self.online_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
        else:
            self.local_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

    def _save_outputs(self, result: AnalysisResult, config: AnalysisConfig) -> None:
        base_name = os.path.splitext(os.path.basename(config.input_path))[0]
        report_filename = f"{base_name}_hierarchy.xlsx"
        log_filename = "log.xlsx"
        report_path = os.path.join(config.output_dir, report_filename)
        log_path = os.path.join(config.output_dir, log_filename)

        self._push_log(f"{PERSONA_TITLE}: Сохраняю отчет {report_path}.")
        report_columns = list(result.report_df.columns)
        if "reason" in report_columns:
            report_columns.remove("reason")
        if "simScore" in report_columns:
            report_columns.remove("simScore")
        result.report_df[report_columns].to_excel(
            report_path,
            index=False,
            engine="openpyxl",
        )

        self._push_log(f"{PERSONA_TITLE}: Формирую технический лог {log_path}.")
        with pd.ExcelWriter(log_path, engine="openpyxl") as writer:
            result.log_df.to_excel(writer, index=False, sheet_name="log")
            meta_df = pd.DataFrame(
                [{"key": key, "value": value} for key, value in result.metadata.items()]
            )
            meta_df.to_excel(writer, index=False, sheet_name="meta")
        return {"report": report_path, "log": log_path}

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    root = tk.Tk()
    app = HierarchyAnalyzerApp(root)
    app.run()


if __name__ == "__main__":
    main()
