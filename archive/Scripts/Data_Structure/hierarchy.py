# -*- coding: utf-8 -*-
"""
Иерархический анализатор функций государственных органов v1.4 (Турнирный отбор + Фильтр по сферам)
=====================================================================================================
Автор: Gemini (на основе методологии и примеров от пользователя Кастер)
Дата: 03.09.2025

Что нового в v1.4:
- ДОБАВЛЕНО: Внедрена обязательная фильтрация по сфере деятельности. Теперь для анализа
  допускаются только те родительские функции, которые находятся в той же сфере,
  что и дочерняя. Для этого в исходном Excel-файле должна присутствовать колонка "Сфера".

Что нового в v1.3:
- КАРДИНАЛЬНОЕ ИЗМЕНЕНИЕ ЛОГИКИ: Внедрен "турнирный" метод отбора. Вместо
  последовательного поиска скрипт теперь проводит многоэтапный отбор:
  1. Отборочный тур: Все кандидаты (>0.5) делятся на группы по 10,
     и LLM выбирает по одному лучшему из каждой группы.
  2. Промежуточные туры: Победители снова делятся на группы и соревнуются,
     пока не останется 10 или менее финалистов.
  3. Финальный тур: LLM определяет единственного победителя из числа финалистов.
  Этот подход нацелен на более точное и объективное определение лучшей связи.

Архитектура:
- GUI: Построен на tkinter с опциональной темой sv_ttk.
- Многопоточность: Все ресурсоемкие операции вынесены в отдельный рабочий поток.
- Гибридный анализ:
  1. Построение графа организационной иерархии.
  2. Векторизация всех функций для быстрого поиска.
  3. Каскадный поиск кандидатов в рамках одной сферы (сходство >= 0.50).
  4. Многоэтапный "турнирный" отбор лучшей связи с помощью LLM.
- Подключение к AI: Поддерживает три режима: "Онлайн", "Локальная", "АП".
- Отчетность: Генерирует итоговый Excel-файл с двумя листами.
"""

import os
import re
import sys
import time
import json
import queue
import threading
import traceback
from pathlib import Path
from itertools import cycle
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Зависимости ---
import pandas as pd
import numpy as np

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Опциональная тема для GUI
try:
    import sv_ttk
except ImportError:
    sv_ttk = None

# Зависимости для работы с AI
try:
    from openai import OpenAI, APIConnectionError, RateLimitError, AuthenticationError
    import httpx
except ImportError:
    messagebox.showerror("Зависимость не найдена",
                         "Библиотеки 'openai' и 'httpx' не установлены.\n"
                         "Пожалуйста, установите их: pip install openai httpx")
    OpenAI = None


# === Константы и настройки по умолчанию ===

# Названия колонок из методологии
COL_ID = "ID функции"
COL_CHILD_GO = "Подведомственный ГО"
COL_PARENT_GO = "Вышестоящий ГО"
COL_TEXT = "Полный текст функции"
COL_SPHERE = "Сфера" # Новая колонка для фильтрации

# Порог косинусного сходства для отбора кандидатов
SIMILARITY_THRESHOLD = 0.70

# Количество кандидатов в одной группе для "турнирного" отбора
CANDIDATES_PER_GROUP = 10


