# -*- coding: utf-8 -*-
"""
Итоговый комплексный анализатор коллизий v14.3 (Исправлено распределение нагрузки)
================================================================================
Автор: Gemini (на основе запросов пользователя Кастер)
Версия: 14.3 (15.08.2025)

Архитектура v14.3:
- ИСПРАВЛЕНА ЛОГИКА РАСПРЕДЕЛЕНИЯ НАГРУЗКИ: Вместо общего пула воркеров,
  использующих Round-Robin для выбора сервера, теперь для каждого локального
  сервера создается выделенная группа воркеров (по N штук). Это обеспечивает
  равномерную и предсказуемую нагрузку на все серверы.
- ОПТИМИЗИРОВАНА ПЕРЕДАЧА ПАРАМЕТРОВ: Функция _verification_worker теперь
  принимает один экземпляр клиента, что упрощает логику и устраняет
  необходимость в RoundRobin.

"""

import os
import json
import time
import queue
import threading
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Optional, Any, Set, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pandas as pd
import torch
import numpy as np

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
    from openai import (
        OpenAI,
        APIError,
        APIStatusError,
        APIConnectionError,
        RateLimitError,
        AuthenticationError,
    )
except ImportError:
    messagebox.showerror(
        "Зависимость не найдена",
        "Библиотека 'openai' не установлена.\nПожалуйста, установите ее: pip install openai",
    )
    OpenAI = None


# ====== Константы/настройки по умолчанию ======
TEXT_COLUMN = "FunctionText"
EXECUTOR_COLUMN = "Исполняющий ГО"
ID_COLUMN = "ID"
DEFAULT_GROUPING_COLUMNS = "Type, Sphere_3"
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
    "You are a highly qualified AI analyst specializing in identifying duplication in government functions. Your task is to perform a deep semantic and contextual analysis of two functions.\n\n"
    "**Context is crucial:** Always consider which agencies (Agency 1, Agency 2) are performing these functions. Sometimes, functions with similar wording have different meanings in the context of different agencies.\n\n"
    "**What to consider a real collision (verdict: CORRECT):**\n"
    "* The functions are completely identical in their essence, meaning, and final outcome. They describe the same action aimed at the same object.\n"
    "* The wording might differ slightly (use of synonyms, different word order), but the semantic core and the ultimate goal are the same.\n\n"
    "**What is NOT a collision (verdict: NOT_CORRECT):**\n"
    "1.  **Different Actions:** The functions describe fundamentally different tasks (e.g., one is about licensing, the other is about monitoring).\n"
    '2.  **Different Process Stages:** One function describes development/planning (e.g., "developing policy"), while the other describes execution/implementation ("implementing policy").\n'
    '3.  **Different Objects/Subjects:** The functions target different objects (e.g., "support for small businesses" vs. "support for large industrial enterprises").\n'
    '4.  **General vs. Specific:** One function is general and high-level (e.g., "ensuring national security"), while the other describes a specific, narrow task within that general function (e.g., "issuing passes to a secure facility").\n\n'
    'Respond only with JSON: {"verdict": "CORRECT"} or {"verdict": "NOT_CORRECT"}'
)


@dataclass
class ServerConfig:
    url: str
    embedding_model: str


# =============================== Утилиты ===============================
class RoundRobin:
    def __init__(self, n: int):
        if n <= 0:
            raise ValueError("Количество элементов должно быть больше нуля")
        self.n = n
        self.i = 0
        self.lock = threading.Lock()

    def next(self) -> int:
        with self.lock:
            idx = self.i
            self.i = (self.i + 1) % self.n
            return idx


def prepare_api_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        return url
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def cosine_matrix(x: torch.Tensor) -> torch.Tensor:
    x = x.to(dtype=torch.float32)
    x = torch.nn.functional.normalize(x, p=2, dim=1)
    return x @ x.T


def get_embedding_from_server(
    client: OpenAI, model: str, texts: List[str], retries: int = 3
) -> Optional[List[List[float]]]:
    for attempt in range(retries):
        try:
            resp = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in resp.data]
        except Exception as e:
            print(f"Embedding attempt {attempt + 1} failed: {e}")
            time.sleep(1)
    return None


def read_table_auto(fpath: str) -> pd.DataFrame:
    low = fpath.lower()
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(fpath)
    if low.endswith(".csv"):
        last_err: Optional[Exception] = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            for sep in (",", ";", "\t", "|"):
                try:
                    return pd.read_csv(fpath, encoding=enc, sep=sep)
                except Exception as e:
                    last_err = e
        if last_err:
            raise last_err
    raise ValueError(f"Неподдерживаемый формат файла: {fpath}")


