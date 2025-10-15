# =====================================================================================
# М3 ПАРСЕР v6.0 (AI Extraction + Python Parsing): извлечение функций и иерархии ГО
#
# Что нового в v6.0:
# - НОВЫЙ ГИБРИДНЫЙ ПОДХОД: ИИ извлекает полное иерархическое название ГО
#   (например, "Комитет X Министерства Y"), а Python-скрипт уже на своей
#   стороне разделяет эту строку на "Госорган" и "Вышестоящий".
#   Это значительно повышает точность определения иерархии.
# - ОБНОВЛЕННЫЙ ПРОМПТ: Промпт переработан для извлечения единого полного названия.
# - НОВАЯ ЛОГИКА ПАРСИНГА: В код добавлена функция для разделения строки по
#   ключевым словам ("Министерства", "Агентства" и т.д.).
# - АДАПТИРОВАНА СТАТИСТИКА: Отчет теперь отражает этапы извлечения и парсинга.
#
# Зависимости: pip install pandas python-docx openpyxl openai httpx pywin32
# Опционально для темы: pip install sv-ttk
# =====================================================================================

import os
import re
import sys
import json
import time
import queue
import threading
import traceback
from collections import defaultdict

# --- Основные зависимости ---
import pandas as pd
from docx import Document

# --- GUI ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- COM-интерфейс для .doc (Windows) ---
try:
    import win32com.client as win32

    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    win32 = None

# --- Зависимости для работы с AI ---
try:
    from openai import OpenAI, APIConnectionError, RateLimitError, APITimeoutError
    import httpx
except ImportError:
    OpenAI = None  # Обработаем отсутствие библиотеки в GUI
    APIConnectionError, RateLimitError, APITimeoutError, httpx = None, None, None, None

# --- Опциональная тема для GUI ---
try:
    import sv_ttk
except ImportError:
    sv_ttk = None

# -----------------------------------
# Конфигурация и константы
# -----------------------------------


# --- Новый логгер для вывода в терминал ---
def log_to_terminal(message: str):
    """Выводит сообщение в терминал с временной меткой и ID потока."""
    timestamp = time.strftime("%H:%M:%S")
    thread_id = threading.get_ident()
    print(f"[{timestamp}][Thread-{thread_id}] {message}")


# Стоп-слова для аббревиатур
ABBR_STOPWORDS = {
    "и",
    "по",
    "о",
    "в",
    "на",
    "об",
    "с",
    "при",
    "для",
    "над",
    "под",
    "из",
    "во",
    "со",
    "республика",
    "республики",
    "казахстан",
    "казахстана",
    "государственного",
    "учреждения",
}

# Промпт для LLM (v6.0) - Извлечение полного названия
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


# -----------------------------------
# Вспомогательные функции
# -----------------------------------


def read_doc_paragraphs_win32(filepath: str):
    """Чтение .doc через COM-интерфейс MS Word (Windows + pywin32)."""
    word, doc = None, None
    try:
        word = win32.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(filepath, ReadOnly=True)
        return [p.Range.Text for p in doc.Paragraphs]
    finally:
        if doc:
            doc.Close(0)
        if word:
            word.Quit()


def fast_clean_and_format_text(text: str) -> str:
    """Быстрая чистка мусора и унификация форматирования пункта функции."""
    if not text:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    patterns_to_remove = [
        r"\s*сноска\s*\..*",
        r"\s*примечание\s*ИЗПИ!.*",
        r"\s*\(?вводится в действие.*?(\(|$)",
        r"\(порядок введения в действие см\. п\. \d+\)",
        r"\s*искл[ю]?че?н[оа]?.*",
        r"\s*действовал[аи]? до \d{2}\.\d{2}\.\d{4}.*",
        r";\s*от\s+\d{2}\.\d{2}\.\d{4}.*",
        r"\s*см\. п\. \d+",
    ]
    for pat in patterns_to_remove:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*\d+(?:[-.][\w]+)*\)\s*", "", text).strip()
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip().rstrip(";,").strip()


