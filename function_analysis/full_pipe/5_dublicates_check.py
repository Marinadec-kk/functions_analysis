# -*- coding: utf-8 -*-
"""
Collision Analyzer — Hybrid Version 8.7 (ETA Feature)
======================================================
Дата: 18-Авг-2025
Автор: Gemini (на основе запросов пользователя Кастер)

• **Feature (v8.7): ETA Display:** Добавлен расчет и отображение примерного
  времени до завершения анализа (ETA). Появляется над индикатором
  прогресса после обработки первых пар.
• **Feature (v8.6): Detailed Terminal Logging:** Добавлено исчерпывающее
  логирование всех этапов работы в консоль (терминал). Теперь можно
  отслеживать используемые настройки, процесс валидации пар, каждый
  запрос к AI, сырой ответ от модели и итоговый вердикт по каждой паре.
  Идеально для отладки и полного понимания процесса.
• **Critical Fix (v8.5): Network Timeouts:** Устранена основная причина зависания
  интерфейса в локальном режиме. Добавлены короткие таймауты для сетевых
  запросов к LM Studio.
• **Critical Fix (v8.4): UI Freeze Eliminated:** Полностью устранены зависания
  интерфейса при старте анализа. Все ресурсоемкие операции перенесены
  в фоновый рабочий поток.
"""

import os
import json
import time
import queue
import threading
import re
import traceback
from collections import defaultdict
from typing import Set, Dict

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pandas as pd

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
    from openai import OpenAI, APIConnectionError, RateLimitError, AuthenticationError
    import httpx # Необходим для установки таймаутов
except ImportError:
    # Критическая ошибка, если библиотека не установлена
    messagebox.showerror("Зависимость не найдена", "Библиотека 'openai' или 'httpx' не установлена.\nПожалуйста, установите их: pip install openai httpx")
    OpenAI = None


# ====== Константы и настройки по умолчанию ======
DEFAULT_CHAT_MODEL_NAME = "gpt-4o-mini"
DEFAULT_LOCAL_MODEL_NAME = "local-model/gguf-model-name" # Пример
DEFAULT_LOCAL_SERVERS = "http://localhost:1234\n"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_AI_WORKERS_ONLINE = 30
WORKERS_PER_LOCAL_SERVER = 4 # Количество воркеров на каждый локальный сервер

