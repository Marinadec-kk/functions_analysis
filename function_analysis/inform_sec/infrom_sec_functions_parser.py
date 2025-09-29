#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Анализатор функций информационной безопасности v1.5
================================================================================
Автор: Gemini (на основе запросов и архитектуры от пользователя Кастер)
Версия: 1.5 (22.08.2025)

Архитектура v1.5:
- ИСПРАВЛЕНИЕ: Устранена критическая ошибка 'No engine for filetype' при
  сохранении отчета. Добавлена проверка на наличие библиотеки 'openpyxl' и
  явное указание движка при сохранении в .xlsx.
- НОВОЕ (v1.4): Добавлено отображение примерного времени до завершения (ETA) для
  каждого этапа анализа над соответствующими прогресс-барами.
- ИСПРАВЛЕНИЕ (v1.3): Устранен сбой в онлайн-режиме из-за ошибки "Unsupported
  parameter: 'max_tokens'".
- ИСПРАВЛЕНИЕ (v1.2): Устранена ошибка 'AttributeError' с httpx.Timeout.
- Двухэтапный AI-анализ (Классификатор + Аудитор).
- Графический интерфейс (GUI) на Tkinter.
- Асинхронная обработка для параллельных запросов к AI.
- Гибкая настройка AI (Онлайн/Локальный).
- Детальное логирование и отображение прогресса.
"""

import os
import sys
import json
import time
import queue
import threading
import re
import traceback
from typing import List, Dict, Optional, Any, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pandas as pd

# --- ЗАВИСИМОСТИ ---
try:
    # Для красивого интерфейса, опционально
    import sv_ttk
except ImportError:
    print("="*80)
    print("ПРЕДУПРЕЖДЕНИЕ: Не найдена тема оформления 'sv-ttk'.")
    print("Интерфейс будет стандартным. Для улучшения вида установите тему: pip install sv-ttk")
    print("="*80)
    sv_ttk = None

try:
    # Основная зависимость для работы с AI
    from openai import AsyncOpenAI, APIConnectionError, RateLimitError, AuthenticationError, APIStatusError, BadRequestError
    import httpx # Необходим для установки таймаутов
except ImportError:
    messagebox.showerror("Зависимость не найдена", "Библиотека 'openai' или 'httpx' не установлена.\nПожалуйста, установите их: pip install openai httpx")
    AsyncOpenAI = None

try:
    # Движок для записи в .xlsx файлы
    import openpyxl
except ImportError:
    messagebox.showerror("Зависимость не найдена", "Библиотека 'openpyxl' не установлена.\nОна необходима для сохранения отчетов в формате Excel (.xlsx).\n\nПожалуйста, установите ее: pip install openpyxl")
    openpyxl = None # Это предотвратит запуск, если openpyxl отсутствует

import asyncio

# ====== Константы и настройки по умолчанию ======

# --- Названия колонок ---
COL_TEXT = "FunctionText"
COL_ID = "ID"
# Служебные колонки больше не используются в выводе, но константы можно оставить для внутренней логики
COL_AI1_VERDICT = "AI_1_Verdict"
COL_AI2_VERDICT = "AI_2_Verdict"

# --- Настройки AI по умолчанию ---
DEFAULT_ONLINE_MODEL = "gpt-4o-mini"
DEFAULT_LOCAL_MODEL = "local-model/gguf-model-name"
DEFAULT_LOCAL_SERVERS = "http://localhost:1234\n"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_AI_WORKERS_ONLINE = 30
WORKERS_PER_LOCAL_SERVER = 4

# --- Промпты по умолчанию ---
DEFAULT_PROMPT_AI1 = """Твоя задача — проанализировать текст функции госоргана и определить, имеет ли она какое-либо отношение к информационной безопасности.

Считай функцию связанной, если в ней упоминается:
- Защита данных, информации, информационных систем.
- Кибербезопасность, кибератаки, инциденты.
- Персональные данные.
- Электронная подпись, шифрование, аутентификация.

Даже если упоминание косвенное, классифицируй ее как связанную. Не нужно глубоко анализировать контекст на этом этапе.