def create_abbreviation(name: str) -> str:
    """Создаёт аббревиатуру, исключая стоп-слова."""
    if not isinstance(name, str) or not name.strip():
        return "GO"
    name = " ".join(name.split())
    tokens = re.split(r"[\s\-]+", name)
    if all(tok.isupper() and len(tok) > 1 for tok in tokens):
        return tokens[0]
    letters = [w[0].upper() for w in tokens if w and w.lower() not in ABBR_STOPWORDS]
    return "".join(letters) or (tokens[0][:3].upper())


def enumerate_functions(paragraphs):
    """Извлекает пункты из раздела «Функции»."""
    in_section = False
    acc = []
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
    cleaned_functions = [t for t in (fast_clean_and_format_text(t) for t in acc) if t]
    if not in_section:
        return [], "HEADER_NOT_FOUND"
    if in_section and not cleaned_functions:
        return [], "NO_ITEMS_FOUND"
    return cleaned_functions, "OK"


def prepare_api_base_url(url: str) -> str:
    """Готовит URL для OpenAI-совместимого API."""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _sanitize_json_string(s: str) -> str:
    """Пытается очистить строку, чтобы она стала валидным JSON."""
    s = s.strip()
    match = re.search(r"```(?:json)?\s*(\{.*})\s*```", s, re.DOTALL)
    if match:
        s = match.group(1)
    try:
        start = s.index("{")
        end = s.rindex("}") + 1
        return s[start:end]
    except ValueError:
        return s


def _parse_full_go_name(full_name: str) -> dict:
    """Разделяет полное иерархическое название на основной и вышестоящий ГО."""
    if not full_name:
        return {"main_go": None, "parent_go": None}

    # Ключевые слова в родительном падеже, указывающие на начало вышестоящего органа
    parent_keywords = [
        "Министерства",
        "Агентства",
        "Комитета",
        "Департамента",
        "Управления",
    ]

    normalized_name = " ".join(full_name.split())

    for keyword in parent_keywords:
        # Ищем ключевое слово как отдельное слово
        match = re.search(
            r"\b" + re.escape(keyword) + r"\b", normalized_name, re.IGNORECASE
        )
        if match:
            split_index = match.start()

            main_go = normalized_name[:split_index].strip()
            parent_part = normalized_name[split_index:].strip()

            # Простое восстановление именительного падежа для первого слова
            parent_tokens = parent_part.split()
            first_word = parent_tokens[0]
            if first_word.lower().endswith("а"):
                nominative_first_word = first_word[:-1]
            elif first_word.lower().endswith("я"):
                nominative_first_word = first_word[:-1] + "е"
            else:
                nominative_first_word = first_word

            parent_go = nominative_first_word + " " + " ".join(parent_tokens[1:])

            # Если после разделения основная часть пуста, значит это и есть основной ГО
            if not main_go:
                return {"main_go": parent_go.strip(), "parent_go": None}

            return {"main_go": main_go, "parent_go": parent_go.strip()}

    # Если ключевых слов не найдено, значит это орган верхнего уровня
    return {"main_go": normalized_name, "parent_go": None}


def build_anchor_context(paragraphs, window_size: int = 10) -> str | None:
    """Формирует сэмпл вокруг первого вхождения якоря «положение о»."""
    if not paragraphs:
        return None

    full_text = "\n".join(p.strip() for p in paragraphs if p)
    if not full_text.strip():
        return None

    normalized_text = re.sub(r"\s+", " ", full_text).strip()
    if not normalized_text:
        return None

    anchor_pattern = re.compile(r"\bположение\s*о\b", re.IGNORECASE)
    match = anchor_pattern.search(normalized_text)
    if not match:
        return None

    before_text = normalized_text[: match.start()].strip()
    after_text = normalized_text[match.end() :].strip()

    before_words = before_text.split()
    after_words = after_text.split()

    anchor_fragment = normalized_text[match.start() : match.end()].strip()
    context_words = (
        before_words[-window_size:] + [anchor_fragment] + after_words[:window_size]
    )
    return " ".join(context_words).strip() if context_words else None


# -----------------------------------
# Основной класс приложения
# -----------------------------------