# =============================== Приложение ===============================
class CollisionAnalyzerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Анализатор коллизий v14.3 (Распределение нагрузки)")
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
                elif command == "update_candidates_count":
                    self.candidates_found_var.set(f"Найдено кандидатов: {value}")
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

        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def log(self, message: str, to_terminal: bool = False):
        self.ui_queue.put(("log", message))
        if to_terminal:
            print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def set_status(self, text: str):
        self.ui_queue.put(("status", text))

    def update_candidates_count(self, count: int):
        self.ui_queue.put(("update_candidates_count", count))

    def reset_progress(self, bar_name: str, total: int):
        self.ui_queue.put(("progress", ("reset", (bar_name, total))))

    def update_progress(self, bar_name: str, increment: int):
        self.ui_queue.put(("progress", (bar_name, increment)))

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=450)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))

        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- ЛЕВАЯ ПАНЕЛЬ: НАСТРОЙКИ ---

        # 1. Файлы и группировка
        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Данные и группировка")
        files_frame.pack(fill=tk.X, pady=5)
        self.input_file_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self.universal_json_var = tk.StringVar()

        self._create_file_row(
            files_frame, "Исходный файл:", self.input_file_var, self._choose_input_file
        )
        self._create_file_row(
            files_frame,
            "Папка для отчета:",
            self.output_dir_var,
            self._choose_output_dir,
        )
        self._create_file_row(
            files_frame,
            "JSON универс. функций:",
            self.universal_json_var,
            self._choose_json,
        )

        # New UI for grouping columns: two listboxes with add/remove buttons
        grouping_selection_frame = ttk.LabelFrame(
            files_frame, text="Столбцы для группировки"
        )
        grouping_selection_frame.pack(fill=tk.X, pady=5, padx=5)

        listboxes_frame = ttk.Frame(grouping_selection_frame)
        listboxes_frame.pack(fill=tk.BOTH, expand=True)

        # Available Columns Listbox
        available_frame = ttk.Frame(listboxes_frame)
        available_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        ttk.Label(available_frame, text="Доступные:").pack(anchor=tk.W)
        self.available_cols_listbox = tk.Listbox(
            available_frame, selectmode=tk.EXTENDED, height=6
        )
        self.available_cols_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        available_scrollbar = ttk.Scrollbar(
            available_frame,
            orient="vertical",
            command=self.available_cols_listbox.yview,
        )
        available_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.available_cols_listbox.config(yscrollcommand=available_scrollbar.set)

        # Buttons Frame
        buttons_frame = ttk.Frame(listboxes_frame)
        buttons_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(buttons_frame, text=">>>", command=self._add_grouping_column).pack(
            pady=5
        )
        ttk.Button(
            buttons_frame, text="<<<", command=self._remove_grouping_column
        ).pack(pady=5)

        # Selected Grouping Columns Listbox
        selected_frame = ttk.Frame(listboxes_frame)
        selected_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        ttk.Label(selected_frame, text="Выбранные:").pack(anchor=tk.W)
        self.selected_grouping_cols_listbox = tk.Listbox(
            selected_frame, selectmode=tk.EXTENDED, height=6
        )
        self.selected_grouping_cols_listbox.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True
        )
        selected_scrollbar = ttk.Scrollbar(
            selected_frame,
            orient="vertical",
            command=self.selected_grouping_cols_listbox.yview,
        )
        selected_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.selected_grouping_cols_listbox.config(
            yscrollcommand=selected_scrollbar.set
        )

        # 2. Настройки эмбеддингов
        embed_frame = ttk.LabelFrame(left_panel, text="Этап 2: Поиск (Эмбеддинги)")
        embed_frame.pack(fill=tk.X, pady=5)
        self.sim_threshold_var = tk.DoubleVar(value=DEFAULT_SIMILARITY_THRESHOLD)
        self.univ_threshold_var = tk.DoubleVar(
            value=DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD
        )
        self.embedding_workers_var = tk.IntVar(value=DEFAULT_EMBEDDING_WORKERS)
        self.batch_size_var = tk.IntVar(value=DEFAULT_BATCH_SIZE)
        self.embedding_model_var = tk.StringVar(value=DEFAULT_EMBEDDING_MODEL_NAME)
        self.embed_server_var = tk.StringVar(value="http://localhost:1234")

        self._create_entry_row(
            embed_frame, "Сервер эмбеддингов:", self.embed_server_var
        )
        self._create_entry_row(
            embed_frame, "Модель эмбеддингов:", self.embedding_model_var
        )
        self._create_entry_row(embed_frame, "Порог похожести:", self.sim_threshold_var)
        self._create_entry_row(
            embed_frame, "Порог универсальных:", self.univ_threshold_var
        )
        self._create_entry_row(
            embed_frame, "Воркеров эмбеддинга:", self.embedding_workers_var
        )
        self._create_entry_row(
            embed_frame, "Размер пакета (batch):", self.batch_size_var
        )

        # 3. Настройки AI Верификации
        ai_frame = ttk.LabelFrame(left_panel, text="Этап 3: Анализ (AI-верификация)")
        ai_frame.pack(fill=tk.X, pady=5)

        self.ai_verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            ai_frame, text="Включить AI-проверку", variable=self.ai_verify_var
        ).pack(anchor="w", padx=5, pady=(5, 0))

        self.verification_mode_var = tk.StringVar(value="Онлайн")
        mode_frame = ttk.Frame(ai_frame)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(
            mode_frame,
            text="Онлайн (OpenAI API)",
            variable=self.verification_mode_var,
            value="Онлайн",
            command=self._on_verification_mode_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="Локальная (LM Studio)",
            variable=self.verification_mode_var,
            value="Локальная",
            command=self._on_verification_mode_change,
        ).pack(side=tk.LEFT, padx=10)

        # -- Онлайн-настройки --
        self.online_frame = ttk.Frame(ai_frame)
        self.openai_api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.chat_model_var = tk.StringVar(value=DEFAULT_CHAT_MODEL_NAME)
        self.ai_workers_var = tk.IntVar(value=DEFAULT_AI_WORKERS)
        self._create_entry_row(
            self.online_frame, "Ключ OpenAI API:", self.openai_api_key_var, show="*"
        )
        self._create_entry_row(self.online_frame, "Модель OpenAI:", self.chat_model_var)
        self._create_entry_row(
            self.online_frame, "Воркеров верификации:", self.ai_workers_var
        )

        # -- Локальные-настройки --
        self.local_frame = ttk.Frame(ai_frame)
        self.local_model_var = tk.StringVar(value="local-model/gguf-model-name")
        self._create_entry_row(
            self.local_frame, "Локальная модель:", self.local_model_var
        )
        ttk.Label(
            self.local_frame,
            text=f"Будет ~{WORKERS_PER_LOCAL_SERVER} воркера на сервер.",
        ).pack(anchor="w", padx=5)
        ttk.Label(self.local_frame, text="Адреса серверов LM Studio:").pack(
            anchor="w", pady=(5, 0), padx=5
        )
        self.local_servers_text = tk.Text(self.local_frame, height=3)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=2, padx=5)
        self.local_servers_text.insert(tk.END, "http://localhost:1234\n")

        self._on_verification_mode_change()  # Показать/скрыть нужные фреймы

        # -- Общие настройки AI --
        self.temperature_var = tk.DoubleVar(value=DEFAULT_TEMPERATURE)
        self.max_tokens_var = tk.IntVar(value=DEFAULT_MAX_TOKENS)
        self.json_mode_var = tk.BooleanVar(value=True)

        common_ai_frame = ttk.Frame(ai_frame)
        common_ai_frame.pack(fill=tk.X, padx=0, pady=0)

        self._create_entry_row(
            common_ai_frame, "Температура (0.0-2.0):", self.temperature_var
        )
        self._create_entry_row(
            common_ai_frame, "Макс. токенов ответа:", self.max_tokens_var
        )
        ttk.Checkbutton(
            common_ai_frame,
            text="JSON режим (только для Онлайн)",
            variable=self.json_mode_var,
        ).pack(anchor="w", padx=5, pady=3)

        # --- ПРАВАЯ ПАНЕЛЬ: УПРАВЛЕНИЕ, ПРОГРЕСС, ЛОГИ ---

        # 1. Управление
        controls_frame = ttk.LabelFrame(right_panel, text="Управление")
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

        # 2. Прогресс
        progress_frame = ttk.LabelFrame(right_panel, text="Прогресс по этапам")
        progress_frame.pack(fill=tk.X, pady=5)
        self.progress_bars = {}

        # Progress Bar for Discovery
        discovery_frame = ttk.Frame(progress_frame)
        discovery_frame.pack(fill=tk.X, padx=5, pady=(8, 0))
        ttk.Label(discovery_frame, text="Поиск кандидатов (группы):").pack(side=tk.LEFT)
        self.etr_labels["discovery"] = ttk.Label(discovery_frame, text="ETR: --:--")
        self.etr_labels["discovery"].pack(side=tk.RIGHT)
        self.progress_bars["discovery"] = ttk.Progressbar(progress_frame)
        self.progress_bars["discovery"].pack(fill=tk.X, padx=5, pady=(2, 0))
        self.candidates_found_var = tk.StringVar(value="Найдено кандидатов: 0")
        self.candidates_found_label = ttk.Label(
            progress_frame, textvariable=self.candidates_found_var
        )
        self.candidates_found_label.pack(fill=tk.X, padx=7, pady=(0, 5))

        # Progress Bar for Verification
        verification_frame = ttk.Frame(progress_frame)
        verification_frame.pack(fill=tk.X, padx=5, pady=(8, 0))
        ttk.Label(verification_frame, text="AI-верификация (пары):").pack(side=tk.LEFT)
        self.etr_labels["verification"] = ttk.Label(
            verification_frame, text="ETR: --:--"
        )
        self.etr_labels["verification"].pack(side=tk.RIGHT)
        self.progress_bars["verification"] = ttk.Progressbar(progress_frame)
        self.progress_bars["verification"].pack(fill=tk.X, padx=5, pady=(2, 5))

        # 3. Промпт и Логи
        notebook = ttk.Notebook(right_panel)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # Вкладка с промптом
        prompt_frame = ttk.Frame(notebook, padding=10)
        notebook.add(prompt_frame, text="Системный промпт для AI")
        self.ai_prompt_text = tk.Text(prompt_frame, height=8, wrap=tk.WORD)
        self.ai_prompt_text.pack(fill=tk.BOTH, expand=True)
        self.ai_prompt_text.insert(tk.END, DEFAULT_AI_SYSTEM_PROMPT)

        # Вкладка с логами
        log_frame = ttk.Frame(notebook, padding=10)
        notebook.add(log_frame, text="Логи выполнения")
        self.log_text = tk.Text(log_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.status_label = ttk.Label(status_frame, text="Готово")
        self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)

    def _on_verification_mode_change(self):
        if self.verification_mode_var.get() == "Онлайн":
            self.local_frame.pack_forget()
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            self.online_frame.pack_forget()
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    def _create_file_row(self, parent, label, var, cmd):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=20).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    def _create_entry_row(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=20).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )

    def _update_column_listboxes(self, columns: List[str]):
        self.available_cols_listbox.delete(0, tk.END)
        self.selected_grouping_cols_listbox.delete(0, tk.END)

        default_cols = [c.strip() for c in DEFAULT_GROUPING_COLUMNS.split(",")]
        available_to_add = []
        selected_to_add = []

        for col in sorted(columns):  # Sort columns alphabetically
            if col in default_cols:
                selected_to_add.append(col)
            else:
                available_to_add.append(col)

        for col in available_to_add:
            self.available_cols_listbox.insert(tk.END, col)
        for col in selected_to_add:
            self.selected_grouping_cols_listbox.insert(tk.END, col)

    def _choose_input_file(self):
        f = filedialog.askopenfilename(
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"), ("Все", "*.*")]
        )
        if f:
            self.input_file_var.set(f)
            try:
                temp_df = read_table_auto(f)
                self._update_column_listboxes(temp_df.columns.tolist())
            except Exception as e:
                messagebox.showerror(
                    "Ошибка чтения файла",
                    f"Не удалось прочитать файл для получения заголовков: {e}",
                )
                self.available_cols_listbox.delete(
                    0, tk.END
                )  # Clear listboxes on error
                self.selected_grouping_cols_listbox.delete(0, tk.END)

    def _choose_output_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir_var.set(d)

    def _choose_json(self):
        f = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("Все", "*.*")])
        if f:
            self.universal_json_var.set(f)

    def _add_grouping_column(self):
        selected_indices = self.available_cols_listbox.curselection()
        for i in selected_indices[::-1]:  # Process in reverse to avoid index issues
            col = self.available_cols_listbox.get(i)
            if col not in self.selected_grouping_cols_listbox.get(0, tk.END):
                self.selected_grouping_cols_listbox.insert(tk.END, col)
            self.available_cols_listbox.delete(i)

    def _remove_grouping_column(self):
        selected_indices = self.selected_grouping_cols_listbox.curselection()
        for i in selected_indices[::-1]:  # Process in reverse to avoid index issues
            col = self.selected_grouping_cols_listbox.get(i)
            if col not in self.available_cols_listbox.get(0, tk.END):
                self.available_cols_listbox.insert(tk.END, col)
            self.selected_grouping_cols_listbox.delete(i)
        # Re-sort available columns
        self._sort_listbox(self.available_cols_listbox)

    def _sort_listbox(self, listbox):
        items = list(listbox.get(0, tk.END))
        listbox.delete(0, tk.END)
        for item in sorted(items):  # Sort alphabetically
            listbox.insert(tk.END, item)

    def _tick_ui(self):
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(
                text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}"
            )
        self.root.after(100, self._tick_ui)

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return messagebox.showinfo("Выполняется", "Анализ уже запущен")

        try:
            selected_grouping_cols = list(
                self.selected_grouping_cols_listbox.get(0, tk.END)
            )
            if not selected_grouping_cols:
                raise ValueError("Укажите хотя бы один столбец для группировки.")

            config = {
                "input_file": self.input_file_var.get().strip(),
                "output_dir": self.output_dir_var.get().strip(),
                "grouping_cols": selected_grouping_cols,  # Changed from grouping_cols_str
                "univ_json_path": self.universal_json_var.get().strip(),
                "sim_thr": self.sim_threshold_var.get(),
                "univ_thr": self.univ_threshold_var.get(),
                "embedding_workers": self.embedding_workers_var.get(),
                "batch_size": self.batch_size_var.get(),
                "embed_server_url": self.embed_server_var.get().strip(),
                "embed_model": self.embedding_model_var.get().strip(),
                "ai_should_verify": self.ai_verify_var.get(),
                "verification_mode": self.verification_mode_var.get(),
                "ai_system_prompt": self.ai_prompt_text.get("1.0", tk.END).strip(),
                "ai_temperature": self.temperature_var.get(),
                "ai_max_tokens": self.max_tokens_var.get(),
                "use_json_mode": self.json_mode_var.get(),
            }
            if not config["input_file"] or not os.path.isfile(config["input_file"]):
                raise ValueError("Укажите корректный исходный файл.")
            if not config["output_dir"] or not os.path.isdir(config["output_dir"]):
                raise ValueError("Укажите корректную папку для отчета.")
            # if not config['grouping_cols_str']: raise ValueError("Укажите хотя бы один столбец для группировки.") # Removed
            if config["ai_max_tokens"] <= 0:
                raise ValueError("Максимальное количество токенов должно быть > 0.")

            if config["ai_should_verify"]:
                if config["verification_mode"] == "Онлайн":
                    config["openai_api_key"] = self.openai_api_key_var.get().strip()
                    config["ai_chat_model"] = self.chat_model_var.get().strip()
                    config["ai_workers"] = self.ai_workers_var.get()
                    if not config["openai_api_key"]:
                        raise ValueError(
                            "В режиме 'Онлайн' необходимо указать ключ OpenAI API."
                        )
                    if config["ai_workers"] <= 0:
                        raise ValueError(
                            "Количество воркеров верификации должно быть > 0."
                        )
                else:  # Локальная
                    local_servers = (
                        self.local_servers_text.get("1.0", tk.END).strip().splitlines()
                    )
                    config["local_servers"] = [
                        s.strip() for s in local_servers if s.strip()
                    ]
                    config["local_chat_model"] = self.local_model_var.get().strip()
                    if not config["local_servers"]:
                        raise ValueError(
                            "В режиме 'Локальная' нужен хотя бы один адрес сервера."
                        )
                    # Общее число воркеров теперь не нужно, так как оно определяется динамически
                    # config['ai_workers'] = len(config['local_servers']) * WORKERS_PER_LOCAL_SERVER

        except (ValueError, tk.TclError) as e:
            return messagebox.showerror("Ошибка в настройках", str(e))

        self._stop_flag.clear()
        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log("=" * 50)
        self.log("ЗАПУСК АНАЛИЗА КОЛЛИЗИЙ")
        self.candidates_found_var.set("Найдено кандидатов: 0")

        self._worker_thread = threading.Thread(
            target=self._worker_main, args=(config,), daemon=True
        )
        self._worker_thread.start()

    def _on_stop(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self.log(
                "Запрошена остановка... Завершаю текущие операции.", to_terminal=True
            )
            self._stop_flag.set()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_worker_finished(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._timer_running = False
        final_message = (
            "Готово."
            if not self._stop_flag.is_set()
            else "Процесс остановлен пользователем."
        )
        self.set_status(final_message)
        self.log(final_message.upper())
        self.log("=" * 50)

    def _worker_main(self, config: dict):
        try:
            os.makedirs(config["output_dir"], exist_ok=True)

            # --- Этап 0: Подготовка ---
            self.set_status("Подготовка: загрузка данных и моделей...")
            self.log("Этап 0: Подготовка...")

            # Клиент для эмбеддингов
            embed_server_url = prepare_api_base_url(config["embed_server_url"])
            if not embed_server_url:
                raise ValueError("Не указан адрес сервера эмбеддингов.")
            embed_client = OpenAI(base_url=embed_server_url, api_key="not-needed")

            # Загрузка универсальных функций
            universal_texts = self._load_universal_texts(config["univ_json_path"])
            universal_embeddings = []
            if universal_texts:
                self.log(
                    f"Векторизация {len(universal_texts)} универсальных функций..."
                )
                univ_batches = [
                    universal_texts[i : i + config["batch_size"]]
                    for i in range(0, len(universal_texts), config["batch_size"])
                ]
                for batch in univ_batches:
                    if self._stop_flag.is_set():
                        return
                    if embs := get_embedding_from_server(
                        embed_client, config["embed_model"], batch
                    ):
                        universal_embeddings.extend(embs)

            # Загрузка основной таблицы
            self.log(
                f"Чтение исходного файла: {os.path.basename(config['input_file'])}"
            )
            source_df = read_table_auto(config["input_file"])
            source_df[ID_COLUMN] = source_df[ID_COLUMN].astype(str)
            grouping_cols = config["grouping_cols"]  # Directly use the list of columns
            missing_cols = [
                col for col in grouping_cols if col not in source_df.columns
            ]
            if missing_cols:
                raise ValueError(
                    f"В файле отсутствуют столбцы для группировки: {', '.join(missing_cols)}"
                )

            grouped_data = source_df.groupby(grouping_cols)
            total_groups = grouped_data.ngroups
            self.log(
                f"Данные сгруппированы. Найдено {total_groups} уникальных групп для анализа."
            )

            # --- ФАЗА 1: ПОИСК КАНДИДАТОВ ПО ГРУППАМ ---
            self.log("ФАЗА 1: Поиск кандидатов в группах...")
            self.reset_progress("discovery", total_groups)
            all_candidate_pairs = set()

            for i, (group_name, group_df) in enumerate(grouped_data):
                if self._stop_flag.is_set():
                    return
                if isinstance(group_name, tuple):
                    display_group_name = ", ".join(
                        [f"{col}: {val}" for col, val in zip(grouping_cols, group_name)]
                    )
                else:
                    display_group_name = group_name
                self.set_status(f"Группа {i + 1}/{total_groups}: {display_group_name}")

                group_df_for_analysis = group_df.copy()
                if "Type" in group_df_for_analysis.columns:
                    general_funcs_mask = (
                        group_df_for_analysis["Type"] == "Общие функции"
                    )
                    group_df_for_analysis = group_df_for_analysis[~general_funcs_mask]

                if len(group_df_for_analysis) < 2:
                    self.log(
                        f"Группа '{group_name}' ({len(group_df)} строк) пропущена - недостаточно данных для сравнения."
                    )
                    self.update_progress("discovery", 1)
                    continue

                file_embeddings = self._calculate_embeddings_for_df(
                    group_df_for_analysis, embed_client, config
                )
                if self._stop_flag.is_set():
                    return

                candidate_pairs_for_group = self._find_candidate_pairs(
                    group_df_for_analysis,
                    file_embeddings,
                    universal_embeddings,
                    config["sim_thr"],
                    config["univ_thr"],
                )

                if candidate_pairs_for_group:
                    self.log(
                        f"В группе '{group_name}' найдено кандидатов: {len(candidate_pairs_for_group)}"
                    )
                    all_candidate_pairs.update(candidate_pairs_for_group)
                    self.update_candidates_count(len(all_candidate_pairs))
                self.update_progress("discovery", 1)

            self.log(
                f"Поиск завершен. Всего найдено уникальных кандидатов: {len(all_candidate_pairs)}"
            )
            self.update_candidates_count(len(all_candidate_pairs))

            # --- ФАЗА 2: ГЛОБАЛЬНАЯ AI-ВЕРИФИКАЦИЯ ---
            confirmed_pairs: Set[Tuple[str, str]] = set()
            if config["ai_should_verify"] and all_candidate_pairs:
                self.log(
                    f"ФАЗА 2: Запуск AI-верификации в режиме '{config['verification_mode']}'..."
                )
                llm_clients = []
                try:
                    if config["verification_mode"] == "Онлайн":
                        llm_clients.append(OpenAI(api_key=config["openai_api_key"]))
                    else:  # Локальная
                        for url in config["local_servers"]:
                            llm_clients.append(
                                OpenAI(
                                    base_url=prepare_api_base_url(url),
                                    api_key="not-needed",
                                )
                            )
                    if not llm_clients:
                        raise ValueError("Не удалось создать AI-клиентов.")
                except Exception as e:
                    self.log(
                        f"Ошибка инициализации AI-клиента: {e}. Верификация будет пропущена.",
                        to_terminal=True,
                    )
                    confirmed_pairs = all_candidate_pairs
                else:
                    task_queue = queue.Queue()
                    [task_queue.put(p) for p in all_candidate_pairs]
                    confirmed_lock = threading.Lock()
                    df_indexed = source_df.set_index(ID_COLUMN)

                    total_tasks = len(all_candidate_pairs)
                    self.reset_progress("verification", total_tasks)

                    threads = []
                    # --- ИЗМЕНЕННАЯ ЛОГИКА СОЗДАНИЯ ПОТОКОВ ---
                    if config["verification_mode"] == "Онлайн":
                        # Для онлайн-режима создаем воркеры для одного клиента
                        client = llm_clients[0]
                        model_name = config["ai_chat_model"]
                        for _ in range(config["ai_workers"]):
                            thread = threading.Thread(
                                target=self._verification_worker,
                                args=(
                                    task_queue,
                                    confirmed_pairs,
                                    confirmed_lock,
                                    df_indexed,
                                    client,
                                    model_name,
                                    config,
                                ),
                            )
                            threads.append(thread)
                    else:  # Локальная
                        # Для локального режима создаем по N воркеров на КАЖДЫЙ сервер
                        model_name = config["local_chat_model"]
                        for client in (
                            llm_clients
                        ):  # llm_clients содержит по одному клиенту на сервер
                            for _ in range(WORKERS_PER_LOCAL_SERVER):
                                thread = threading.Thread(
                                    target=self._verification_worker,
                                    args=(
                                        task_queue,
                                        confirmed_pairs,
                                        confirmed_lock,
                                        df_indexed,
                                        client,
                                        model_name,
                                        config,
                                    ),
                                )
                                threads.append(thread)

                    self.log(f"Создано {len(threads)} воркеров для AI-верификации.")
                    [t.start() for t in threads]
                    # --- КОНЕЦ ИЗМЕНЕННОЙ ЛОГИКИ ---

                    while any(t.is_alive() for t in threads):
                        if self._stop_flag.is_set():
                            break
                        time.sleep(0.5)
                    [t.join() for t in threads]
                    self.log(
                        f"AI-верификация завершена. Подтверждено коллизий: {len(confirmed_pairs)}"
                    )
            else:
                self.log("AI-верификация пропущена или нет кандидатов.")
                confirmed_pairs = all_candidate_pairs

            # --- ФАЗА 3: СОХРАНЕНИЕ ОТЧЕТА ---
            if self._stop_flag.is_set():
                return
            self.log("ФАЗА 3: Формирование и сохранение итогового отчета...")
            self.set_status("Формирование отчета...")

            final_df = source_df.copy()
            confirmed_dict = defaultdict(set)
            for id1, id2 in confirmed_pairs:
                confirmed_dict[id1].add(id2)
                confirmed_dict[id2].add(id1)

            if "Anomaly" not in final_df.columns:
                final_df["Anomaly"] = ""
            if "CollisionWith" not in final_df.columns:
                final_df["CollisionWith"] = ""

            for idx, row in final_df.iterrows():
                if (row_id := str(row[ID_COLUMN])) in confirmed_dict:
                    final_df.loc[idx, "Anomaly"] = "Collision"
                    final_df.loc[idx, "CollisionWith"] = "; ".join(
                        sorted(confirmed_dict[row_id])
                    )

            output_fname = (
                f"Сводный_отчет_по_коллизиям_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
            )
            output_path = os.path.join(config["output_dir"], output_fname)
            final_df.to_excel(output_path, index=False)
            self.log(f"Сохранен сводный отчет: {output_path}")

        except Exception as e:
            import traceback

            self.log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", to_terminal=True)
            self.log(f"Traceback: {traceback.format_exc()}", to_terminal=True)
            self.set_status(f"Ошибка: {e}")
        finally:
            self.ui_queue.put(("worker_done", None))

    def _calculate_embeddings_for_df(
        self, df: pd.DataFrame, client: OpenAI, config: dict
    ) -> Dict[int, List[float]]:
        texts = df[TEXT_COLUMN].fillna("").astype(str)
        valid_rows = list(texts[texts.str.strip().ne("")].items())
        if not valid_rows:
            return {}

        batches = [
            valid_rows[i : i + config["batch_size"]]
            for i in range(0, len(valid_rows), config["batch_size"])
        ]
        task_queue = queue.Queue()
        [task_queue.put(b) for b in batches]
        embeddings = {}
        lock = threading.Lock()
        threads = []

        for _ in range(config["embedding_workers"]):
            thread = threading.Thread(
                target=self._embedding_worker,
                args=(task_queue, embeddings, lock, client, config["embed_model"]),
            )
            threads.append(thread)
            thread.start()
        for t in threads:
            t.join()
        return embeddings

    def _embedding_worker(
        self,
        task_queue: queue.Queue,
        results_dict: Dict,
        lock: threading.Lock,
        client: OpenAI,
        model_name: str,
    ):
        while not task_queue.empty():
            if self._stop_flag.is_set():
                break
            try:
                batch = task_queue.get_nowait()
                indices, texts_to_embed = (
                    [item[0] for item in batch],
                    [item[1] for item in batch],
                )
                list_of_embeddings = get_embedding_from_server(
                    client, model_name, texts_to_embed
                )
                if list_of_embeddings and len(list_of_embeddings) == len(indices):
                    with lock:
                        for i, embedding in enumerate(list_of_embeddings):
                            results_dict[indices[i]] = embedding
                task_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                self.log(f"Ошибка в воркере эмбеддингов: {e}")
                break

    def _get_llm_verdict(
        self,
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
        if self._stop_flag.is_set():
            return "STOPPED", None
        user_prompt = f"Agency 1: {go1}\nFunction 1: {func1}\n\nAgency 2: {go2}\nFunction 2: {func2}\n"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
        }

        if use_json_mode and verification_mode == "Онлайн":
            request_params["response_format"] = {"type": "json_object"}

        last_error_message = "Unknown error after 3 retries"
        for _ in range(3):
            try:
                resp = client.chat.completions.create(**request_params)
                content = resp.choices[0].message.content
                match = re.search(r"\{.*\}", content, re.DOTALL)
                if match:
                    verdict = json.loads(match.group(0)).get("verdict")
                    if verdict in ("CORRECT", "NOT_CORRECT"):
                        return verdict, None
                last_error_message = (
                    f"Could not parse JSON or find valid verdict in response: {content}"
                )
            except AuthenticationError as e:
                return (
                    "AUTH_ERROR",
                    f"Authentication Error: {e.body.get('message', str(e)) if e.body else str(e)}",
                )
            except APIStatusError as e:
                return "ERROR", f"API Error (Status {e.status_code}): {e.response.text}"
            except (APIConnectionError, RateLimitError) as e:
                last_error_message = f"Connection/Rate Limit Error: {str(e)}"
                time.sleep(1)
            except Exception as e:
                last_error_message = str(e)
        return "ERROR", last_error_message

    # --- ИЗМЕНЕННАЯ ФУНКЦИЯ-ВОРКЕР ---
    def _verification_worker(
        self,
        task_queue: queue.Queue,
        confirmed_pairs: Set,
        lock: threading.Lock,
        df_indexed: pd.DataFrame,
        client: OpenAI,
        model_name: str,
        config: Dict,
    ):
        while not task_queue.empty():
            if self._stop_flag.is_set():
                break
            try:
                id1, id2 = task_queue.get_nowait()
                try:
                    row1, row2 = df_indexed.loc[id1], df_indexed.loc[id2]
                except KeyError as e:
                    self.log(
                        f"ОШИБКА: ID {e} не найден. Пропускаю пару ({id1}, {id2})."
                    )
                    continue

                # Строка 'client = clients[client_rr.next()]' была удалена,
                # так как 'client' теперь передается напрямую.

                status, message = self._get_llm_verdict(
                    client,
                    model_name,
                    config["ai_system_prompt"],
                    row1[EXECUTOR_COLUMN],
                    row1[TEXT_COLUMN],
                    row2[EXECUTOR_COLUMN],
                    row2[TEXT_COLUMN],
                    config["use_json_mode"],
                    config["ai_temperature"],
                    config["verification_mode"],
                    config["ai_max_tokens"],
                )
                if status == "CORRECT":
                    with lock:
                        confirmed_pairs.add(tuple(sorted((id1, id2))))
                elif status == "AUTH_ERROR":
                    self.log(f"ОШИБКА АУТЕНТИФИКАЦИИ: {message}", to_terminal=True)
                    self.log(
                        "Ошибка аутентификации. Проверьте API ключ. Воркер остановлен."
                    )
                    task_queue.put((id1, id2))
                    break
                elif status == "ERROR":
                    self.log(
                        f"ОШИБКА API (Пара {id1}, {id2}): {message}", to_terminal=True
                    )

            except queue.Empty:
                break
            except Exception as e:
                import traceback

                self.log(
                    f"Критическая ошибка в воркере верификации: {e}\n{traceback.format_exc()}",
                    to_terminal=True,
                )
                break
            finally:
                self.update_progress("verification", 1)
                task_queue.task_done()

    def _load_universal_texts(self, univ_json_path: str) -> List[str]:
        if not (univ_json_path and os.path.isfile(univ_json_path)):
            return []
        try:
            with open(univ_json_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            tmp = set()

            def collect(node: Any):
                if isinstance(node, dict):
                    if (
                        "function" in node
                        and isinstance(val := node["function"], str)
                        and val.strip()
                    ):
                        tmp.add(val.strip())
                    for v in node.values():
                        collect(v)
                elif isinstance(node, list):
                    for it in node:
                        collect(it)

            collect(data)
            return sorted(list(tmp))
        except Exception as e:
            self.log(f"Не удалось прочитать JSON универсальных функций: {e}")
            return []

    def _find_candidate_pairs(
        self,
        df: pd.DataFrame,
        file_embeddings: Dict,
        universal_embeddings: List,
        sim_thr: float,
        univ_thr: float,
    ) -> Set[Tuple[str, str]]:
        if not file_embeddings:
            return set()

        idx_keep = sorted(file_embeddings.keys())
        emb_keep = torch.tensor(
            [file_embeddings[i] for i in idx_keep], dtype=torch.float32
        )

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
        candidate_pairs = set()

        if len(to_analyze_global_idx) > 1:
            df_analyze = df.loc[to_analyze_global_idx]
            emb_analyze = emb_keep[non_universal_indices_local]
            cos_scores = cosine_matrix(emb_analyze)

            exec_series = df_analyze[EXECUTOR_COLUMN].astype(str).values
            diff_exec_mask = torch.from_numpy(~np.equal.outer(exec_series, exec_series))

            edges_mask = (
                (cos_scores >= sim_thr)
                & diff_exec_mask
                & torch.triu(torch.ones_like(cos_scores), 1).bool()
            )
            edge_i, edge_j = torch.nonzero(edges_mask, as_tuple=True)

            ids_analyze = df_analyze[ID_COLUMN].values
            for a, b in zip(edge_i.tolist(), edge_j.tolist()):
                pair = tuple(sorted((str(ids_analyze[a]), str(ids_analyze[b]))))
                candidate_pairs.add(pair)
        return candidate_pairs


def main():
    if OpenAI is None:
        return
    root = tk.Tk()
    CollisionAnalyzerApp(root)
    root.mainloop()


if __name__ == "__main__":
    # Для Windows, чтобы избежать проблем с asyncio в потоках
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    main()