Ответь ТОЛЬКО в формате JSON: {"verdict": "IS_RELATED"} или {"verdict": "NOT_RELATED"}.
"""

DEFAULT_PROMPT_AI2 = """### РОЛЬ И ЗАДАЧА
Ты — главный эксперт-аудитор по вопросам информационной безопасности в госуправлении. Первый AI-аналитик предположил, что следующая функция относится к ИБ. Твоя задача — провести строгую перепроверку и вынести окончательный вердикт. Будь предельно внимателен к контексту.

### КРИТИЧЕСКИ ВАЖНО
ТЫ ДОЛЖЕН ОТКЛОНИТЬ ФУНКЦИЮ, ЕСЛИ ОНА КАСАЕТСЯ:
- Государственных секретов: Любое упоминание гостайн, секретного делопроизводства.
- Физической безопасности: Охрана зданий, пропускной режим, защита объектов.
- Национальной безопасности: Оборона, мобилизация, терроризм, ЧС.

### АНАЛИЗ
Функция для проверки: "[текст функции будет подставлен здесь]"
Вердикт первого AI: "Относится к ИБ"

Проведи собственный анализ. Является ли эта функция действительно функцией по соблюдению ИБ в контексте защиты данных и кибербезопасности?

Ответь ТОЛЬКО в формате JSON: {"verdict": "CONFIRMED"} или {"verdict": "REJECTED"}.
"""


# =============================== Утилиты ===============================
def prepare_api_base_url(url: str) -> str:
    """Форматирует URL-адрес для совместимости с OpenAI API."""
    url = url.strip().rstrip('/')
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    if not url.endswith('/v1'):
        url += "/v1"
    return url

def read_table_auto(fpath: str) -> pd.DataFrame:
    """Автоматически читает Excel или CSV файлы."""
    low = fpath.lower()
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(fpath, dtype=str)
    if low.endswith(".csv"):
        try:
            return pd.read_csv(fpath, sep=';', encoding='utf-8-sig', dtype=str)
        except Exception:
            return pd.read_csv(fpath, sep=',', encoding='utf-8', dtype=str)
    raise ValueError(f"Неподдерживаемый формат файла: {fpath}")

def parse_json_verdict(response_text: str) -> Optional[str]:
    """Извлекает значение ключа 'verdict' из JSON-строки."""
    try:
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return data.get("verdict")
    except (json.JSONDecodeError, AttributeError):
        return None
    return None

# =============================== Основной класс приложения ===============================

class ISAnalyzerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Анализатор функций ИБ v1.5")
        self.root.geometry("1100x850")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)

        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Данные для расчета ETA
        self.eta_data = {}
        self.eta_labels = {}

        self._build_ui()

    def _drain_ui_queue(self):
        """Обрабатывает команды из очереди для обновления UI."""
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
                elif command == 'progress_reset':
                    bar_name, total = value
                    self.progress_bars[bar_name]['maximum'] = max(1, total)
                    self.progress_bars[bar_name]['value'] = 0
                    self.eta_data[bar_name] = {'start_time': time.time(), 'total': total}
                    self.eta_labels[bar_name].config(text="ETA: --:--:--")
                elif command == 'progress_update':
                    bar_name, increment = value
                    self.progress_bars[bar_name]['value'] += increment
                    self._update_eta(bar_name)
                elif command == 'worker_done':
                    self._on_worker_finished(value)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def _update_eta(self, bar_name: str):
        """Рассчитывает и обновляет ETA для указанного прогресс-бара."""
        if bar_name not in self.eta_data:
            return

        data = self.eta_data[bar_name]
        items_done = self.progress_bars[bar_name]['value']

        if items_done > 0:
            elapsed_time = time.time() - data['start_time']
            time_per_item = elapsed_time / items_done
            items_remaining = data['total'] - items_done
            eta_seconds = items_remaining * time_per_item
            eta_formatted = time.strftime('%H:%M:%S', time.gmtime(eta_seconds))
            self.eta_labels[bar_name].config(text=f"ETA: {eta_formatted}")

    def log(self, message: str, to_terminal: bool = False):
        self.ui_queue.put(('log', message))
        if to_terminal:
            print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def set_status(self, text: str):
        self.ui_queue.put(('status', text))

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=450)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- ЛЕВАЯ ПАНЕЛЬ: НАСТРОЙКИ ---
        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Файлы")
        files_frame.pack(fill=tk.X, pady=5)
        self.input_file_var = tk.StringVar()
        self.output_file_var = tk.StringVar()
        self._create_file_row(files_frame, "Исходный файл:", self.input_file_var, self._choose_input_file)
        self._create_file_row(files_frame, "Сохранить отчет в:", self.output_file_var, self._choose_output_file)

        ai_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки AI")
        ai_frame.pack(fill=tk.X, pady=5)
        self.verification_mode_var = tk.StringVar(value="Онлайн")
        mode_frame = ttk.Frame(ai_frame)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(mode_frame, text="Онлайн (OpenAI API)", variable=self.verification_mode_var, value="Онлайн", command=self._on_verification_mode_change).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="Локальная (LM Studio)", variable=self.verification_mode_var, value="Локальная", command=self._on_verification_mode_change).pack(side=tk.LEFT, padx=10)

        # -- Онлайн-настройки --
        self.online_frame = ttk.Frame(ai_frame)
        self.openai_api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.chat_model_var = tk.StringVar(value=DEFAULT_ONLINE_MODEL)
        self.ai_workers_var = tk.IntVar(value=DEFAULT_AI_WORKERS_ONLINE)
        self._create_entry_row(self.online_frame, "Ключ OpenAI API:", self.openai_api_key_var, show="*")
        self._create_entry_row(self.online_frame, "Модель OpenAI:", self.chat_model_var)
        self._create_entry_row(self.online_frame, "Воркеров:", self.ai_workers_var)

        # -- Локальные-настройки --
        self.local_frame = ttk.Frame(ai_frame)
        self.local_model_var = tk.StringVar(value=DEFAULT_LOCAL_MODEL)
        self._create_entry_row(self.local_frame, "Локальная модель:", self.local_model_var)
        ttk.Label(self.local_frame, text=f"Будет ~{WORKERS_PER_LOCAL_SERVER} воркера на сервер.").pack(anchor='w', padx=5)
        ttk.Label(self.local_frame, text="Адреса серверов LM Studio:").pack(anchor='w', pady=(5,0), padx=5)
        self.local_servers_text = tk.Text(self.local_frame, height=3)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=2, padx=5)
        self.local_servers_text.insert(tk.END, DEFAULT_LOCAL_SERVERS)
        self._on_verification_mode_change()

        # -- Общие настройки AI --
        self.temperature_var = tk.DoubleVar(value=DEFAULT_TEMPERATURE)
        common_ai_frame = ttk.Frame(ai_frame)
        common_ai_frame.pack(fill=tk.X, padx=0, pady=0)
        self._create_entry_row(common_ai_frame, "Температура (0.0-2.0):", self.temperature_var)

        # --- ПРАВАЯ ПАНЕЛЬ ---
        controls_frame = ttk.LabelFrame(right_panel, text="Управление и Прогресс")
        controls_frame.pack(fill=tk.X, pady=5)
        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(btn_row, text="Старт анализ", command=self._on_start, style="Accent.TButton")
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(btn_row, text="Стоп", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        self.progress_bars = {}
        self._create_progress_bar(controls_frame, 'ai1', "Этап 1: AI-Классификатор (поиск кандидатов)")
        self._create_progress_bar(controls_frame, 'ai2', "Этап 2: AI-Аудитор (финальная проверка)")

        notebook = ttk.Notebook(right_panel)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self.prompt1_text = self._create_prompt_tab(notebook, "Промпт AI-1 (Классификатор)", DEFAULT_PROMPT_AI1)
        self.prompt2_text = self._create_prompt_tab(notebook, "Промпт AI-2 (Аудитор)", DEFAULT_PROMPT_AI2)
        log_frame = ttk.Frame(notebook, padding=10)
        notebook.add(log_frame, text="Логи выполнения")
        self.log_text = tk.Text(log_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5,0))
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
        ttk.Label(row, text=label, width=15).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    def _create_entry_row(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=15).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

    def _create_progress_bar(self, parent, key, text):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=5, pady=(8,0))
        ttk.Label(row, text=text).pack(side=tk.LEFT)
        self.eta_labels[key] = ttk.Label(row, text="ETA: --:--:--")
        self.eta_labels[key].pack(side=tk.RIGHT)

        self.progress_bars[key] = ttk.Progressbar(parent)
        self.progress_bars[key].pack(fill=tk.X, padx=5, pady=(2,5))

    def _create_prompt_tab(self, notebook, title, content):
        frame = ttk.Frame(notebook, padding=10)
        notebook.add(frame, text=title)
        text_widget = tk.Text(frame, height=8, wrap=tk.WORD)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert(tk.END, content)
        return text_widget

    def _choose_input_file(self):
        f = filedialog.askopenfilename(filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"), ("Все", "*.*")])
        if f: self.input_file_var.set(f)

    def _choose_output_file(self):
        f = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if f: self.output_file_var.set(f)

    def _tick_ui(self):
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(500, self._tick_ui)

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return messagebox.showinfo("Выполняется", "Анализ уже запущен")

        try:
            config = self._validate_config()
        except ValueError as e:
            return messagebox.showerror("Ошибка в настройках", str(e))

        self._stop_event.clear()
        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log("="*50, to_terminal=True)
        self.log("ЗАПУСК АНАЛИЗА ФУНКЦИЙ ИБ", to_terminal=True)

        self._worker_thread = threading.Thread(target=self._run_async_worker, args=(config,), daemon=True)
        self._worker_thread.start()

    def _validate_config(self) -> Dict[str, Any]:
        """Собирает и проверяет конфигурацию из GUI."""
        config = {
            'input_file': self.input_file_var.get().strip(),
            'output_file': self.output_file_var.get().strip(),
            'verification_mode': self.verification_mode_var.get(),
            'prompt1': self.prompt1_text.get("1.0", tk.END).strip(),
            'prompt2': self.prompt2_text.get("1.0", tk.END).strip(),
            'temperature': self.temperature_var.get(),
        }
        if not config['input_file'] or not os.path.isfile(config['input_file']):
            raise ValueError("Укажите корректный исходный файл.")
        if not config['output_file']:
            raise ValueError("Укажите файл для сохранения отчета.")

        if config['verification_mode'] == "Онлайн":
            config['api_key'] = self.openai_api_key_var.get().strip()
            config['model'] = self.chat_model_var.get().strip()
            config['workers'] = self.ai_workers_var.get()
            if not config['api_key']: raise ValueError("В режиме 'Онлайн' нужен ключ OpenAI API.")
            if not config['model']: raise ValueError("В режиме 'Онлайн' нужно имя модели.")
            if config['workers'] <= 0: raise ValueError("Количество воркеров должно быть > 0.")
        else: # Локальная
            local_servers = self.local_servers_text.get("1.0", tk.END).strip().splitlines()
            config['local_servers'] = [s.strip() for s in local_servers if s.strip()]
            config['model'] = self.local_model_var.get().strip()
            if not config['local_servers']: raise ValueError("В режиме 'Локальная' нужен хотя бы один адрес сервера.")
            if not config['model']: raise ValueError("В режиме 'Локальная' нужно имя модели.")
            config['workers'] = len(config['local_servers']) * WORKERS_PER_LOCAL_SERVER

        return config

    def _on_stop(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self.log("Запрошена остановка... Завершаю текущие операции.", to_terminal=True)
            self._stop_event.set()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_worker_finished(self, final_message: str):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._timer_running = False
        self.set_status(final_message)
        # Сброс ETA при завершении
        for label in self.eta_labels.values():
            label.config(text="ETA: --:--:--")
        self.log(final_message.upper(), to_terminal=True)
        self.log("="*50, to_terminal=True)

    def _run_async_worker(self, config: dict):
        """Запускает asyncio event loop в отдельном потоке."""
        try:
            asyncio.run(self._worker_main(config))
        except Exception as e:
            self.log(f"Критическая ошибка: {e}", to_terminal=True)
            self.log(traceback.format_exc(), to_terminal=True)
            self.ui_queue.put(('worker_done', f"Ошибка: {e}"))

    async def _worker_main(self, config: dict):
        """Основная асинхронная логика анализа."""
        clients = []
        try:
            # --- Этап 0: Подготовка ---
            self.set_status("Подготовка: чтение данных и инициализация AI...")
            df = read_table_auto(config['input_file'])
            if COL_TEXT not in df.columns:
                raise ValueError(f"В файле отсутствует обязательная колонка '{COL_TEXT}'.")
            if COL_ID not in df.columns:
                df[COL_ID] = [f"row_{i}" for i in range(len(df))]

            df.dropna(subset=[COL_TEXT], inplace=True)
            df = df[df[COL_TEXT].str.strip() != ''].copy()

            clients, semaphore = self._initialize_clients(config)
            if not clients:
                raise ConnectionError("Не удалось создать ни одного AI-клиента.")

            # --- Этап 1: AI-Классификатор ---
            self.set_status(f"Этап 1: AI-Классификатор ищет кандидатов среди {len(df)} функций...")
            self.ui_queue.put(('progress_reset', ('ai1', len(df))))
            tasks_ai1 = [
                self._run_ai_task(
                    client=clients[i % len(clients)],
                    semaphore=semaphore,
                    prompt_template=config['prompt1'],
                    text_to_check=row[COL_TEXT],
                    expected_verdict="IS_RELATED",
                    config=config,
                    row_data=row
                )
                for i, row in enumerate(df.to_dict('records'))
            ]
            results_ai1 = await asyncio.gather(*tasks_ai1)
            candidates = [res for res in results_ai1 if res is not None]

            if self._stop_event.is_set():
                self.ui_queue.put(('worker_done', "Процесс остановлен пользователем."))
                return

            self.log(f"Этап 1 завершен. Найдено кандидатов: {len(candidates)}", to_terminal=True)

            # --- Этап 2: AI-Аудитор ---
            if not candidates:
                self.log("Релевантных функций не найдено. Анализ завершен.", to_terminal=True)
                pd.DataFrame(columns=df.columns).to_excel(config['output_file'], index=False, engine='openpyxl')
                self.ui_queue.put(('worker_done', "Релевантных функций не найдено."))
                return

            self.set_status(f"Этап 2: AI-Аудитор проверяет {len(candidates)} кандидатов...")
            self.ui_queue.put(('progress_reset', ('ai2', len(candidates))))
            tasks_ai2 = [
                self._run_ai_task(
                    client=clients[i % len(clients)],
                    semaphore=semaphore,
                    prompt_template=config['prompt2'],
                    text_to_check=row[COL_TEXT],
                    expected_verdict="CONFIRMED",
                    config=config,
                    row_data=row
                )
                for i, row in enumerate(candidates)
            ]
            results_ai2 = await asyncio.gather(*tasks_ai2)
            final_functions = [res for res in results_ai2 if res is not None]

            if self._stop_event.is_set():
                self.ui_queue.put(('worker_done', "Процесс остановлен пользователем."))
                return

            # --- Этап 3: Сохранение ---
            self.set_status("Формирование и сохранение отчета...")
            self.log(f"Этап 2 завершен. Подтверждено функций ИБ: {len(final_functions)}", to_terminal=True)
            if final_functions:
                final_df = pd.DataFrame(final_functions)
                if 'temp_id' in final_df.columns:
                    final_df.drop(columns=['temp_id'], inplace=True)
                final_df.to_excel(config['output_file'], index=False, engine='openpyxl')
                self.log(f"Отчет сохранен в: {config['output_file']}", to_terminal=True)
            else:
                pd.DataFrame(columns=df.columns).to_excel(config['output_file'], index=False, engine='openpyxl')
                self.log("Подтвержденных функций не найдено. Сохранен пустой отчет.", to_terminal=True)

            self.ui_queue.put(('worker_done', "Анализ успешно завершен!"))

        except Exception as e:
            self.log(f"Критическая ошибка: {e}", to_terminal=True)
            self.ui_queue.put(('worker_done', f"Ошибка: {e}"))
        finally:
            if clients:
                await asyncio.gather(*(client.close() for client in clients))


    async def _run_ai_task(self, client: AsyncOpenAI, semaphore: asyncio.Semaphore, prompt_template: str, text_to_check: str, expected_verdict: str, config: dict, row_data: Dict) -> Optional[Dict]:
        """Выполняет один запрос к AI и возвращает исходные данные строки в случае успеха."""
        if self._stop_event.is_set():
            return None

        stage = 'ai1' if expected_verdict == "IS_RELATED" else 'ai2'
        row_id = row_data.get(COL_ID, 'N/A')

        # Заменяем плейсхолдер в промпте аудитора
        if stage == 'ai2':
            prompt_template = prompt_template.replace("[текст функции будет подставлен здесь]", text_to_check)
            messages = [{"role": "system", "content": prompt_template}]
        else:
            messages = [
                {"role": "system", "content": prompt_template},
                {"role": "user", "content": text_to_check}
            ]

        response_text = await self._call_api_with_backoff(client, semaphore, messages, config)
        verdict = parse_json_verdict(response_text)

        self.ui_queue.put(('progress_update', (stage, 1)))

        if verdict == expected_verdict:
            self.log(f"[{stage.upper()}] ID: {row_id} -> УСПЕХ (Вердикт: {verdict})")
            return row_data
        else:
            self.log(f"[{stage.upper()}] ID: {row_id} -> ОТКЛОНЕНО (Вердикт: {verdict}, Ответ: {response_text[:100].strip()})")
            return None

    async def _call_api_with_backoff(self, client: AsyncOpenAI, semaphore: asyncio.Semaphore, messages: List[Dict], config: dict) -> str:
        """Выполняет вызов API с обработкой ошибок и семафором."""
        async with semaphore:
            if self._stop_event.is_set():
                return ""
            try:
                resp = await client.chat.completions.create(
                    model=config['model'],
                    messages=messages,
                    temperature=config['temperature']
                )
                return resp.choices[0].message.content or ""
            except APIStatusError as e:
                error_body = "N/A"
                try:
                    error_body = e.response.json()
                except Exception:
                    error_body = e.response.text
                self.log(f"Ошибка API (HTTP {e.status_code}): {error_body}. Повторная попытка...", to_terminal=True)
                await asyncio.sleep(1)
                return f"API_ERROR: {e.status_code}"
            except (APIConnectionError, RateLimitError, httpx.TimeoutException) as e:
                self.log(f"Ошибка сети/API: {type(e).__name__}. Повторная попытка...", to_terminal=True)
                await asyncio.sleep(1)
                return f"API_ERROR: {type(e).__name__}"
            except AuthenticationError as e:
                self.log(f"КРИТИЧЕСКАЯ ОШИБКА АУТЕНТИФИКАЦИИ: {e}. Проверьте API ключ.", to_terminal=True)
                self._stop_event.set()
                return "AUTH_ERROR"
            except Exception as e:
                self.log(f"Неизвестная ошибка при вызове API: {e}", to_terminal=True)
                return f"UNKNOWN_ERROR: {e}"

    def _initialize_clients(self, config: dict) -> Tuple[List[AsyncOpenAI], asyncio.Semaphore]:
        """Инициализирует клиентов и семафор на основе конфигурации."""
        clients = []
        timeout = httpx.Timeout(20.0, connect=10.0)
        if config['verification_mode'] == 'Онлайн':
            client = AsyncOpenAI(api_key=config['api_key'], http_client=httpx.AsyncClient(timeout=timeout))
            clients.append(client)
            semaphore = asyncio.Semaphore(config['workers'])
        else: # Локальная
            for url in config['local_servers']:
                client = AsyncOpenAI(base_url=prepare_api_base_url(url), api_key="not-needed", http_client=httpx.AsyncClient(timeout=timeout))
                clients.append(client)
            semaphore = asyncio.Semaphore(config['workers'])
        return clients, semaphore


def main():
    if AsyncOpenAI is None or openpyxl is None:
        return
    # Для Windows, чтобы избежать проблем с asyncio в потоках
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    root = tk.Tk()
    ISAnalyzerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