class ParserApp:
    def __init__(self, master: tk.Tk):
        if not OpenAI:
            messagebox.showerror(
                "Зависимость не найдена",
                "Библиотеки 'openai' и 'httpx' не установлены.\n"
                "Пожалуйста, установите их: pip install openai httpx",
            )
            master.destroy()
            sys.exit(1)

        self.root = master
        self.root.title("Парсер функций ГО v6.0 (AI Extraction + Python Parsing)")
        self.root.geometry("1000x700")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)
        self._worker_thread = None
        self._stop_flag = threading.Event()
        self.stats_lock = threading.Lock()
        self.stats = defaultdict(int)

    def _drain_ui_queue(self):
        try:
            while True:
                command, value = self.ui_queue.get_nowait()
                if command == "progress_reset":
                    self.progress_bar["maximum"] = max(1, value)
                    self.progress_bar["value"] = 0
                elif command == "progress_update":
                    self.progress_bar.step(value)
                elif command == "worker_done":
                    self._on_worker_finished(value)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=400)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Выбор папки")
        files_frame.pack(fill=tk.X, pady=5)
        self.input_folder_var = tk.StringVar()
        self._create_folder_row(
            files_frame,
            "Папка с документами:",
            self.input_folder_var,
            self._choose_input_folder,
        )

        ai_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки ИИ")
        ai_frame.pack(fill=tk.X, pady=5)
        self.ai_mode_var = tk.StringVar(value="Локальный")
        self._create_ai_mode_selector(ai_frame)
        self._create_ai_config_frames(ai_frame)
        self._on_ai_mode_change()

        perf_frame = ttk.LabelFrame(
            left_panel, text="Этап 3: Настройки производительности"
        )
        perf_frame.pack(fill=tk.X, pady=5)
        self.workers_var = tk.StringVar(value="8")
        self._create_entry_row(perf_frame, "Количество воркеров:", self.workers_var)

        controls_frame = ttk.LabelFrame(right_panel, text="Управление")
        controls_frame.pack(fill=tk.X, pady=5)
        self.start_btn = ttk.Button(
            controls_frame,
            text="Старт анализа",
            command=self._on_start,
            style="Accent.TButton",
        )
        self.start_btn.pack(
            side=tk.LEFT, expand=True, fill=tk.X, ipady=5, padx=5, pady=5
        )
        self.stop_btn = ttk.Button(
            controls_frame, text="Стоп", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(
            side=tk.LEFT, expand=True, fill=tk.X, ipady=5, padx=5, pady=5
        )

        progress_frame = ttk.LabelFrame(right_panel, text="Прогресс")
        progress_frame.pack(fill=tk.X, pady=5, expand=True)
        self.progress_bar = ttk.Progressbar(progress_frame)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=10)

    @staticmethod
    def _create_folder_row(parent, label, var, cmd):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=5, padx=5)
        ttk.Label(row, text=label, width=20).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    @staticmethod
    def _create_entry_row(parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=20).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )

    def _create_ai_mode_selector(self, parent):
        mode_frame = ttk.Frame(parent)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(
            mode_frame,
            text="Онлайн",
            variable=self.ai_mode_var,
            value="Онлайн",
            command=self._on_ai_mode_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="Локальный",
            variable=self.ai_mode_var,
            value="Локальный",
            command=self._on_ai_mode_change,
        ).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(
            mode_frame,
            text="АП",
            variable=self.ai_mode_var,
            value="АП",
            command=self._on_ai_mode_change,
        ).pack(side=tk.LEFT)

    def _create_ai_config_frames(self, parent):
        self.timeout_var = tk.StringVar(value="60.0")

        self.online_frame = ttk.Frame(parent)
        self.api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.online_model_var = tk.StringVar(value="gpt-4o-mini")
        self._create_entry_row(
            self.online_frame, "Ключ API:", self.api_key_var, show="*"
        )
        self._create_entry_row(self.online_frame, "Модель:", self.online_model_var)
        self._create_entry_row(self.online_frame, "Таймаут (сек):", self.timeout_var)

        self.local_frame = ttk.Frame(parent)
        row = ttk.Frame(self.local_frame)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text="URL серверов (1 на строку):", width=25).pack(
            side=tk.LEFT, anchor="n"
        )
        self.local_url_text = tk.Text(row, height=4, width=30)
        self.local_url_text.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.local_url_text.insert(tk.END, "http://localhost:1234")
        self.local_model_var = tk.StringVar(value="local-model")
        self._create_entry_row(self.local_frame, "Модель:", self.local_model_var)
        self._create_entry_row(self.local_frame, "Таймаут (сек):", self.timeout_var)

        self.ap_frame = ttk.Frame(parent)
        self.ap_url_var = tk.StringVar(value="https://llm.govplan.kz")
        self.ap_key_var = tk.StringVar(value="sk-...")
        self.ap_model_var = tk.StringVar(value="openai/gpt-oss-120b")
        self._create_entry_row(self.ap_frame, "URL сервера:", self.ap_url_var)
        self._create_entry_row(self.ap_frame, "Ключ API:", self.ap_key_var, show="*")
        self._create_entry_row(self.ap_frame, "Модель:", self.ap_model_var)
        self._create_entry_row(self.ap_frame, "Таймаут (сек):", self.timeout_var)

    def _choose_input_folder(self):
        d = filedialog.askdirectory(title="Выберите корневую папку с документами")
        if d:
            self.input_folder_var.set(d)

    def _on_ai_mode_change(self):
        mode = self.ai_mode_var.get()
        self.online_frame.pack_forget()
        self.local_frame.pack_forget()
        self.ap_frame.pack_forget()
        if mode == "Онлайн":
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        elif mode == "Локальный":
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)
        elif mode == "АП":
            self.ap_frame.pack(fill=tk.X, padx=5, pady=2)

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return messagebox.showwarning("Выполняется", "Анализ уже запущен.")
        try:
            config = self._get_config()
        except ValueError as e:
            return messagebox.showerror("Ошибка в настройках", str(e))

        self._stop_flag.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        log_to_terminal("=" * 50)
        log_to_terminal("ЗАПУСК АНАЛИЗА ДОКУМЕНТОВ")

        self._worker_thread = threading.Thread(
            target=self._master_thread_logic, args=(config,), daemon=True
        )
        self._worker_thread.start()
        return

    def _get_config(self):
        try:
            timeout = float(self.timeout_var.get())
        except (ValueError, TypeError):
            raise ValueError("Значение таймаута должно быть числом (например, 60.0).")

        try:
            num_workers = int(self.workers_var.get())
            if num_workers <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Количество воркеров должно быть целым числом больше 0.")

        config = {
            "input_folder": self.input_folder_var.get().strip(),
            "ai_mode": self.ai_mode_var.get(),
            "timeout": timeout,
            "num_workers": num_workers,
        }
        if not config["input_folder"] or not os.path.isdir(config["input_folder"]):
            raise ValueError("Укажите корректную папку с документами.")

        mode = config["ai_mode"]
        if mode == "Онлайн":
            config.update(
                {
                    "api_key": self.api_key_var.get().strip(),
                    "model": self.online_model_var.get().strip(),
                    "base_url": "https://api.openai.com/v1",
                }
            )
            if not config["api_key"]:
                raise ValueError("В режиме 'Онлайн' нужен ключ API.")
        elif mode == "Локальный":
            urls_text = self.local_url_text.get("1.0", tk.END).strip()
            if not urls_text:
                raise ValueError(
                    "В режиме 'Локальный' укажите хотя бы один URL сервера."
                )
            raw_urls = [url.strip() for url in urls_text.split("\n") if url.strip()]
            if not raw_urls:
                raise ValueError(
                    "В режиме 'Локальный' укажите хотя бы один URL сервера."
                )

            config.update(
                {
                    "base_urls": [prepare_api_base_url(url) for url in raw_urls],
                    "model": self.local_model_var.get().strip(),
                    "api_key": "not-needed",
                }
            )
        elif mode == "АП":
            config.update(
                {
                    "base_url": prepare_api_base_url(self.ap_url_var.get().strip()),
                    "api_key": self.ap_key_var.get().strip(),
                    "model": self.ap_model_var.get().strip(),
                }
            )
        return config

    def _on_stop(self):
        if self._worker_thread and self._worker_thread.is_alive():
            log_to_terminal("Запрошена остановка... Завершаю текущий файл.")
            self._stop_flag.set()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_worker_finished(self, final_message):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        log_to_terminal(final_message.upper())

    def _master_thread_logic(self, config: dict):
        try:
            self.stats = defaultdict(int)  # Сброс статистики перед запуском
            tasks_queue = queue.Queue()
            results_queue = queue.Queue()

            docx_files = []
            doc_files = []
            for root_dir, _, files in os.walk(config["input_folder"]):
                for name in files:
                    if name.startswith(("~$", "~", ".")):
                        continue
                    lower_name = name.lower()
                    filepath = os.path.abspath(os.path.join(root_dir, name))
                    if lower_name.endswith(".docx"):
                        docx_files.append(filepath)
                    elif lower_name.endswith(".doc") and WIN32_AVAILABLE:
                        doc_files.append(filepath)

            total_files = len(docx_files) + len(doc_files)
            if not total_files:
                raise ValueError("В указанной папке не найдено .doc/.docx файлов.")

            self.stats["total_files"] = total_files
            self.ui_queue.put(("progress_reset", total_files))
            log_to_terminal(
                f"Найдено {total_files} файлов ({len(docx_files)} docx, {len(doc_files)} doc)."
            )

            # Определяем список серверов для работы
            is_local_multi = config["ai_mode"] == "Локальный" and "base_urls" in config
            servers = (
                config["base_urls"] if is_local_multi else [config.get("base_url")]
            )
            num_servers = len(servers)

            if docx_files:
                num_workers = config["num_workers"]

                # Распределяем воркеров по серверам
                workers_per_server = [num_workers // num_servers] * num_servers
                remainder = num_workers % num_servers
                for i in range(remainder):
                    workers_per_server[i] += 1

                log_to_terminal(
                    f"Распределение воркеров по серверам: {list(zip(servers, workers_per_server))}"
                )

                for f in docx_files:
                    tasks_queue.put(f)

                threads = []
                for server_url, worker_count in zip(servers, workers_per_server):
                    if worker_count == 0:
                        continue
                    log_to_terminal(
                        f"Запуск {worker_count} воркеров для сервера {server_url}..."
                    )
                    for _ in range(worker_count):
                        thread = threading.Thread(
                            target=self._file_processor_worker,
                            args=(tasks_queue, results_queue, config, server_url),
                            daemon=True,
                        )
                        thread.start()
                        threads.append(thread)
                tasks_queue.join()

            if self._stop_flag.is_set():
                raise InterruptedError()

            if doc_files:
                log_to_terminal("Начинаю последовательную обработку .doc файлов...")
                doc_server_url = servers[0]
                log_to_terminal(f"Используется сервер {doc_server_url} для .doc файлов")
                ai_client = OpenAI(
                    base_url=doc_server_url,
                    api_key=config.get("api_key", "not-needed"),
                    http_client=httpx.Client(timeout=config.get("timeout", 60.0)),
                )
                for filepath in doc_files:
                    if self._stop_flag.is_set():
                        raise InterruptedError()
                    self._process_single_file(
                        filepath, results_queue, config, ai_client
                    )

            raw_results = []
            while not results_queue.empty():
                raw_results.append(results_queue.get())

            log_to_terminal("Все файлы обработаны. Идет сборка отчета...")

            final_rows = []
            func_counters = defaultdict(int)

            for result in sorted(raw_results, key=lambda x: x["filepath"]):
                main_go = result.get("main_go")
                parent_go = result.get("parent_go")
                if not main_go:
                    continue

                id_prefix = create_abbreviation(main_go)

                for func_text in result["functions"]:
                    func_counters[id_prefix] += 1
                    row = {
                        "ID": f"{id_prefix}-{func_counters[id_prefix]}",
                        "Госорган": main_go,
                        "Вышестоящий": parent_go,
                        "Текст_функции": func_text,
                    }
                    final_rows.append(row)

            self._save_report(final_rows, config)

            # Формирование и вывод итоговой статистики
            stats_report = self._generate_stats_report()
            log_to_terminal("\n" + "=" * 20 + " ИТОГОВЫЙ ОТЧЕТ " + "=" * 20)
            log_to_terminal(stats_report)
            log_to_terminal("=" * 56)

            messagebox.showinfo("Анализ завершен!", stats_report)
            self.ui_queue.put(("worker_done", "Анализ успешно завершен!"))

        except InterruptedError:
            self.ui_queue.put(("worker_done", "Процесс остановлен пользователем."))
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("Критическая ошибка", f"Произошла ошибка:\n{e}")
            self.ui_queue.put(("worker_done", f"Ошибка: {e}"))

    def _generate_stats_report(self):
        """Формирует текстовый отчет по собранной статистике."""
        s = self.stats
        report = (
            f"--- Общая статистика ---\n"
            f"Всего файлов для обработки: {s['total_files']}\n\n"
            f"--- Этап 1: Извлечение полного названия ГО ---\n"
            f"Успешно извлечено: {s['name_extracted_success']} / {s['total_files']}\n"
            f"Не удалось извлечь: {s['name_extracted_fail']}\n\n"
            f"--- Этап 2: Парсинг названия ---\n"
            f"Успешно разделено на части: {s['name_parsed_success']}\n"
            f"Из них с вышестоящим ГО: {s['parent_go_parsed']}\n\n"
            f"--- Этап 3: Поиск функций ---\n"
            f"Документов с функциями: {s['functions_found_success']}\n"
            f"Документов без функций (пустой раздел): {s['functions_found_empty']}\n"
            f"Документов без раздела 'Функции': {s['functions_header_not_found']}\n"
        )
        return report

    def _process_single_file(self, filepath, results_queue, config, ai_client):
        filename = os.path.basename(filepath)
        log_to_terminal(f"Обработка: {filename}")
        try:
            paragraphs = self._read_paragraphs(filepath)

            # Этап 1: Извлечение полного названия
            full_go_name = self._extract_full_go_name(
                paragraphs, filename, config, ai_client
            )
            if not full_go_name:
                with self.stats_lock:
                    self.stats["name_extracted_fail"] += 1
                log_to_terminal(
                    f"⚠️ Пропуск {filename}: не удалось извлечь полное название ГО."
                )
                return
            with self.stats_lock:
                self.stats["name_extracted_success"] += 1

            # Этап 2: Парсинг названия
            go_data = _parse_full_go_name(full_go_name)
            with self.stats_lock:
                self.stats["name_parsed_success"] += 1
                if go_data.get("parent_go"):
                    self.stats["parent_go_parsed"] += 1

            # Этап 3: Поиск функций
            functions, status = enumerate_functions(paragraphs)
            if status != "OK":
                if status == "HEADER_NOT_FOUND":
                    with self.stats_lock:
                        self.stats["functions_header_not_found"] += 1
                    log_to_terminal(
                        f"⚠️ {filename}: Пропуск. Не найден заголовок раздела 'Функции'."
                    )
                elif status == "NO_ITEMS_FOUND":
                    with self.stats_lock:
                        self.stats["functions_found_empty"] += 1
                    log_to_terminal(
                        f"⚠️ {filename}: Пропуск. Раздел 'Функции' найден, но он пуст."
                    )
                return
            with self.stats_lock:
                self.stats["functions_found_success"] += 1

            results_queue.put(
                {
                    "filepath": filepath,
                    "main_go": go_data["main_go"],
                    "parent_go": go_data.get("parent_go"),
                    "functions": functions,
                }
            )
        except Exception as e:
            log_to_terminal(f"❌ Ошибка при обработке {filename}: {e}")
        finally:
            self.ui_queue.put(("progress_update", 1))

    def _file_processor_worker(
        self,
        tasks_queue: queue.Queue,
        results_queue: queue.Queue,
        config: dict,
        base_url: str,
    ):
        """Воркер, который обрабатывает файлы из очереди, подключаясь к указанному серверу."""
        log_to_terminal(f"Воркер стартовал для сервера {base_url}.")
        ai_client = OpenAI(
            base_url=base_url,
            api_key=config.get("api_key", "not-needed"),
            http_client=httpx.Client(timeout=config.get("timeout", 60.0)),
        )

        while not self._stop_flag.is_set():
            try:
                filepath = tasks_queue.get(timeout=1)
                self._process_single_file(filepath, results_queue, config, ai_client)
                tasks_queue.task_done()
            except queue.Empty:
                break
        log_to_terminal(f"Воркер для {base_url} завершил работу.")

    @staticmethod
    def _read_paragraphs(filepath):
        if filepath.lower().endswith(".docx"):
            doc = Document(filepath)
            return [p.text for p in doc.paragraphs]
        else:
            return read_doc_paragraphs_win32(filepath)

    @staticmethod
    def _extract_full_go_name(paragraphs, filename, config, client):
        """Этап 1: Извлекает полное иерархическое название ГО."""
        sample_text = build_anchor_context(paragraphs)
        fallback_used = False
        if not sample_text:
            fallback_used = True
            context_paragraphs = paragraphs[:100]
            sample_text = "\n".join(p.strip() for p in context_paragraphs)

        if not sample_text.strip():
            log_to_terminal(
                f"❌ Пропуск {filename}: не удалось подготовить текст для анализа."
            )
            return None

        if fallback_used:
            log_to_terminal(
                f"⚠️ {filename}: якорь 'положение о' не найден. Используется резервный фрагмент текста."
            )

        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries):
            try:
                api_args = {
                    "model": config["model"],
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT_EXTRACT_FULL_NAME},
                        {"role": "user", "content": sample_text},
                    ],
                    "model": "gpt-4o",
                    "temperature": 1.0,
                    "max_tokens": 4000,
                    "top_p": 1,
                    "frequency_penalty": 0,
                    "presence_penalty": 0,
                }

                if config["ai_mode"] != "Локальный":
                    api_args["response_format"] = {"type": "json_object"}

                log_to_terminal(
                    f"Отправка запроса на извлечение полного названия для {filename} (попытка {attempt + 1}/{max_retries})..."
                )
                response = client.chat.completions.create(**api_args)

                content = response.choices[0].message.content
                log_to_terminal(
                    f"ПОЛУЧЕН ОТВЕТ для {filename}:\n--- ОТВЕТ СЕРВЕРА ---\n{content}\n--- КОНЕЦ ОТВЕТА ---"
                )

                try:
                    clean_content = _sanitize_json_string(content)
                    data = json.loads(clean_content)
                    full_go_name = data.get("full_go_name")

                    if full_go_name and isinstance(full_go_name, str):
                        return full_go_name.strip()
                    else:
                        log_to_terminal(
                            f"❌ Некорректный формат ответа или отсутствует ключ 'full_go_name' для {filename}."
                        )
                except json.JSONDecodeError:
                    log_to_terminal(f"❌ Ошибка декодирования JSON для {filename}.")

            except (APITimeoutError, APIConnectionError, RateLimitError) as e:
                log_to_terminal(
                    f"  [AI] Ошибка API для {filename} (попытка {attempt + 1}/{max_retries}): {type(e).__name__}. Повтор через {delay:.1f} сек..."
                )
                if attempt + 1 == max_retries:
                    log_to_terminal(
                        f"  [AI] ❌ Превышено количество попыток для {filename}."
                    )
                    return None
                time.sleep(delay)
                delay *= 2
            except Exception as e:
                log_to_terminal(
                    f"  [AI] ❌ Непредвиденная ошибка на этапе извлечения ГО для {filename}: {e}"
                )
                traceback.print_exc()
                return None
        return None

    @staticmethod
    def _save_report(final_rows, config):
        if not final_rows:
            log_to_terminal("Нет данных для сохранения. Отчет не создан.")
            messagebox.showwarning("Нет данных", "Не удалось извлечь ни одной функции.")
            return

        df = pd.DataFrame(final_rows)

        # Обновленная структура отчета
        final_cols_order = ["ID", "Госорган", "Вышестоящий", "Текст_функции"]
        df_final = df.reindex(columns=final_cols_order)

        folder_name = os.path.basename(config["input_folder"].rstrip("/\\"))
        suggested_filename = f"Итоговая матрица функций — {folder_name}.xlsx"

        output_file = filedialog.asksaveasfilename(
            title="Сохранить итоговый Excel как…",
            initialdir=config["input_folder"],
            initialfile=suggested_filename,
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not output_file:
            log_to_terminal("Сохранение отменено.")
            return

        try:
            df_final.to_excel(output_file, index=False, engine="openpyxl")
            log_to_terminal(f"🎉 Отчет сохранен: {output_file}")
            # Сообщение о сохранении файла теперь показывается вместе со статистикой в конце
        except Exception as e:
            log_to_terminal(f"❌ Ошибка при сохранении отчета: {e}")
            messagebox.showerror(
                "Ошибка сохранения", f"Не удалось сохранить Excel-файл.\n\nОшибка: {e}"
            )


if __name__ == "__main__":
    root = tk.Tk()
    app = ParserApp(root)
    app.build_ui()
    root.mainloop()