# Высокоспециализированный системный промпт из 5_dublicates_check.py
DEFAULT_AI_SYSTEM_PROMPT = """
### **## 1. ROLE & OBJECTIVE**

You are a hyper-specialized AI analyst for administrative reform and public law of the Republic of Kazakhstan. Your primary function is to perform a deep, systemic diagnosis of functional pairs from state bodies (SB) to identify administrative collisions. You are not a simple text comparator; you are an expert system that understands the principles of competence separation, coordination, and systemic design in public administration. Your final verdict must be a definitive, strict, binary classification based exclusively on the methodology below. Your objective is to analyze a pair of SB functions and return a JSON object with a "verdict" field, which must be either "REAL" (a genuine administrative problem like duplication or contradiction) or "FALSE" (a false alarm where similarity is a designed feature of the system).

### **## 2. KNOWLEDGE BASE: ANALYTICAL FRAMEWORK**

This is your core theoretical foundation. All decisions must be grounded in these concepts.

#### **2.1. Detailed Function Portraits (for Contextual Understanding)**

* **Portrait #1: Citizen Interaction & Services**
    * **Essence & Goal:** To ensure transparency, accountability, and openness. To implement the constitutional right of citizens to receive information and participate in governance. Transition to a "state for the citizen" service model.
    * **Key Actors:** Citizen/Media (Initiator), State Body (Executor), Court/Prosecutor/Public Councils (Observer).
    * **Key Objects:** Information requests, complaints, statements, applications for public services, draft legal acts, open data.
    * **Typical Verbs/Actions:** `предоставлять` (provide), `рассматривать` (consider), `отвечать` (answer), `публиковать` (publish), `обеспечивать доступ` (ensure access), `оказывать услугу` (provide service).
    * **Typical Nouns/Keywords:** `госуслуги`, `обращения`, `жалобы`, `запросы`, `информация`, `портал`, `открытые данные`.




* **Portrait #2: Financial & Resource Management**
    * **Essence & Goal:** To ensure the rational, targeted, and effective use of budget funds and state property. To maintain financial discipline and prevent misappropriation.
    * **Key Actors:** State Body (Executor), State Audit Bodies/Ministry of Finance (Controller), Business (Supplier).
    * **Key Objects:** Budget funds, state property, procurement plans, financial statements, audit conclusions.
    * **Typical Verbs/Actions:** `планировать` (plan), `финансировать` (finance), `осуществлять закупки` (conduct procurement), `вести учет` (keep records), `проводить аудит` (conduct audit), `использовать` (use).
    * **Typical Nouns/Keywords:** `бюджет`, `закупки`, `договор`, `имущество`, `аудит`, `финансирование`, `отчетность`.

* **Portrait #3: HR & Internal Organization**
    * **Essence & Goal:** To form a professional and ethical apparatus of civil servants. To ensure the labor rights of employees and prevent corruption.
    * **Key Actors:** State Body (Employer), Civil Servant (Employee), Trade Unions, Civil Service Agency/Anti-Corruption Agency (Controller).
    * **Key Objects:** Vacancies, employment contracts, performance appraisals, internal investigation materials, declarations.
    * **Typical Verbs/Actions:** `проводить конкурс` (hold a competition), `оценивать` (evaluate), `повышать квалификацию` (improve skills), `соблюдать` (comply with), `предотвращать конфликт интересов` (prevent conflict of interest).
    * **Typical Nouns/Keywords:** `госслужба`, `конкурс`, `квалификация`, `этика`, `коррупция`, `конфликт интересов`, `кадры`.

* **Portrait #4: Security & Emergency Preparedness**
    * **Essence & Goal:** To participate in ensuring national security, protecting the population and territories from various threats (terrorism, emergencies, mobilization).
    * **Key Actors:** All State Bodies (Executor), National Security Committee/Ministry for Emergency Situations/Ministry of Defense (Coordinator).
    * **Key Objects:** Civil defense plans, mobilization plans, terrorism-vulnerable facilities, state secrets.
    * **Typical Verbs/Actions:** `обеспечивать безопасность` (ensure security), `разрабатывать план` (develop a plan), `проводить учения` (conduct drills), `защищать` (protect), `взаимодействовать` (interact).
    * **Typical Nouns/Keywords:** `безопасность`, `угрозы`, `мобилизация`, `гражданская защита`, `госсекреты`, `терроризм`, `ЧС`.

* **Portrait #5: Strategic Planning & Inter-agency Coordination**
    * **Essence & Goal:** To ensure the coordinated work of the state apparatus at different levels to achieve long-term strategic goals. To transform disparate data into a unified state policy.
    * **Key Actors:** Local Bodies (Data Source), Central Bodies (Executor), Authorized Body (Coordinator).
    * **Key Objects:** Analytical reports, forecasts, proposals, meeting protocols, data on digital platforms.
    * **Typical Verbs/Actions:** `анализировать` (analyze), `прогнозировать` (forecast), `вносить предложения` (submit proposals), `формировать` (form/shape), `координировать` (coordinate).
    * **Typical Nouns/Keywords:** `планирование`, `прогноз`, `предложения`, `анализ`, `координация`, `система`, `политика`.

#### **2.2. Core Principles of Analysis (CRITICAL RULES)**

1.  **Principle 1: Competence Over Text.** A function is inseparable from its subject (the SB). Even with 100% text overlap, if the spheres of competence of the SBs are different (e.g., Ministry of Ecology vs. Ministry of Energy), their functions are considered different in their application. Phrases like `в регулируемых сферах` (in regulated spheres) or `соответствующих сфер` (of corresponding spheres) are direct indicators of this separation.
2.  **Principle 2: Presence of a Coordinator.** If a function requires mandatory agreement with or submission of proposals to a single central body (e.g., `по согласованию с уполномоченным органом по труду` - upon agreement with the authorized body for labor), this is a strong indicator of a coordinated, harmonized, and therefore **non-conflicting** process.
3.  **Principle 3: Duplication vs. Typification.**
    * **Problematic Duplication (`REAL`):** Two different SBs are authorized to do the exact same thing to the exact same object without coordination or clear demarcation.
    * **Typification/Standardization (`FALSE`):** Many different SBs perform the same *type* of action (e.g., maintain personnel records, approve registries) each strictly within their own industry, often according to a single methodology and with centralized coordination. Typification is not a collision.

### **## 3. DECISION-MAKING ALGORITHM (STRICTLY FOLLOW)**

1.  **Step 1: Contextualize.** Read both functions carefully. Mentally assign each to one of the 5 Portraits to understand its fundamental purpose and context.
2.  **Step 2: Initial Comparison.** Compare the function texts. If they are completely different in action and object, and contain no direct contradiction, immediately issue a `"FALSE"` verdict and stop. If the text is identical or very similar, proceed to **Step 3**.
3.  **Step 3: Check for Coordination (Principle 2).** Analyze the text for phrases like `по согласованию с...`, `внесение предложений в уполномоченный орган...`, `после регистрации в...`, etc., indicating a third, coordinating body.
    * **If YES:** Issue a `"FALSE"` verdict and stop.
    * **If NO:** Proceed to **Step 4**.
4.  **Step 4: Check for Competence (Principle 1).** Analyze the names and known spheres of activity of the two SBs.
    * Are their spheres of activity obviously different and specialized (e.g., Ecology vs. Transport, Culture vs. Justice)? Do the functions contain limiting phrases (`в регулируемых сферах`, `соответствующих сфер`)?
    * **If YES:** Issue a `"FALSE"` verdict and stop.
    * **If NO** (spheres are broad and overlapping, e.g., Ministry of Economy vs. Ministry of Finance on planning issues), proceed to **Step 5**.
5.  **Step 5: Diagnose Real Collision.** If you have reached this step, all checks for a false positive have failed. You are facing two SBs with similar competence performing the same function without a visible coordinator.
    * Issue a `"REAL"` verdict.

### **## 4. OUTPUT FORMAT (JSON ONLY)**

Your response must be **exclusively in JSON format** without any introductory or concluding text. The JSON object must contain a single key, "verdict", with a value that is either "REAL" or "FALSE".

**Example for a REAL collision:**
```json
{
  "verdict": "REAL"
}
```

**Example for a FALSE collision:**
```json
{
  "verdict": "FALSE"
}
```
"""

