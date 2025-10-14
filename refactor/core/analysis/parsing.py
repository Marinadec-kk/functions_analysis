"""
Business logic for the document parsing tool (legacy ``1_parsing.py``).
The GUI layer is responsible for collecting user input and displaying progress;
this module focuses only on file traversal, AI interaction and statistics.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import re

from ..ai.client import AIClientConfig, create_openai_client, prepare_api_base_url
from ..text_processing import create_abbreviation, fast_clean_and_format_text
from ..file_io import read_doc_paragraphs, read_doc_paragraphs_win32

try:
    from openai import OpenAI
    from openai import APIConnectionError, APITimeoutError, RateLimitError
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore[assignment]
    APIConnectionError = APITimeoutError = RateLimitError = None  # type: ignore[assignment]
    httpx = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- data
@dataclass
class ParserStatistics:
    total_files: int = 0
    name_extracted_success: int = 0
    name_extracted_fail: int = 0
    name_parsed_success: int = 0
    parent_go_parsed: int = 0
    functions_found_success: int = 0
    functions_found_empty: int = 0
    functions_header_not_found: int = 0

    def to_report(self) -> str:
        return (
            f"--- Общая статистика ---\n"
            f"Всего файлов для обработки: {self.total_files}\n\n"
            f"--- Этап 1: Извлечение полного названия ГО ---\n"
            f"Успешно извлечено: {self.name_extracted_success} / {self.total_files}\n"
            f"Не удалось извлечь: {self.name_extracted_fail}\n\n"
            f"--- Этап 2: Парсинг названия ---\n"
            f"Успешно разделено на части: {self.name_parsed_success}\n"
            f"Из них с вышестоящим ГО: {self.parent_go_parsed}\n\n"
            f"--- Этап 3: Поиск функций ---\n"
            f"Документов с функциями: {self.functions_found_success}\n"
            f"Документов без функций (пустой раздел): {self.functions_found_empty}\n"
            f"Документов без раздела 'Функции': {self.functions_header_not_found}\n"
        )


@dataclass
class ParsingConfig:
    input_folder: str
    ai_mode: str
    timeout: float
    num_workers: int
    model: str
    api_key: str = ""
    base_url: str = ""
    base_urls: List[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    filepath: str
    main_go: Optional[str]
    parent_go: Optional[str]
    functions: List[str]


@dataclass
class ParsingResult:
    rows: List[Dict[str, str]]
    stats: ParserStatistics
    report: str
    documents: List[ParsedDocument]


# ---------------------------------------------------------------------- helpers
def enumerate_functions(paragraphs: Iterable[str]) -> Tuple[List[str], str]:
    """Extract function entries from paragraphs."""
    in_section = False
    acc: List[str] = []
    for raw in paragraphs:
        if not raw:
            continue
        text = " ".join(raw.split())
        low = text.lower()
        if not in_section:
            if re.match(r"^\s*(?:\d+\.?\s*)?функции:?\s*$", low):
                in_section = True
            continue
        if re.match(r"^\s*глава\s+\d+", low) or re.match(r"^\s*раздел\s+\d+", low):
            break
        if text.strip():
            acc.append(text.strip())
    cleaned_functions = [
        t for t in (fast_clean_and_format_text(t) for t in acc) if t
    ]
    if not in_section:
        return [], "HEADER_NOT_FOUND"
    if in_section and not cleaned_functions:
        return [], "NO_ITEMS_FOUND"
    return cleaned_functions, "OK"


def sanitize_json_string(payload: str) -> str:
    """Attempt to extract a JSON object from arbitrary text."""
    payload = payload.strip()
    import re

    match = re.search(r"```(?:json)?\s*(\{.*})\s*```", payload, re.DOTALL)
    if match:
        payload = match.group(1)
    try:
        start = payload.index("{")
        end = payload.rindex("}") + 1
        return payload[start:end]
    except ValueError:
        return payload


def parse_full_go_name(full_name: str) -> Dict[str, Optional[str]]:
    """Split hierarchical government body names into main and parent parts."""
    if not full_name:
        return {"main_go": None, "parent_go": None}

    import re

    parent_keywords = [
        "Министерства",
        "Агентства",
        "Комитета",
        "Департамента",
        "Управления",
    ]

    normalized_name = " ".join(full_name.split())
    for keyword in parent_keywords:
        match = re.search(
            r"\b" + re.escape(keyword) + r"\b", normalized_name, re.IGNORECASE
        )
        if match:
            split_index = match.start()
            main_go = normalized_name[:split_index].strip()
            parent_part = normalized_name[split_index:].strip()
            parent_tokens = parent_part.split()
            first_word = parent_tokens[0]
            if first_word.lower().endswith("а"):
                nominative_first_word = first_word[:-1]
            elif first_word.lower().endswith("я"):
                nominative_first_word = first_word[:-1] + "е"
            else:
                nominative_first_word = first_word
            parent_go = nominative_first_word + " " + " ".join(parent_tokens[1:])
            if not main_go:
                return {"main_go": parent_go.strip(), "parent_go": None}
            return {"main_go": main_go, "parent_go": parent_go.strip()}
    return {"main_go": normalized_name, "parent_go": None}


# ---------------------------------------------------------------------- analyzer
class ParsingAnalyzer:
    def __init__(
        self,
        logger: Callable[[str], None] = lambda msg: None,
        progress_callback: Callable[[int], None] = lambda delta: None,
    ):
        if OpenAI is None or httpx is None:
            raise RuntimeError(
                "openai/httpx packages are required for ParsingAnalyzer. "
                "Install them with 'pip install openai httpx'."
            )
        self.logger = logger
        self.progress = progress_callback
        self.stats = ParserStatistics()
        self.stats_lock = threading.Lock()

    # ----------------------------------------- public API ---------------------
    def analyze(
        self,
        config: ParsingConfig,
        stop_event: Optional[threading.Event] = None,
    ) -> ParsingResult:
        stop_event = stop_event or threading.Event()

        docx_files: List[str] = []
        doc_files: List[str] = []

        for base, _, files in os.walk(config.input_folder):
            for fname in files:
                if fname.startswith(("~$", "~", ".")):
                    continue
                lower = fname.lower()
                path = os.path.abspath(os.path.join(base, fname))
                if lower.endswith(".docx"):
                    docx_files.append(path)
                elif lower.endswith(".doc"):
                    doc_files.append(path)

        total_files = len(docx_files) + len(doc_files)
        if not total_files:
            raise ValueError("В указанной папке не найдено .doc/.docx файлов.")

        self.stats = ParserStatistics(total_files=total_files)

        self.logger(
            f"Найдено {total_files} файлов ({len(docx_files)} docx, {len(doc_files)} doc)."
        )

        servers = (
            config.base_urls
            if config.ai_mode == "Локальный" and config.base_urls
            else [config.base_url]
        )
        servers = [prepare_api_base_url(url) for url in servers if url]
        if not servers:
            raise ValueError("Не указаны серверы для подключения к ИИ.")

        results_queue: "queue.Queue[ParsedDocument]" = queue.Queue()

        if docx_files:
            self._process_docx_files(docx_files, servers, config, results_queue, stop_event)

        if stop_event.is_set():
            raise InterruptedError()

        if doc_files:
            self._process_doc_files(doc_files, servers[0], config, results_queue, stop_event)

        documents: List[ParsedDocument] = []
        while not results_queue.empty():
            documents.append(results_queue.get())

        documents.sort(key=lambda item: item.filepath)
        rows = self._build_rows(documents)
        report = self.stats.to_report()

        return ParsingResult(rows=rows, stats=self.stats, report=report, documents=documents)

    # ------------------------------------ internal helpers --------------------
    def _process_docx_files(
        self,
        docx_files: List[str],
        servers: List[str],
        config: ParsingConfig,
        results_queue: "queue.Queue[ParsedDocument]",
        stop_event: threading.Event,
    ) -> None:
        num_workers = config.num_workers
        num_servers = len(servers)
        workers_per_server = [num_workers // num_servers] * num_servers
        for idx in range(num_workers % num_servers):
            workers_per_server[idx] += 1

        self.logger(
            f"Распределение воркеров по серверам: {list(zip(servers, workers_per_server))}"
        )

        tasks_queue: "queue.Queue[str]" = queue.Queue()
        for path in docx_files:
            tasks_queue.put(path)

        threads: List[threading.Thread] = []
        for server_url, worker_count in zip(servers, workers_per_server):
            if worker_count == 0:
                continue
            self.logger(f"Запуск {worker_count} воркеров для сервера {server_url}...")
            for _ in range(worker_count):
                thread = threading.Thread(
                    target=self._file_processor_worker,
                    args=(
                        tasks_queue,
                        results_queue,
                        config,
                        server_url,
                        stop_event,
                    ),
                    daemon=True,
                )
                thread.start()
                threads.append(thread)

        tasks_queue.join()

    def _process_doc_files(
        self,
        doc_files: List[str],
        server_url: str,
        config: ParsingConfig,
        results_queue: "queue.Queue[ParsedDocument]",
        stop_event: threading.Event,
    ) -> None:
        self.logger("Начинаю последовательную обработку .doc файлов...")
        self.logger(f"Используется сервер {server_url} для .doc файлов")
        ai_client = create_openai_client(
            AIClientConfig(
                base_url=server_url,
                api_key=config.api_key,
                timeout=config.timeout,
            )
        )
        for filepath in doc_files:
            if stop_event.is_set():
                raise InterruptedError()
            self._process_single_file(
                filepath,
                results_queue,
                config,
                ai_client,
                stop_event,
            )

    def _file_processor_worker(
        self,
        tasks_queue: "queue.Queue[str]",
        results_queue: "queue.Queue[ParsedDocument]",
        config: ParsingConfig,
        base_url: str,
        stop_event: threading.Event,
    ) -> None:
        self.logger(f"Воркер стартовал для сервера {base_url}.")
        ai_client = create_openai_client(
            AIClientConfig(
                base_url=base_url,
                api_key=config.api_key,
                timeout=config.timeout,
            )
        )
        while not stop_event.is_set():
            try:
                filepath = tasks_queue.get(timeout=1)
            except queue.Empty:
                break
            try:
                self._process_single_file(
                    filepath, results_queue, config, ai_client, stop_event
                )
            finally:
                tasks_queue.task_done()
        self.logger(f"Воркер для {base_url} завершил работу.")

    def _process_single_file(
        self,
        filepath: str,
        results_queue: "queue.Queue[ParsedDocument]",
        config: ParsingConfig,
        ai_client: OpenAI,
        stop_event: threading.Event,
    ) -> None:
        filename = os.path.basename(filepath)
        self.logger(f"Обработка: {filename}")
        try:
            paragraphs = self._read_paragraphs(filepath)
            full_go_name = self._extract_full_go_name(
                paragraphs, filename, config, ai_client
            )
            if not full_go_name:
                with self.stats_lock:
                    self.stats.name_extracted_fail += 1
                self.logger(
                    f"⚠️ Пропуск {filename}: не удалось извлечь полное название ГО."
                )
                return
            with self.stats_lock:
                self.stats.name_extracted_success += 1

            go_data = parse_full_go_name(full_go_name)
            with self.stats_lock:
                self.stats.name_parsed_success += 1
                if go_data.get("parent_go"):
                    self.stats.parent_go_parsed += 1

            functions, status = enumerate_functions(paragraphs)
            if status != "OK":
                with self.stats_lock:
                    if status == "HEADER_NOT_FOUND":
                        self.stats.functions_header_not_found += 1
                    elif status == "NO_ITEMS_FOUND":
                        self.stats.functions_found_empty += 1
                if status == "HEADER_NOT_FOUND":
                    self.logger(
                        f"⚠️ {filename}: Пропуск. Не найден заголовок раздела 'Функции'."
                    )
                elif status == "NO_ITEMS_FOUND":
                    self.logger(
                        f"⚠️ {filename}: Пропуск. Раздел 'Функции' найден, но он пуст."
                    )
                return

            with self.stats_lock:
                self.stats.functions_found_success += 1

            results_queue.put(
                ParsedDocument(
                    filepath=filepath,
                    main_go=go_data.get("main_go"),
                    parent_go=go_data.get("parent_go"),
                    functions=functions,
                )
            )
        except Exception as exc:  # pragma: no cover - robustness
            self.logger(f"❌ Ошибка при обработке {filename}: {exc}")
            traceback.print_exc()
        finally:
            self.progress(1)

    def _read_paragraphs(self, filepath: str) -> List[str]:
        if filepath.lower().endswith(".docx"):
            return read_doc_paragraphs(filepath)
        return read_doc_paragraphs_win32(filepath)

    def _extract_full_go_name(
        self,
        paragraphs: List[str],
        filename: str,
        config: ParsingConfig,
        client: OpenAI,
    ) -> Optional[str]:
        context_paragraphs = paragraphs[:150]
        full_text = "\n".join(p.strip() for p in context_paragraphs)
        if not full_text.strip():
            self.logger(
                f"❌ Пропуск {filename}: не найден текст для анализа в первых 150 абзацах."
            )
            return None

        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries):
            try:
                api_args = {
                    "model": config.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT_EXTRACT_FULL_NAME},
                        {"role": "user", "content": full_text},
                    ],
                    "temperature": 1.0,
                    "max_tokens": 4000,
                    "top_p": 1,
                    "frequency_penalty": 0,
                    "presence_penalty": 0,
                }
                if config.ai_mode != "Локальный":
                    api_args["response_format"] = {"type": "json_object"}

                self.logger(
                    f"Отправка запроса на извлечение полного названия для {filename} "
                    f"(попытка {attempt + 1}/{max_retries})..."
                )
                response = client.chat.completions.create(**api_args)
                content = response.choices[0].message.content
                self.logger(
                    f"ПОЛУЧЕН ОТВЕТ для {filename}:\n--- ОТВЕТ СЕРВЕРА ---\n{content}\n--- КОНЕЦ ОТВЕТА ---"
                )
                try:
                    clean_content = sanitize_json_string(content)
                    data = json.loads(clean_content)
                    full_go_name = data.get("full_go_name")
                    if isinstance(full_go_name, str) and full_go_name.strip():
                        return full_go_name.strip()
                    self.logger(
                        f"❌ Некорректный формат ответа или отсутствует ключ 'full_go_name' для {filename}."
                    )
                except json.JSONDecodeError:
                    self.logger(f"❌ Ошибка декодирования JSON для {filename}.")
            except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                self.logger(
                    f"  [AI] Ошибка API для {filename} (попытка {attempt + 1}/{max_retries}): "
                    f"{type(exc).__name__}. Повтор через {delay:.1f} сек..."
                )
                if attempt + 1 == max_retries:
                    self.logger("  [AI] ❌ Превышено количество попыток.")
                    return None
                time.sleep(delay)
                delay *= 2
            except Exception as exc:  # pragma: no cover - robustness
                self.logger(
                    f"  [AI] ❌ Непредвиденная ошибка на этапе извлечения ГО для {filename}: {exc}"
                )
                traceback.print_exc()
                return None
        return None

    def _build_rows(self, documents: List[ParsedDocument]) -> List[Dict[str, str]]:
        func_counters: Dict[str, int] = defaultdict(int)
        final_rows: List[Dict[str, str]] = []
        for doc in documents:
            if not doc.main_go:
                continue
            id_prefix = create_abbreviation(doc.main_go)
            for func_text in doc.functions:
                func_counters[id_prefix] += 1
                final_rows.append(
                    {
                        "ID": f"{id_prefix}-{func_counters[id_prefix]}",
                        "Госорган": doc.main_go,
                        "Вышестоящий": doc.parent_go,
                        "Текст_функции": func_text,
                    }
                )
        return final_rows


# --------------------------- prompt storage (for imports convenience) ----------
SYSTEM_PROMPT_EXTRACT_FULL_NAME = """
You are an expert in analyzing legal and regulatory documents of the Republic of Kazakhstan. You will be given the first few pages of an official document.

Your task is to carefully study the text and identify the **full hierarchical name** of the single, **main government body** the document is about.

RULES:
1.  The main government body is often mentioned in headers like "Положение о..." ("Regulation on...").
2.  Extract its complete name, including any parent bodies mentioned as part of the name (e.g., "Committee X of Ministry Y").
3.  Provide the name in the nominative case (именительный падеж).
4.  Clean the name from phrases like "Республики Казахстан".
5.  Correct any minor grammatical errors.
6.  Provide the response STRICTLY in JSON format with a single key: "full_go_name".
7.  **Crucially, the value MUST be in Russian.**

EXAMPLE TEXT 1: "...Положение о Комитете государственных доходов Министерства финансов..."
EXAMPLE RESPONSE 1:
{
  "full_go_name": "Комитет государственных доходов Министерства финансов"
}

EXAMPLE TEXT 2: "...Утвердить прилагаемые: 1) Положение о Министерстве здравоохранения Республики Казахстан..."
EXAMPLE RESPONSE 2:
{
  "full_go_name": "Министерство здравоохранения"
}
"""