# Системный промпт для LLM
SYSTEM_PROMPT_HIERARCHY = """
You are an expert analyst specializing in public administration structures.
Your task is to identify the single, most logical hierarchical link between a child function (from a subordinate body) and a list of candidate parent functions (from its superior body).

RULES:
1.  **Analyze the Essence:** Do not just match keywords. Understand the core action, object, and purpose of the child function.
2.  **Find the Best Fit:** Compare the child function's essence to each candidate parent function. The parent function should be a more general, strategic, or coordinating version of the child's action. The child function must be a direct implementation or specification of the parent function.
3.  **Choose ONLY ONE:** You must select only one parent function that is the most direct and logical superior from the provided list.
4.  **No Match Condition:** If NONE of the candidates are a logical parent (e.g., they are about completely different topics or are less specific), you MUST conclude that there is no valid link.
5.  **Provide Justification:** Briefly explain your choice, focusing on the semantic connection (e.g., "The child function 'develops technical standards' which is a direct execution of the parent function 'establishes state policy on standardization'").

Respond ONLY in JSON format. The JSON object must contain two keys: "parent_id" and "justification".

- If a match is found:
  {
    "parent_id": "ID_OF_THE_BEST_PARENT_FUNCTION",
    "justification": "Your concise explanation here."
  }

- If no suitable parent is found:
  {
    "parent_id": "отсутствует",
    "justification": "No candidate function serves as a direct strategic or coordinating superior for the child function."
  }
"""


# === Вспомогательные утилиты ===

def prepare_api_base_url(url: str) -> str:
    """Готовит URL для OpenAI-совместимого API."""
    url = url.strip().rstrip('/')
    if not url: return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    if not url.endswith('/v1'):
        url += "/v1"
    return url

def cosine_similarity(v1, v2):
    """Вычисляет косинусное сходство между двумя векторами."""
    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return dot_product / (norm_v1 * norm_v2)


# === Основной класс приложения ===