# =============================== Утилиты ===============================
def prepare_api_base_url(url: str) -> str:
    """Форматирует URL-адрес для совместимости с OpenAI API."""
    url = url.strip().rstrip('/')
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    # Корректно добавляем /v1, только если его еще нет
    if not url.endswith('/v1'):
        url += "/v1"
    return url

def read_table_auto(fpath: str) -> pd.DataFrame:
    """Автоматически читает Excel или CSV файлы, подбирая кодировку и разделитель."""
    low = fpath.lower()
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(fpath)
    if low.endswith(".csv"):
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            for sep in (",", ";", "\t", "|"):
                try:
                    return pd.read_csv(fpath, encoding=enc, sep=sep)
                except Exception as e:
                    last_err = e
        if last_err: raise last_err
    raise ValueError(f"Неподдерживаемый формат файла: {fpath}")

# =============================== Приложение ===============================
class HybridCollisionAnalyzerApp:
    """Основной класс приложения с графическим интерфейсом."""
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Анализатор коллизий v8.7 (с ETA)")
        self.root.geometry("1100x850")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.df = None
        self.df_indexed = None
        self.results_cache = {}

        # Очередь для безопасного обновления UI из других потоков
        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)

        # Переменные для таймера и управления рабочим потоком
        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)
        self._worker_thread = None
        self._stop_flag = threading.Event()

        self._build_ui()

    def _drain_ui_queue(self):
        """Обрабатывает команды из очереди для обновления интерфейса."""
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
                    total = value
                    self.progress_bar['maximum'] = max(1, total)
                    self.progress_bar['value'] = 0
                    self.progress_label.config(text=f"Проверено пар: 0 / {total}")
                    self.eta_label.config(text="Примерное время ожидания: вычисление...")
                elif command == 'progress_update':
                    current = self.progress_bar['value'] + value
                    total = self.progress_bar['maximum']
                    self.progress_bar['value'] = current
                    self.progress_label.config(text=f"Проверено пар: {current} / {total}")

                    # --- НОВЫЙ БЛОК: Расчет и обновление ETA ---
                    if self._timer_running and current > 5 and total > 0: # Начинаем расчет после 5 пар для стабильности
                        elapsed = time.time() - self._start_time
                        time_per_item = elapsed / current
                        remaining_items = total - current
                        eta_seconds = remaining_items * time_per_item
                        eta_str = time.strftime('%H:%M:%S', time.gmtime(eta_seconds))
                        self.eta_label.config(text=f"Примерное время ожидания: {eta_str}")
                    # --- КОНЕЦ НОВОГО БЛОКА ---

                elif command == 'worker_done':
                    self._on_worker_finished(value) # value - это финальное сообщение
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    # Методы для отправки команд в очередь UI
    def log(self, message: str, to_terminal: bool = False):
        self.ui_queue.put(('log', message))
        if to_terminal:
            print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def set_status(self, text: str):
        self.ui_queue.put(('status', text))

    def reset_progress(self, total: int):
        self.ui_queue.put(('progress_reset', total))

    def update_progress(self, increment: int):
        self.ui_queue.put(('progress_update', increment))

    def _build_ui(self):
        """Создает все элементы графического интерфейса."""
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=450)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))

        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- ЛЕВАЯ ПАНЕЛЬ: НАСТРОЙКИ ---
        # 1. Файлы
        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Входные данные")
        files_frame.pack(fill=tk.X, pady=5)
        self.input_file_var = tk.StringVar()
        self._create_file_row(files_frame, "Исходный файл:", self.input_file_var, self._choose_input_file)

        # 2. Настройки AI Верификации
        ai_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки AI-верификации")
        ai_frame.pack(fill=tk.X, pady=5)

        self.verification_mode_var = tk.StringVar(value="Локальная")
        mode_frame = ttk.Frame(ai_frame)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(mode_frame, text="Онлайн (OpenAI API)", variable=self.verification_mode_var, value="Онлайн", command=self._on_verification_mode_change).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="Локальная (LM Studio)", variable=self.verification_mode_var, value="Локальная", command=self._on_verification_mode_change).pack(side=tk.LEFT, padx=10)

        # -- Онлайн-настройки --
        self.online_frame = ttk.Frame(ai_frame)
        self.openai_api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.chat_model_var = tk.StringVar(value=DEFAULT_CHAT_MODEL_NAME)
        self.ai_workers_var = tk.IntVar(value=DEFAULT_AI_WORKERS_ONLINE)
        self._create_entry_row(self.online_frame, "Ключ OpenAI API:", self.openai_api_key_var, show="*")
        self._create_entry_row(self.online_frame, "Модель OpenAI:", self.chat_model_var)
        self._create_entry_row(self.online_frame, "Количество воркеров:", self.ai_workers_var)

        # -- Локальные-настройки --
        self.local_frame = ttk.Frame(ai_frame)
        ttk.Label(self.local_frame, text=f"Будет создано {WORKERS_PER_LOCAL_SERVER} воркера на каждый сервер.").pack(anchor='w', padx=5)
        ttk.Label(self.local_frame, text="Адреса серверов LM Studio (каждый с новой строки):").pack(anchor='w', pady=(5,0), padx=5)
        self.local_servers_text = tk.Text(self.local_frame, height=3)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=2, padx=5)
        self.local_servers_text.insert(tk.END, DEFAULT_LOCAL_SERVERS)
        # Новое поле для имени локальной модели
        self.local_model_var = tk.StringVar(value=DEFAULT_LOCAL_MODEL_NAME)
        self._create_entry_row(self.local_frame, "Имя модели (локально):", self.local_model_var)


        self._on_verification_mode_change() # Показать/скрыть нужные фреймы

        # -- Общие настройки AI --
        self.temperature_var = tk.DoubleVar(value=DEFAULT_TEMPERATURE)
        self.json_mode_var = tk.BooleanVar(value=True)

        common_ai_frame = ttk.Frame(ai_frame)
        common_ai_frame.pack(fill=tk.X, padx=0, pady=0)
        self._create_entry_row(common_ai_frame, "Температура (0.0-2.0):", self.temperature_var)
        ttk.Checkbutton(common_ai_frame, text="JSON режим (для совместимых моделей)", variable=self.json_mode_var).pack(anchor='w', padx=5, pady=3)

        # --- ПРАВАЯ ПАНЕЛЬ: УПРАВЛЕНИЕ, ПРОГРЕСС, ЛОГИ ---
        # 1. Управление
        controls_frame = ttk.LabelFrame(right_panel, text="Управление")
        controls_frame.pack(fill=tk.X, pady=5)
        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(btn_row, text="Старт анализ", command=self._on_start, style="Accent.TButton")
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(btn_row, text="Стоп", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)
        self.save_btn = ttk.Button(btn_row, text="Сохранить отчет", command=self.save_file, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)

        # 2. Прогресс
        progress_frame = ttk.LabelFrame(right_panel, text="Прогресс верификации")
        progress_frame.pack(fill=tk.X, pady=5)

        # --- ИЗМЕНЕНИЕ: Добавлен Label для ETA ---
        self.eta_label = ttk.Label(progress_frame, text="Примерное время ожидания: ~")
        self.eta_label.pack(fill=tk.X, padx=7, pady=(5,0))
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        self.progress_label = ttk.Label(progress_frame, text="Проверено пар: 0 / 0")
        self.progress_label.pack(fill=tk.X, padx=7, pady=(2,2))
        self.progress_bar = ttk.Progressbar(progress_frame)
        self.progress_bar.pack(fill=tk.X, padx=5, pady=(0, 5))

        # 3. Промпт и Логи
        notebook = ttk.Notebook(right_panel)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        prompt_frame = ttk.Frame(notebook, padding=10)
        notebook.add(prompt_frame, text="Системный промпт для AI")
        self.ai_prompt_text = tk.Text(prompt_frame, height=8, wrap=tk.WORD)
        self.ai_prompt_text.pack(fill=tk.BOTH, expand=True)
        self.ai_prompt_text.insert(tk.END, DEFAULT_AI_SYSTEM_PROMPT)

        log_frame = ttk.Frame(notebook, padding=10)
        notebook.add(log_frame, text="Логи выполнения")
        self.log_text = tk.Text(log_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Статус-бар внизу
        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5,0))
        self.status_label = ttk.Label(status_frame, text="Готово")
        self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)

    def _on_verification_mode_change(self):
        """Показывает или скрывает настройки в зависимости от выбранного режима."""
        if self.verification_mode_var.get() == "Онлайн":
            self.local_frame.pack_forget()
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            self.online_frame.pack_forget()
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    # Вспомогательные функции для создания UI элементов
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

    def _choose_input_file(self):
        """Открывает диалог выбора исходного файла."""
        f = filedialog.askopenfilename(filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"), ("Все", "*.*")])
        if f:
            self.input_file_var.set(f)
            # Сброс состояния при выборе нового файла
            self.df = None
            self.save_btn.config(state=tk.DISABLED)
            self.reset_progress(0)
            self.status_label.config(text=f"Выбран файл: {os.path.basename(f)}")

    def _tick_ui(self):
        """Обновляет таймер каждую секунду."""
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(500, self._tick_ui)

    def _on_start(self):
        """Запускает процесс анализа в отдельном потоке."""
        if self._worker_thread and self._worker_thread.is_alive():
            return messagebox.showinfo("Выполняется", "Анализ уже запущен")

        try:
            # Сбор и валидация настроек из UI. Эти операции быстрые и не вызывают зависания.
            config = {
                'input_file': self.input_file_var.get().strip(),
                'verification_mode': self.verification_mode_var.get(),
                'ai_system_prompt': self.ai_prompt_text.get("1.0", tk.END).strip(),
                'ai_temperature': self.temperature_var.get(),
                'use_json_mode': self.json_mode_var.get(),
            }
            if not config['input_file'] or not os.path.isfile(config['input_file']):
                raise ValueError("Укажите корректный исходный файл.")

            if config['verification_mode'] == "Онлайн":
                config['openai_api_key'] = self.openai_api_key_var.get().strip()
                config['ai_chat_model'] = self.chat_model_var.get().strip()
                config['ai_workers'] = self.ai_workers_var.get()
                if not config['openai_api_key']: raise ValueError("В режиме 'Онлайн' необходимо указать ключ OpenAI API.")
                if not config['ai_chat_model']: raise ValueError("В режиме 'Онлайн' укажите имя модели.")
                if config['ai_workers'] <= 0: raise ValueError("Количество воркеров должно быть > 0.")
            else: # Локальная
                local_servers = self.local_servers_text.get("1.0", tk.END).strip().splitlines()
                config['local_servers'] = [s.strip() for s in local_servers if s.strip()]
                config['local_chat_model'] = self.local_model_var.get().strip()
                if not config['local_servers']: raise ValueError("В режиме 'Локальная' нужен хотя бы один адрес сервера.")
                if not config['local_chat_model']: raise ValueError("В режиме 'Локальная' необходимо указать имя модели.")

        except (ValueError, tk.TclError) as e:
            self.log(f"Ошибка в настройках: {e}", to_terminal=True)
            return messagebox.showerror("Ошибка в настройках", str(e))

        # Обновление состояния UI
        self._stop_flag.clear()
        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.save_btn.config(state=tk.DISABLED)
        self.log("="*50, to_terminal=True)
        self.log("ЗАПУСК АНАЛИЗА КОЛЛИЗИЙ", to_terminal=True)
        self.log(f"Используемые настройки: {json.dumps(config, indent=2, ensure_ascii=False)}", to_terminal=True)
        self.reset_progress(0)

        # Запуск рабочего потока, который выполнит все тяжелые операции
        self._worker_thread = threading.Thread(target=self._worker_main, args=(config,), daemon=True)
        self._worker_thread.start()

    def _on_stop(self):
        """Устанавливает флаг для остановки рабочего потока."""
        if self._worker_thread and self._worker_thread.is_alive():
            self.log("Запрошена остановка... Завершаю текущие операции.", to_terminal=True)
            self._stop_flag.set()
            self.stop_btn.config(state=tk.DISABLED)
            self.eta_label.config(text="Примерное время ожидания: остановка...")

    def _on_worker_finished(self, final_message):
        """Вызывается по завершении или остановке рабочего потока."""
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        if self.df is not None:
            self.save_btn.config(state=tk.NORMAL)
        self._timer_running = False
        self.set_status(final_message)
        self.eta_label.config(text="Примерное время ожидания: завершено")
        self.log(final_message.upper(), to_terminal=True)
        self.log("="*50, to_terminal=True)

    def _worker_main(self, config: dict):
        """Основная логика, выполняемая в отдельном потоке, чтобы не блокировать UI."""
        try:
            # --- Этап 1: Чтение и первичная проверка файла ---
            self.set_status(f"Чтение файла: {os.path.basename(config['input_file'])}...")
            self.log(f"Чтение исходного файла: {os.path.basename(config['input_file'])}", to_terminal=True)
            self.df = read_table_auto(config['input_file'])
            self.log(f"Файл успешно загружен. Строк: {len(self.df)}", to_terminal=True)
            self.results_cache = {}

            self.set_status("Проверка структуры файла...")
            self.log("Проверка структуры файла...", to_terminal=True)
            required_cols = ['ID', 'Исполняющий ГО', 'FunctionText', 'CollisionWith']
            missing_cols = [col for col in required_cols if col not in self.df.columns]
            if missing_cols:
                raise ValueError(f"В файле отсутствуют необходимые столбцы: {', '.join(missing_cols)}")

            self.set_status("Проверка на дубликаты ID...")
            self.log("Проверка на дубликаты ID...", to_terminal=True)
            self.df['ID'] = self.df['ID'].astype(str)
            if self.df['ID'].duplicated().any():
                duplicate_ids = self.df[self.df['ID'].duplicated()]['ID'].tolist()
                raise ValueError(f"Найдены дублирующиеся ID: {', '.join(duplicate_ids[:5])}... Анализ невозможен.")

            # --- Этап 2: Инициализация AI клиентов ---
            self.set_status("Инициализация AI клиентов...")
            self.log("Инициализация AI клиентов...", to_terminal=True)
            llm_clients, model_name = [], ""
            if config['verification_mode'] == 'Онлайн':
                llm_clients.append(OpenAI(api_key=config['openai_api_key']))
                model_name = config['ai_chat_model']
                self.log(f"Режим 'Онлайн'. Модель: {model_name}. Воркеров: {config['ai_workers']}.", to_terminal=True)
            else: # Локальная
                timeout = httpx.Timeout(10.0, connect=5.0)
                for url in config['local_servers']:
                    try:
                        client = OpenAI(
                            base_url=prepare_api_base_url(url),
                            api_key="not-needed",
                            http_client=httpx.Client(timeout=timeout)
                        )
                        llm_clients.append(client)
                        self.log(f"Подготовлен клиент для сервера: {url}", to_terminal=True)
                    except Exception as e:
                        self.log(f"Ошибка создания клиента для {url}: {e}", to_terminal=True)

                if not llm_clients:
                    raise ConnectionError("Не удалось создать ни одного клиента для локальных серверов. Проверьте адреса.")

                model_name = config['local_chat_model']
                total_workers = len(llm_clients) * WORKERS_PER_LOCAL_SERVER
                self.log(f"Режим 'Локальная'. Модель: {model_name}. Серверов: {len(llm_clients)}. Всего воркеров: {total_workers}.", to_terminal=True)

            # --- Этап 3: Формирование и ВАЛИДАЦИЯ задач ---
            self.set_status("Формирование и валидация задач для анализа...")
            self.log("Формирование и валидация задач для анализа...", to_terminal=True)
            self.df['CollisionWith'] = self.df['CollisionWith'].fillna('').astype(str)
            self.df_indexed = self.df.set_index('ID')
            valid_ids = set(self.df['ID'])
            tasks = []

            for _, row in self.df[self.df['CollisionWith'].str.strip() != ''].iterrows():
                row_id = row['ID']
                for c_id in re.split(r'[;, ]+', row['CollisionWith']):
                    if not (clean_id := c_id.strip()):
                        continue

                    if clean_id not in valid_ids:
                        self.log(f"ПРЕДУПРЕЖДЕНИЕ: В строке ID {row_id} ссылка на несуществующий ID: '{clean_id}'. Пара пропущена.", to_terminal=True)
                        continue

                    if clean_id == row_id:
                        self.log(f"ПРЕДУПРЕЖДЕНИЕ: В строке ID {row_id} ссылка на саму себя. Пара пропущена.", to_terminal=True)
                        continue

                    pair_key = tuple(sorted((row_id, clean_id)))
                    if pair_key not in self.results_cache:
                        tasks.append({'row1_id': row_id, 'row2_id': clean_id})
                        self.results_cache[pair_key] = None

            self.log("Валидация пар завершена.", to_terminal=True)

            if not tasks:
                self.ui_queue.put(('worker_done', "Корректных коллизий для анализа не найдено."))
                return

            self.log(f"Найдено {len(tasks)} уникальных и корректных пар для AI-верификации.", to_terminal=True)
            task_queue = queue.Queue()
            [task_queue.put(t) for t in tasks]
            self.reset_progress(len(tasks))

            # --- Этап 4: Запуск воркеров ---
            self.set_status(f"Выполнение AI-анализа ({len(tasks)} пар)...")
            threads = []
            confirmed_pairs = set()
            lock = threading.Lock()

            if config['verification_mode'] == 'Онлайн':
                client = llm_clients[0]
                num_workers = config['ai_workers']
                for i in range(num_workers):
                    thread = threading.Thread(target=self._verification_worker, args=(task_queue, confirmed_pairs, lock, client, model_name, config), name=f"Worker-Online-{i+1}")
                    threads.append(thread)
            else: # Локальная
                for i, client in enumerate(llm_clients):
                    for j in range(WORKERS_PER_LOCAL_SERVER):
                        thread = threading.Thread(target=self._verification_worker, args=(task_queue, confirmed_pairs, lock, client, model_name, config), name=f"Worker-Local-{i+1}-{j+1}")
                        threads.append(thread)

            self.log(f"Запуск {len(threads)} потоков-воркеров...", to_terminal=True)
            [t.start() for t in threads]
            while any(t.is_alive() for t in threads):
                if self._stop_flag.is_set():
                    break
                time.sleep(0.5)
            [t.join() for t in threads]

            if self._stop_flag.is_set():
                self.ui_queue.put(('worker_done', "Процесс остановлен пользователем."))
                return

            # --- Этап 5: Обновление DataFrame и завершение ---
            self.set_status("Обновление таблицы с результатами...")
            self.update_dataframe_with_results(confirmed_pairs)

            total_pairs = len(self.results_cache)
            total_real = len(confirmed_pairs)
            total_false = sum(1 for res in self.results_cache.values() if res and res.get('verdict') == 'FALSE')
            total_errors = total_pairs - total_real - total_false
            pct = lambda part: round(part / total_pairs * 100, 1) if total_pairs else 0

            summary_message = (
                f"Анализ завершён!\n\n"
                f"Всего уникальных пар: {total_pairs}\n"
                f"— РЕАЛЬНЫЕ коллизии: {total_real} ({pct(total_real)}%)\n"
                f"— ЛОЖНЫЕ коллизии: {total_false} ({pct(total_false)}%)\n"
                f"— Ошибки: {total_errors} ({pct(total_errors)}%)"
            )
            self.log(summary_message, to_terminal=True)
            messagebox.showinfo("Готово", summary_message)
            self.ui_queue.put(('worker_done', "Готово. Можно сохранить отчет."))

        except Exception as e:
            error_message = f"КРИТИЧЕСКАЯ ОШИБКА: {e}"
            self.log(error_message, to_terminal=True)
            self.log(f"Traceback: {traceback.format_exc()}", to_terminal=True)
            self.ui_queue.put(('worker_done', f"Ошибка: {e}"))

    def _verification_worker(self, task_queue: queue.Queue, confirmed_pairs: Set, lock: threading.Lock, client: OpenAI, model_name: str, config: Dict):
        """Функция, выполняемая каждым потоком-воркером."""
        worker_name = threading.current_thread().name
        while not task_queue.empty():
            if self._stop_flag.is_set():
                break
            try:
                task = task_queue.get_nowait()
                id1, id2 = task['row1_id'], task['row2_id']
                self.log(f"[{worker_name}] Взял в работу пару ID: ({id1}, {id2})", to_terminal=True)
                pair_key = tuple(sorted((id1, id2)))
                row1, row2 = self.df_indexed.loc[id1], self.df_indexed.loc[id2]
                verdict_json = self._get_llm_verdict(client, model_name, row1, row2, config)

                with lock:
                    self.results_cache[pair_key] = verdict_json
                    if verdict_json and verdict_json.get('verdict') == 'REAL':
                        confirmed_pairs.add(pair_key)

            except queue.Empty:
                break
            except Exception as e:
                self.log(f"Критическая ошибка в воркере [{worker_name}]: {e}", to_terminal=True)
                break
            finally:
                self.update_progress(1)
                task_queue.task_done()
        self.log(f"[{worker_name}] Завершил работу, задачи в очереди закончились.", to_terminal=True)


    def _get_llm_verdict(self, client: OpenAI, model: str, row1: pd.Series, row2: pd.Series, config: dict) -> Dict:
        """Отправляет запрос к модели AI и возвращает JSON-ответ."""
        id1, id2 = row1.name, row2.name
        user_prompt = (f"Название ГО1: {row1['Исполняющий ГО']}\nФункция ГО1: {row1['FunctionText']}\n\n"
                       f"Название ГО2: {row2['Исполняющий ГО']}\nФункция ГО2: {row2['FunctionText']}\n")
        messages = [{"role": "system", "content": config['ai_system_prompt']}, {"role": "user", "content": user_prompt}]

        request_params = {
            "model": model,
            "messages": messages,
            "temperature": config['ai_temperature']
        }
        if config['use_json_mode']:
            request_params["response_format"] = {"type": "json_object"}

        self.log(f"ЗАПРОС к AI для пары ({id1}, {id2})", to_terminal=True)

        for i in range(3): # Механизм повтора
            if self._stop_flag.is_set():
                return {"verdict": "STOPPED"}
            try:
                resp = client.chat.completions.create(**request_params)
                content = resp.choices[0].message.content
                self.log(f"ОТВЕТ от AI для пары ({id1}, {id2}):\n---\n{content}\n---", to_terminal=True)
                match = re.search(r'\{.*\}', content, re.DOTALL)
                if match:
                    response_json = json.loads(match.group(0))
                    if 'verdict' in response_json:
                        self.log(f"ИТОГ для пары ({id1}, {id2}): Вердикт '{response_json['verdict']}'", to_terminal=True)
                        return response_json
            except (APIConnectionError, RateLimitError, httpx.TimeoutException, httpx.ConnectError) as e:
                self.log(f"Ошибка сети/лимитов/таймаута для пары ({id1}, {id2}): {type(e).__name__}. Попытка {i+1}/3. Повтор через 2 сек...", to_terminal=True)
                time.sleep(2)
            except AuthenticationError as e:
                auth_error_msg = e.body.get('message') if e.body else str(e)
                self.log(f"ОШИБКА АУТЕНТИФИКАЦИИ: {auth_error_msg}", to_terminal=True)
                self._stop_flag.set() # Останавливаем все воркеры
                return {"verdict": "AUTH_ERROR"}
            except Exception as e:
                self.log(f"Ошибка API при обработке пары ({id1}, {id2}): {e}", to_terminal=True)
                pass
        self.log(f"ИТОГ для пары ({id1}, {id2}): Не удалось получить вердикт после 3 попыток. Вердикт: ERROR", to_terminal=True)
        return {"verdict": "ERROR"}

    def update_dataframe_with_results(self, confirmed_pairs: Set):
        """Обновляет основной DataFrame результатами анализа."""
        self.log("Обновление столбцов 'CollisionWith' и 'Anomaly' в таблице.", to_terminal=True)
        confirmed_dict = defaultdict(set)
        for a, b in confirmed_pairs:
            confirmed_dict[a].add(b)
            confirmed_dict[b].add(a)

        self.df['CollisionWith'] = self.df['ID'].apply(lambda i: '; '.join(sorted(confirmed_dict.get(i, []))))
        self.df['Anomaly'] = self.df['ID'].apply(lambda i: 'Collision' if i in confirmed_dict else '')
        if 'Justification' in self.df.columns:
            self.df['Justification'] = '' # Очищаем старые данные
        self.log("Таблица успешно обновлена результатами анализа.", to_terminal=True)

    def save_file(self):
        """Сохраняет измененный DataFrame в файл Excel."""
        if self.df is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            title="Сохранить измененную таблицу"
        )
        if not path:
            return
        try:
            self.set_status(f"Сохранение файла в {path}...")
            df_to_save = self.df.copy()
            if 'Justification' in df_to_save.columns:
                df_to_save = df_to_save.drop(columns=['Justification'])
            df_to_save.to_excel(path, index=False, engine='openpyxl')
            self.log(f"Файл успешно сохранён: {path}", to_terminal=True)
            self.set_status("Файл сохранен.")
            messagebox.showinfo("Сохранено", f"Файл успешно сохранён: {path}")
        except Exception as e:
            self.log(f"Ошибка сохранения файла: {e}", to_terminal=True)
            messagebox.showerror("Ошибка сохранения", f"Не удалось сохранить файл.\nОшибка: {e}")

def main():
    """Точка входа в приложение."""
    if OpenAI is None:
        return
    root = tk.Tk()
    HybridCollisionAnalyzerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