class HierarchyAnalyzerApp:
    def __init__(self, root: tk.Tk):
        if not OpenAI:
            root.destroy()
            sys.exit(1)

        self.root = root
        self.root.title("Анализатор иерархии функций v1.4")
        self.root.geometry("1200x950")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)

        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)
        self._worker_thread = None
        self._stop_flag = threading.Event()

        self._build_ui()

    # --- Методы для обновления GUI ---

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
                elif command == 'progress_reset':
                    bar_key, total = value
                    self.progress_bars[bar_key]['maximum'] = max(1, total)
                    self.progress_bars[bar_key]['value'] = 0
                elif command == 'progress_update':
                    bar_key, increment, current_val = value
                    if current_val is not None:
                        self.progress_bars[bar_key]['value'] = current_val
                    else:
                        self.progress_bars[bar_key].step(increment)

                    current = self.progress_bars[bar_key]['value']
                    total = self.progress_bars[bar_key]['maximum']

                    if bar_key == 'main':
                        self.eta_label.config(text=self._calculate_eta(current, total))

                elif command == 'worker_done':
                    self._on_worker_finished(value)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def log(self, message: str):
        self.ui_queue.put(('log', message))

    def set_status(self, text: str):
        self.ui_queue.put(('status', text))

    def reset_progress(self, bar_key: str, total: int):
        self.ui_queue.put(('progress_reset', (bar_key, total)))

    def update_progress(self, bar_key: str, increment: int = 1, current_val=None):
        self.ui_queue.put(('progress_update', (bar_key, increment, current_val)))

    # --- Методы построения GUI ---

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=450)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # -- Левая панель --
        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Данные")
        files_frame.pack(fill=tk.X, pady=5)
        self.input_file_var = tk.StringVar()
        self._create_file_row(files_frame, "Входной Excel:", self.input_file_var, self._choose_input_file)

        embed_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки векторизации")
        embed_frame.pack(fill=tk.X, pady=5)
        self.embed_server_var = tk.StringVar(value="http://192.168.8.200:1234")
        self.embed_model_var = tk.StringVar(value="Qwen/Qwen3-Embedding-8B-GGUF")
        self._create_entry_row(embed_frame, "URL сервера:", self.embed_server_var)
        self._create_entry_row(embed_frame, "Модель:", self.embed_model_var)

        ai_frame = ttk.LabelFrame(left_panel, text="Этап 3: Настройки LLM-валидации")
        ai_frame.pack(fill=tk.X, pady=5)

        self.ai_mode_var = tk.StringVar(value="Локальная")
        mode_frame = ttk.Frame(ai_frame)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(mode_frame, text="Онлайн", variable=self.ai_mode_var, value="Онлайн", command=self._on_ai_mode_change).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="Локальная", variable=self.ai_mode_var, value="Локальная", command=self._on_ai_mode_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(mode_frame, text="АП", variable=self.ai_mode_var, value="АП", command=self._on_ai_mode_change).pack(side=tk.LEFT)

        self.online_frame = ttk.Frame(ai_frame)
        self.api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.online_model_var = tk.StringVar(value="gpt-4o-mini")
        self.online_workers_var = tk.IntVar(value=20)
        self._create_entry_row(self.online_frame, "Ключ API:", self.api_key_var, show="*")
        self._create_entry_row(self.online_frame, "Модель:", self.online_model_var)
        self._create_entry_row(self.online_frame, "Воркеров:", self.online_workers_var)

        self.local_frame = ttk.Frame(ai_frame)
        self.local_servers_text = tk.Text(self.local_frame, height=5, wrap=tk.WORD)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=2, padx=5)
        self.local_servers_text.insert("1.0", "http://192.168.8.200:1234\n")
        self.local_model_var = tk.StringVar(value="google/gemma-3-12b")
        self.local_workers_var = tk.IntVar(value=8)
        self._create_entry_row(self.local_frame, "Модель:", self.local_model_var)
        self._create_entry_row(self.local_frame, "Воркеров:", self.local_workers_var)

        self.ap_frame = ttk.Frame(ai_frame)
        self.ap_url_var = tk.StringVar(value="https://llm.govplan.kz")
        self.ap_key_var = tk.StringVar(value="sk-2XHtdjf7Qu1XNWM0mQ3ozg")
        self.ap_model_var = tk.StringVar(value="openai/gpt-oss-120b")
        self.ap_workers_var = tk.IntVar(value=10)
        self._create_entry_row(self.ap_frame, "URL сервера:", self.ap_url_var)
        self._create_entry_row(self.ap_frame, "Ключ API:", self.ap_key_var, show="*")
        self._create_entry_row(self.ap_frame, "Модель:", self.ap_model_var)
        self._create_entry_row(self.ap_frame, "Воркеров:", self.ap_workers_var)

        self._on_ai_mode_change()

        # -- Правая панель --
        controls_frame = ttk.LabelFrame(right_panel, text="Управление")
        controls_frame.pack(fill=tk.X, pady=5)
        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(btn_row, text="Старт", command=self._on_start, style="Accent.TButton")
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(btn_row, text="Стоп", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        progress_frame = ttk.LabelFrame(right_panel, text="Прогресс выполнения")
        progress_frame.pack(fill=tk.X, pady=5)
        self.progress_bars = {}

        ttk.Label(progress_frame, text="Векторизация функций:").pack(fill=tk.X, padx=5, pady=(5,0))
        self.progress_bars['vectorize'] = ttk.Progressbar(progress_frame)
        self.progress_bars['vectorize'].pack(fill=tk.X, padx=5, pady=(2, 5))

        self.eta_label = ttk.Label(progress_frame, text="Примерное время до завершения: вычисление...")
        self.eta_label.pack(fill=tk.X, padx=5, pady=(5,0))
        ttk.Label(progress_frame, text="Анализ связей:").pack(fill=tk.X, padx=5, pady=(5,0))
        self.progress_bars['main'] = ttk.Progressbar(progress_frame)
        self.progress_bars['main'].pack(fill=tk.X, padx=5, pady=(2, 10))

        log_frame = ttk.LabelFrame(right_panel, text="Логи")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = tk.Text(log_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5,0))
        self.status_label = ttk.Label(status_frame, text="Готово к работе")
        self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)

    def _create_file_row(self, parent, label, var, cmd):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=12).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    def _create_entry_row(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=12).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

    def _choose_input_file(self):
        f = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls"), ("Все", "*.*")])
        if f: self.input_file_var.set(f)

    # --- Методы управления и логики ---

    def _on_ai_mode_change(self):
        mode = self.ai_mode_var.get()
        self.online_frame.pack_forget()
        self.local_frame.pack_forget()
        self.ap_frame.pack_forget()

        if mode == "Онлайн":
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        elif mode == "Локальная":
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)
        elif mode == "АП":
            self.ap_frame.pack(fill=tk.X, padx=5, pady=2)

    def _tick_ui(self):
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(500, self._tick_ui)

    def _calculate_eta(self, current, total):
        # Начинаем расчет после 5 итераций для большей точности
        if self._timer_running and current > 5 and total > 0:
            elapsed = time.time() - self.analysis_start_time
            time_per_item = elapsed / current
            remaining_items = total - current
            eta_seconds = remaining_items * time_per_item
            return f"Примерное время до завершения: {time.strftime('%H:%M:%S', time.gmtime(eta_seconds))}"
        return "Примерное время до завершения: вычисление..."

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return messagebox.showwarning("Выполняется", "Анализ уже запущен.")

        try:
            config = {
                'input_file': self.input_file_var.get().strip(),
                'embed_server': self.embed_server_var.get().strip(),
                'embed_model': self.embed_model_var.get().strip(),
                'ai_mode': self.ai_mode_var.get(),
            }
            if not config['input_file'] or not os.path.isfile(config['input_file']):
                raise ValueError("Укажите корректный входной файл.")
            if not config['embed_server']:
                raise ValueError("Укажите URL сервера для векторизации.")

            mode = config['ai_mode']
            if mode == 'Онлайн':
                config.update({
                    'api_key': self.api_key_var.get().strip(),
                    'model': self.online_model_var.get().strip(),
                    'workers': self.online_workers_var.get(),
                })
                if not config['api_key']: raise ValueError("В режиме 'Онлайн' нужен ключ API.")
            elif mode == 'Локальная':
                servers = self.local_servers_text.get("1.0", tk.END).strip().splitlines()
                config.update({
                    'servers': [s.strip() for s in servers if s.strip()],
                    'model': self.local_model_var.get().strip(),
                    'workers': self.local_workers_var.get(),
                })
                if not config['servers']: raise ValueError("В режиме 'Локальная' нужен хотя бы один URL сервера.")
            elif mode == 'АП':
                config.update({
                    'server_url': self.ap_url_var.get().strip(),
                    'api_key': self.ap_key_var.get().strip(),
                    'model': self.ap_model_var.get().strip(),
                    'workers': self.ap_workers_var.get(),
                })
                if not config['server_url']: raise ValueError("В режиме 'АП' нужен URL сервера.")
                if not config['api_key']: raise ValueError("В режиме 'АП' нужен ключ API.")

            if config.get('workers', 0) <= 0:
                raise ValueError("Количество воркеров должно быть больше нуля.")

        except (ValueError, tk.TclError) as e:
            return messagebox.showerror("Ошибка в настройках", str(e))

        self._stop_flag.clear()
        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log("="*50)
        self.log("ЗАПУСК АНАЛИЗА ИЕРАРХИИ (v1.4 - Турнирный отбор)")

        self._worker_thread = threading.Thread(target=self._worker_main, args=(config,), daemon=True)
        self._worker_thread.start()

    def _on_stop(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self.log("Запрошена остановка... Завершаю текущие операции.")
            self._stop_flag.set()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_worker_finished(self, final_message):
        self._timer_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.set_status(final_message)
        self.log(final_message.upper())

    # --- Основной рабочий поток ---

    def _worker_main(self, config: dict):
        try:
            # --- Этап 1: Загрузка и построение иерархии ---
            self.set_status("Этап 1: Загрузка данных и построение иерархии...")
            self.log("Чтение Excel файла...")
            df = pd.read_excel(config['input_file'])
            required_cols = [COL_ID, COL_CHILD_GO, COL_PARENT_GO, COL_TEXT, COL_SPHERE]
            if not all(col in df.columns for col in required_cols):
                raise ValueError(f"В файле отсутствуют обязательные столбцы: {', '.join(required_cols)}")

            df = df.fillna('')
            self.log(f"Построение организационной иерархии...")
            func_map = {str(row[COL_ID]): row.to_dict() for _, row in df.iterrows()}
            go_to_funcs = defaultdict(list)
            for func_id, func_data in func_map.items():
                go_to_funcs[func_data[COL_CHILD_GO]].append(func_id)

            org_hierarchy = defaultdict(list)
            for child_go, funcs in go_to_funcs.items():
                parent_go = func_map[funcs[0]].get(COL_PARENT_GO, '')
                if parent_go and parent_go != child_go:
                    if child_go not in org_hierarchy[parent_go]:
                        org_hierarchy[parent_go].append(child_go)

            self.log(f"Найдено {len(org_hierarchy)} родительских ГО с уникальными дочерними.")

            # --- Этап 2: Векторизация ---
            self.set_status("Этап 2: Векторизация текстов функций...")
            self.log("Подключение к серверу эмбеддингов...")
            embed_client = OpenAI(base_url=prepare_api_base_url(config['embed_server']), api_key="not-needed")
            self.reset_progress('vectorize', len(func_map))

            texts_to_embed = [func[COL_TEXT] for func in func_map.values()]
            embeddings = []
            batch_size = 64
            for i in range(0, len(texts_to_embed), batch_size):
                if self._stop_flag.is_set(): raise InterruptedError("Процесс остановлен")
                batch = [text if text.strip() else " " for text in texts_to_embed[i:i+batch_size]]
                response = embed_client.embeddings.create(input=batch, model=config['embed_model'])
                embeddings.extend([item.embedding for item in response.data])
                self.update_progress('vectorize', len(batch))

            for i, func_id in enumerate(func_map.keys()):
                func_map[func_id]['embedding'] = np.array(embeddings[i])

            self.log("Векторизация успешно завершена.")

            # --- Этап 3 и 4: Анализ и Валидация ---
            self.set_status("Этап 3/4: Поиск кандидатов и турнирный отбор...")
            tasks = []
            for parent_go, child_gos in org_hierarchy.items():
                parent_funcs_ids = go_to_funcs.get(parent_go, [])
                for child_go in child_gos:
                    child_funcs_ids = go_to_funcs.get(child_go, [])
                    for child_func_id in child_funcs_ids:
                        tasks.append({'child_id': child_func_id, 'parent_ids': parent_funcs_ids})

            self.reset_progress('main', len(tasks))
            self.log(f"Начинается анализ {len(tasks)} дочерних функций...")
            self.analysis_start_time = time.time() # Для расчета ETA

            llm_clients = self._initialize_llm_clients(config)
            if not llm_clients:
                raise ValueError("Не удалось инициализировать LLM-клиенты. Проверьте настройки.")
            client_iterator = cycle(llm_clients)

            results = []
            with ThreadPoolExecutor(max_workers=config['workers']) as executor:
                futures = {executor.submit(self._process_task, task, func_map, next(client_iterator), config): task for task in tasks}
                for future in as_completed(futures):
                    if self._stop_flag.is_set():
                        for f in futures: f.cancel()
                        raise InterruptedError("Процесс остановлен")
                    try:
                        result = future.result()
                        if result: results.append(result)
                    except Exception as exc:
                        self.log(f"Ошибка при обработке задачи: {exc}")
                    self.update_progress('main')

            self.log("Анализ связей завершен.")

            # --- Этап 5: Формирование отчета ---
            self.set_status("Этап 5: Формирование итогового отчета...")
            self._create_and_save_report(df, results)

            self.ui_queue.put(('worker_done', "Анализ успешно завершен!"))

        except InterruptedError:
            self.ui_queue.put(('worker_done', "Процесс остановлен пользователем."))
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("Критическая ошибка", f"Произошла ошибка:\n{e}")
            self.ui_queue.put(('worker_done', f"Ошибка: {e}"))

    def _initialize_llm_clients(self, config):
        mode = config['ai_mode']
        timeout = httpx.Timeout(60.0, connect=10.0)
        clients = []

        if mode == 'Онлайн':
            clients.append(OpenAI(api_key=config['api_key'], http_client=httpx.Client(timeout=timeout)))
        elif mode == 'Локальная':
            for server_url in config['servers']:
                clients.append(OpenAI(base_url=prepare_api_base_url(server_url), api_key="not-needed", http_client=httpx.Client(timeout=timeout)))
        elif mode == 'АП':
            clients.append(OpenAI(base_url=prepare_api_base_url(config['server_url']), api_key=config['api_key'], http_client=httpx.Client(timeout=timeout)))

        if not clients:
            raise ValueError("Не удалось создать ни одного AI клиента.")
        return clients

    def _get_llm_decision_for_batch(self, child_func, batch, llm_client, config):
        """Отправляет одну группу кандидатов в LLM и возвращает решение."""
        child_id = child_func[COL_ID]
        prompt_user = f"Child Function (ID: {child_id}):\n{child_func[COL_TEXT]}\n\n"
        prompt_user += "Candidate Parent Functions:\n"
        for j, cand in enumerate(batch):
            prompt_user += f"{j+1}. (ID: {cand['id']}): {cand['text']}\n"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_HIERARCHY},
            {"role": "user", "content": prompt_user}
        ]

        request_params = {"model": config['model'], "messages": messages}
        if config['ai_mode'] in ['Онлайн', 'АП']:
            request_params["response_format"] = {"type": "json_object"}

        try:
            response = llm_client.chat.completions.create(**request_params)
            content = response.choices[0].message.content
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if not match:
                raise json.JSONDecodeError("No JSON object found", content, 0)
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            self.log(f"Ошибка парсинга JSON для ID {child_id}. Ответ: {content}")
            return {'parent_id': 'ошибка_формата', 'justification': 'Неверный формат ответа от LLM.'}
        except (APIConnectionError, RateLimitError, httpx.ReadTimeout) as e:
            self.log(f"Сетевая ошибка для ID {child_id}: {e}. Группа пропущена.")
            return {'parent_id': 'отсутствует', 'justification': f'Сетевая ошибка при проверке группы: {e}'}

    def _process_task(self, task, func_map, llm_client, config):
        child_id = task['child_id']
        parent_ids = task['parent_ids']
        child_func = func_map[child_id]

        # 1. Найти всех возможных кандидатов с учетом сферы
        child_sphere = child_func.get(COL_SPHERE)
        if not child_sphere or pd.isna(child_sphere):
            return {'child_id': child_id, 'parent_id': 'отсутствует', 'justification': 'У дочерней функции не указана сфера деятельности.'}

        candidates = []
        for parent_id in parent_ids:
            parent_func = func_map.get(parent_id)
            if not parent_func: continue

            # Фильтр по сфере
            if parent_func.get(COL_SPHERE) != child_sphere:
                continue

            # Если сфера совпадает, считаем сходство
            sim = cosine_similarity(child_func['embedding'], parent_func['embedding'])
            if sim >= SIMILARITY_THRESHOLD:
                candidates.append({'id': parent_id, 'text': parent_func[COL_TEXT], 'sim': sim})

        if not candidates:
            return {'child_id': child_id, 'parent_id': 'отсутствует', 'justification': 'Не найдено семантически близких кандидатов в той же сфере.'}

        candidates.sort(key=lambda x: x['sim'], reverse=True)

        # 2. "Турнирный" отбор
        round_num = 1
        current_candidates = candidates

        while len(current_candidates) > 1:
            if len(current_candidates) <= CANDIDATES_PER_GROUP:
                self.log(f"ID {child_id}: Переход в финальный раунд с {len(current_candidates)} кандидатами.")
                break # Переходим к финальному раунду

            self.log(f"ID {child_id}: Раунд {round_num}. Кандидатов: {len(current_candidates)}")

            next_round_winners = []
            num_batches = (len(current_candidates) + CANDIDATES_PER_GROUP - 1) // CANDIDATES_PER_GROUP

            for i in range(num_batches):
                start = i * CANDIDATES_PER_GROUP
                end = start + CANDIDATES_PER_GROUP
                batch = current_candidates[start:end]

                winner_decision = self._get_llm_decision_for_batch(child_func, batch, llm_client, config)

                if winner_decision and winner_decision.get('parent_id') not in ['отсутствует', 'ошибка_формата']:
                    winner_id = winner_decision.get('parent_id')
                    winner_obj = next((c for c in batch if c['id'] == winner_id), None)
                    if winner_obj:
                        next_round_winners.append(winner_obj)

            if not next_round_winners:
                self.log(f"ID {child_id}: Связь не найдена, ни одного победителя в раунде {round_num}.")
                return {'child_id': child_id, 'parent_id': 'отсутствует', 'justification': f'Ни один кандидат не прошел раунд {round_num}.'}

            current_candidates = next_round_winners
            round_num += 1

        # 3. Финальный раунд
        self.log(f"ID {child_id}: Финальный раунд. Кандидатов: {len(current_candidates)}")
        final_decision = self._get_llm_decision_for_batch(child_func, current_candidates, llm_client, config)

        if final_decision and final_decision.get('parent_id') not in ['отсутствует', 'ошибка_формата']:
            self.log(f"ID {child_id}: Найдена финальная связь -> {final_decision.get('parent_id')}")
            return {'child_id': child_id, **final_decision}
        else:
            self.log(f"ID {child_id}: Связь не найдена в финальном раунде.")
            justification = final_decision.get('justification', f'Не найдено подходящей функции после всех раундов отбора.')
            return {'child_id': child_id, 'parent_id': 'отсутствует', 'justification': justification}


    def _create_and_save_report(self, df, results):
        df_res = df.copy()
        links_map = {res['child_id']: res for res in results}

        df_res['Ссылается на'] = df_res[COL_ID].astype(str).map(lambda x: links_map.get(x, {}).get('parent_id', ''))
        df_res['Почему она ссылается на'] = df_res[COL_ID].astype(str).map(lambda x: links_map.get(x, {}).get('justification', ''))

        reverse_links = defaultdict(list)
        for child_id, data in links_map.items():
            parent_id = data.get('parent_id')
            if parent_id and parent_id not in ['отсутствует', 'ошибка_формата']:
                reverse_links[parent_id].append(child_id)

        df_res['Реализуется'] = df_res[COL_ID].astype(str).map(lambda x: "; ".join(sorted(reverse_links.get(x, []))))
        df_res['Почему она реализуется'] = df_res['Реализуется'].apply(
            lambda x: f"Данная функция детализируется в {len(x.split('; '))} нижестоящих функциях." if x else ""
        )

        osnovnoy_cols = list(df.columns) + ['Ссылается на', 'Реализуется']
        analit_cols = list(df.columns) + ['Ссылается на', 'Почему она ссылается на', 'Реализуется', 'Почему она реализуется']

        df_osnovnoy = df_res[osnovnoy_cols]
        df_analit = df_res[analit_cols]

        save_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            title="Сохранить итоговый отчет",
            initialfile=f"Иерархия_отчет_{time.strftime('%Y%m%d_%H%M')}.xlsx"
        )
        if not save_path:
            self.log("Сохранение отменено.")
            return

        with pd.ExcelWriter(save_path, engine='openpyxl') as writer:
            df_osnovnoy.to_excel(writer, sheet_name='Основной отчет', index=False)
            df_analit.to_excel(writer, sheet_name='Аналитический отчет', index=False)

        self.log(f"Отчет успешно сохранен в: {save_path}")
        messagebox.showinfo("Готово", f"Отчет сохранен:\n{save_path}")


if __name__ == "__main__":
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    root = tk.Tk()
    app = HierarchyAnalyzerApp(root)
    root.mainloop()

