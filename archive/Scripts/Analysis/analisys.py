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
import asyncio

# --- ЗАВИСИМОСТИ ---
try:
    import sv_ttk
except ImportError:
    # Опциональная тема оформления, предупреждение, если не найдена
    print("="*80)
    print("ПРЕДУПРЕЖДЕНИЕ: Не найдена тема оформления 'sv-ttk'.")
    print("Интерфейс будет стандартным. Для улучшения вида установите тему: pip install sv-ttk")
    print("="*80)
    sv_ttk = None

try:
    # Основная зависимость для работы с AI
    from openai import AsyncOpenAI, APIConnectionError, RateLimitError, AuthenticationError, APIStatusError
    import httpx # Необходим для установки таймаутов
except ImportError:
    messagebox.showerror("Ошибка зависимости", "Библиотека 'openai' или 'httpx' не установлена.\nПожалуйста, установите их: pip install openai httpx")
    AsyncOpenAI = None

try:
    # Движок для записи в .xlsx файлы
    import openpyxl
except ImportError:
    messagebox.showerror("Ошибка зависимости", "Библиотека 'openpyxl' не установлена.\nОна необходима для сохранения отчетов в формате Excel (.xlsx).\n\nПожалуйста, установите ее: pip install openpyxl")
    openpyxl = None # Это предотвратит запуск, если openpyxl отсутствует


# ====== Константы и настройки по умолчанию ======

# --- Названия колонок ---
COL_ID = "ID"
COL_GO_EXECUTOR = "Исполняющий ГО"
COL_GO_PARENT = "Вышестоящий ГО"
COL_FUNCTION_TEXT = "FunctionText"
COL_IS_RELATED_VERDICT = "ИБ_Связь" # Временная колонка для вердикта AI по фильтрации
COL_ALL_FUNCTIONS_IB = "Функции" # Колонка для сгруппированных функций ИБ
COL_AI_ANALYTICS = "ИИ аналитика" # Колонка для аналитики от AI

# --- Настройки AI по умолчанию ---
DEFAULT_ONLINE_MODEL = "gpt-4o-mini"
DEFAULT_LOCAL_MODEL = "local-model/gguf-model-name" # Пример
DEFAULT_LOCAL_SERVERS = "http://localhost:1234\n"
DEFAULT_TEMPERATURE = 0.1 # Снижена температура для более фактических ответов
DEFAULT_AI_WORKERS_ONLINE = 30
WORKERS_PER_LOCAL_SERVER = 4 # Количество воркеров на каждый локальный сервер

# --- Промпты для AI ---
# Промпт для фильтрации отдельных функций по связи с ИБ
FILTER_PROMPT = """Ваша задача — проанализировать текст государственной функции и определить, относится ли она к сфере информационной безопасности (ИБ).
Учитывайте защиту данных, кибербезопасность, электронную подпись, шифрование, аутентификацию, управление информационными рисками, защиту информации в информационных системах.
Ответьте ТОЛЬКО в формате JSON: {\"verdict\": \"IS_RELATED\"} или {\"verdict\": \"NOT_RELATED\"}."""

# Промпт для суммирования всех ИБ-функций органа (Обновленный промпт для фактического анализа)
ANALYTICS_PROMPT_DEFAULT = (
    "Ты — строгий аналитик. Твоя задача — извлечь и сгруппировать ключевые действия и объекты "
    "из предоставленного списка функций органа, связанных с информационной безопасностью. "
    "Сформулируй краткое, фактическое резюме, используя терминологию, максимально приближенную к оригинальным функциям. "
    "Не придумывай того, чего нет в тексте. Не добавляй общих рассуждений, рекомендаций, предположений или приукрашиваний. "
    "Если функции очень похожи, объедини их в одно предложение. "
    "Длина ответа: 3-5 предложений, не более 150 слов. "
    "Сосредоточься на том, ЧТО делается и НАД ЧЕМ."
)

# Явные маркеры «приукрашивания», которые хотим избегать (расширен список для большей гибкости)
ANTI_EMBELLISHMENT_SUBSTRS = [
    "лучш", "практик", "обучен", "повышен", "квалификац", "внедрени",
    "стандарт", "создани", "безопасн сред", "глобальн", "экосистем",
    "стратегическ", "видени", "дорожн", "доктрин", "концепци",
]


# --- Глобальные переменные для асинхронной части ---
progress_callback_async = None
stop_event_async = asyncio.Event()


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
    """Автоматически читает Excel или CSV файлы, подбирая кодировку и разделитель."""
    low = fpath.lower()
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(fpath, dtype=str)
    if low.endswith(".csv"):
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            for sep in (",", ";", "\t", "|"):
                try:
                    return pd.read_csv(fpath, encoding=enc, sep=sep, dtype=str)
                except Exception as e:
                    last_err = e
        if last_err: raise last_err
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

def _looks_embellished(text: str) -> bool:
    """Проверяет, содержит ли текст слова, указывающие на "приукрашивание"."""
    low = text.lower()
    return any(s in low for s in ANTI_EMBELLISHMENT_SUBSTRS)


def _squeeze_spaces(text: str) -> str:
    """Сжимает множественные пробелы в тексте до одного и удаляет пробелы по краям."""
    return re.sub(r"\s+", " ", text).strip()

# Асинхронная версия generate_ai_analytics, адаптированная для использования AsyncOpenAI клиента
async def async_generate_ai_analytics(
        client: AsyncOpenAI, # Принимаем асинхронный клиент
        semaphore: asyncio.Semaphore, # Принимаем семафор для контроля параллелизма
        functions_text: str,
        system_prompt: Optional[str] = ANALYTICS_PROMPT_DEFAULT,
        model: str = DEFAULT_ONLINE_MODEL, # Модель теперь передается
        max_tokens: int = 2048,
        temperature: float = DEFAULT_TEMPERATURE, # Используем новую DEFAULT_TEMPERATURE
        top_p: float = 0.0, # Фиксировано по дизайну analisys_new.py
        max_retries: int = 3, # Добавляем для обратной совместимости с _call_api_with_backoff
) -> str:
    """
    Возвращает краткую «ИИ аналитику» (3-5 предложений) на основе текста функций органа.
    Без приукрашиваний. Если вход очень короткий — вернёт сжатое повторение функции.
    Адаптировано для использования с AsyncOpenAI клиентом и семафором.
    """
    functions_text = functions_text.strip()
    if not functions_text:
        return "нет данных"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": functions_text},
    ]

    response_text = "ОШИБКА API: Неизвестная ошибка." # Инициализация для случая ошибки
    try:
        # Используем semaphore и _call_api_with_backoff для управления вызовами
        response_text = await _call_api_with_backoff(
            client=client,
            model=model,
            messages=messages,
            temperature=temperature,
            semaphore=semaphore,
            max_tokens=max_tokens,
            max_retries=max_retries
        )
        text = _squeeze_spaces(response_text)

        # Мини-санитайзер: если модель «поплыла» в сторону общих фраз —
        # просим её пересказать ещё короче и фактически.
        if _looks_embellished(text):
            retry_messages = [
                {"role": "system", "content": system_prompt + "\nОтветь ещё короче и строго по факту, используя только термины из предоставленных функций. Не добавляй общих фраз."},
                {"role": "user", "content": functions_text},
            ]
            response_text_retry = await _call_api_with_backoff(
                client=client,
                model=model,
                messages=retry_messages,
                temperature=temperature,
                semaphore=semaphore,
                max_tokens=max_tokens // 2,  # делаем короче
                max_retries=max_retries
            )
            text2 = _squeeze_spaces(response_text_retry)
            if text2 and not text2.startswith("ОШИБКА API") and not text2 == "ОСТАНОВЛЕНО":
                text = text2

        # Страховка: если ничего внятного не получилось — вернём сжатую ключевую функцию
        if not text or len(text) < 3 or text.startswith("ОШИБКА API") or text == "ОСТАНОВЛЕНО":
            words = re.findall(r"[А-Яа-яA-Za-z0-9\-]{2,}", functions_text)
            text = " ".join(words[:12]) if words else "нет данных"

        return text

    except Exception as e:
        # В бою можно добавить логирование детализированное
        return f"нет данных (Ошибка при генерации: {e})"


# =============================== Основной класс приложения ===============================

class ISBA_App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ISBA-анализатор функций с AI")
        self.root.geometry("1100x850")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)

        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        # Данные для расчета ETA
        self.eta_data: Dict[str, Dict[str, Any]] = {}
        self.eta_labels: Dict[str, ttk.Label] = {}
        self.progress_bars: Dict[str, ttk.Progressbar] = {}

        self.last_report_df: Optional[pd.DataFrame] = None # Для хранения последнего сгенерированного отчета

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
                elif command == 'enable_save_button': # Новый обработчик для включения кнопки сохранения
                    self.save_report_btn.config(state=tk.NORMAL)
                    self.last_report_df = value # Передаем сюда DataFrame для сохранения
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
        """Создает все элементы графического интерфейса."""
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

        ai_config_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки AI")
        ai_config_frame.pack(fill=tk.X, pady=5)
        self.verification_mode_var = tk.StringVar(value="Онлайн")
        mode_frame = ttk.Frame(ai_config_frame)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(mode_frame, text="Онлайн (OpenAI API)", variable=self.verification_mode_var, value="Онлайн", command=self._on_verification_mode_change).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="Локальная (LM Studio)", variable=self.verification_mode_var, value="Локальная", command=self._on_verification_mode_change).pack(side=tk.LEFT, padx=10)

        # -- Онлайн-настройки --
        self.online_frame = ttk.Frame(ai_config_frame)
        self.openai_api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.chat_model_var = tk.StringVar(value=DEFAULT_ONLINE_MODEL)
        self.ai_workers_var = tk.IntVar(value=DEFAULT_AI_WORKERS_ONLINE)
        self._create_entry_row(self.online_frame, "Ключ OpenAI API:", self.openai_api_key_var, show="*")
        self._create_entry_row(self.online_frame, "Модель OpenAI:", self.chat_model_var)
        self._create_entry_row(self.online_frame, "Воркеров:", self.ai_workers_var)

        # -- Локальные-настройки --
        self.local_frame = ttk.Frame(ai_config_frame)
        self.local_model_var = tk.StringVar(value=DEFAULT_LOCAL_MODEL)
        self._create_entry_row(self.local_frame, "Локальная модель:", self.local_model_var)
        ttk.Label(self.local_frame, text=f"Будет ~{WORKERS_PER_LOCAL_SERVER} воркера на сервер.").pack(anchor='w', padx=5)
        ttk.Label(self.local_frame, text="Адреса серверов LM Studio:").pack(anchor='w', pady=(5,0), padx=5)
        self.local_servers_text = tk.Text(self.local_frame, height=3)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=2, padx=5)
        self.local_servers_text.insert(tk.END, DEFAULT_LOCAL_SERVERS)
        self._on_verification_mode_change() # Вызов для корректного отображения начальных настроек

        # -- Общие настройки AI --
        self.temperature_var = tk.DoubleVar(value=DEFAULT_TEMPERATURE)
        common_ai_frame = ttk.Frame(ai_config_frame)
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
        # Новая кнопка сохранения
        self.save_report_btn = ttk.Button(btn_row, text="Сохранить отчет", command=self._on_save_report, state=tk.DISABLED)
        self.save_report_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5, padx=10)


        # Прогресс бары для разных этапов
        self._create_progress_bar(controls_frame, 'filter_ib', "Этап 1: AI-фильтрация функций ИБ")
        self._create_progress_bar(controls_frame, 'summarize_ai', "Этап 2: AI-аналитика по ГО")


        notebook = ttk.Notebook(right_panel)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self.filter_prompt_text = self._create_prompt_tab(notebook, "Промпт AI-фильтрации", FILTER_PROMPT)
        # Использование улучшенного промпта из analisys_new.py
        self.summarize_prompt_text = self._create_prompt_tab(notebook, "Промпт AI-аналитики", ANALYTICS_PROMPT_DEFAULT)
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
        """Показывает или скрывает настройки в зависимости от выбранного режима AI."""
        if self.verification_mode_var.get() == "Онлайн":
            self.local_frame.pack_forget()
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            self.online_frame.pack_forget()
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    def _create_file_row(self, parent, label, var, cmd):
        """Вспомогательная функция для создания строки выбора файла."""
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=15).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    def _create_entry_row(self, parent, label, var, **kwargs):
        """Вспомогательная функция для создания строки ввода данных."""
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=15).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

    def _create_progress_bar(self, parent, key, text):
        """Вспомогательная функция для создания прогресс-бара с ETA."""
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=5, pady=(8,0))
        ttk.Label(row, text=text).pack(side=tk.LEFT)
        self.eta_labels[key] = ttk.Label(row, text="ETA: --:--:--")
        self.eta_labels[key].pack(side=tk.RIGHT)

        self.progress_bars[key] = ttk.Progressbar(parent)
        self.progress_bars[key].pack(fill=tk.X, padx=5, pady=(2,5))

    def _create_prompt_tab(self, notebook, title, content):
        """Вспомогательная функция для создания вкладки с промптом."""
        frame = ttk.Frame(notebook, padding=10)
        notebook.add(frame, text=title)
        text_widget = tk.Text(frame, height=8, wrap=tk.WORD)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert(tk.END, content)
        return text_widget

    def _choose_input_file(self):
        """Открывает диалог выбора исходного файла."""
        f = filedialog.askopenfilename(filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"), ("Все", "*.*")])
        if f: self.input_file_var.set(f)

    def _choose_output_file(self):
        """Открывает диалог выбора файла для сохранения отчета."""
        f = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if f: self.output_file_var.set(f)

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
            config = self._validate_config()
        except ValueError as e:
            return messagebox.showerror("Ошибка в настройках", str(e))

        self._stop_flag.clear()
        global stop_event_async # Устанавливаем глобальный stop_event_async
        stop_event_async.clear()

        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.save_report_btn.config(state=tk.DISABLED) # Отключаем кнопку сохранения при старте
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
            'filter_prompt': self.filter_prompt_text.get("1.0", tk.END).strip(),
            'summarize_prompt': self.summarize_prompt_text.get("1.0", tk.END).strip(),
            'temperature': self.temperature_var.get(),
            'max_tokens': 2048 # Установим разумное ограничение для аналитики, если не задано
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
            config['workers'] = len(config['local_servers']) * WORKERS_PER_LOCAL_SERVER # Динамическое определение воркеров

        return config

    def _on_stop(self):
        """Устанавливает флаг для остановки рабочего потока."""
        if self._worker_thread and self._worker_thread.is_alive():
            self.log("Запрошена остановка... Завершаю текущие операции.", to_terminal=True)
            self._stop_flag.set()
            global stop_event_async
            stop_event_async.set() # Устанавливаем stop_event_async для асинхронных задач
            self.stop_btn.config(state=tk.DISABLED)
            self.save_report_btn.config(state=tk.DISABLED) # Отключаем кнопку сохранения при остановке

    def _on_worker_finished(self, final_message: str):
        """Вызывается по завершении или остановке рабочего потока."""
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._timer_running = False
        self.set_status(final_message)
        # Сброс ETA при завершении
        for label in self.eta_labels.values():
            label.config(text="ETA: --:--:--")
        self.log(final_message.upper(), to_terminal=True)
        self.log("="*50, to_terminal=True)
        # Включаем кнопку сохранения, если анализ успешно завершен
        if "успешно завершен" in final_message.lower() and self.last_report_df is not None:
            self.ui_queue.put(('enable_save_button', self.last_report_df))


    def _run_async_worker(self, config: dict):
        """Запускает asyncio event loop в отдельном потоке."""
        try:
            asyncio.run(self._analysis_logic(config))
        except Exception as e:
            self.log(f"Критическая ошибка в асинхронном потоке: {e}", to_terminal=True)
            self.log(traceback.format_exc(), to_terminal=True)
            self.ui_queue.put(('worker_done', f"Ошибка: {e}"))

    async def _analysis_logic(self, config: dict):
        """Основная асинхронная логика анализа."""
        clients = []
        final_output_df = pd.DataFrame() # Инициализация пустого DataFrame на случай ошибки
        try:
            # --- Этап 0: Подготовка ---
            self.set_status("Подготовка: чтение данных и инициализация AI...")
            self.log("Чтение исходного файла...", to_terminal=True)
            df_input = read_table_auto(config['input_file'])

            required_cols = [COL_ID, COL_GO_EXECUTOR, COL_GO_PARENT, COL_FUNCTION_TEXT]
            missing_cols = [col for col in required_cols if col not in df_input.columns]
            if missing_cols:
                raise ValueError(f"В исходном файле отсутствуют обязательные столбцы: {', '.join(missing_cols)}")

            df_input.dropna(subset=[COL_FUNCTION_TEXT], inplace=True)
            df_input = df_input[df_input[COL_FUNCTION_TEXT].str.strip() != ''].copy()
            if df_input.empty:
                raise ValueError("В исходном файле нет функций для анализа.")

            self.log(f"Загружено {len(df_input)} функций.", to_terminal=True)

            clients, semaphore = _initialize_clients(config)
            if not clients:
                raise ConnectionError("Не удалось создать ни одного AI-клиента.")

            # --- Этап 1: AI-фильтрация функций ---
            self.set_status(f"Этап 1: AI-фильтрация функций ИБ ({len(df_input)} функций)...")
            self.ui_queue.put(('progress_reset', ('filter_ib', len(df_input))))

            filter_tasks = []
            for i, row_data in enumerate(df_input.to_dict('records')):
                if stop_event_async.is_set(): break
                filter_tasks.append(
                    self._run_ai_filter_task(
                        client=clients[i % len(clients)],
                        semaphore=semaphore,
                        prompt_template=config['filter_prompt'],
                        text_to_check=row_data[COL_FUNCTION_TEXT],
                        config=config,
                        row_data=row_data
                    )
                )

            filtered_results = await asyncio.gather(*filter_tasks)

            if stop_event_async.is_set():
                self.ui_queue.put(('worker_done', "Процесс остановлен пользователем."))
                return

            df_filtered_ib = pd.DataFrame([res for res in filtered_results if res is not None])
            if df_filtered_ib.empty:
                self.log("AI-фильтрация не выявила функций, связанных с ИБ. Отчет будет пустым.", to_terminal=True)
                # Устанавливаем пустой DataFrame для сохранения
                self.last_report_df = pd.DataFrame(columns=[COL_GO_PARENT, COL_GO_EXECUTOR, COL_ALL_FUNCTIONS_IB, COL_AI_ANALYTICS])
                self.ui_queue.put(('worker_done', "Анализ завершен, функций ИБ не найдено."))
                return

            self.log(f"AI-фильтрация завершена. Найдено {len(df_filtered_ib)} функций, связанных с ИБ.", to_terminal=True)

            # --- Этап 2: Группировка функций и формирование промежуточной таблицы ---
            self.set_status("Этап 2: Группировка функций и подготовка для AI-аналитики...")
            self.log("Группировка функций по исполнителям...", to_terminal=True)

            # Группируем по Исполняющему ГО и Родительскому ГО
            # Используем apply для агрегации текстов функций
            grouped_df = df_filtered_ib.groupby([COL_GO_PARENT, COL_GO_EXECUTOR]).apply(
                lambda x: '\n\n'.join(x[COL_FUNCTION_TEXT].tolist())
            ).reset_index(name=COL_ALL_FUNCTIONS_IB)

            self.log(f"Сгруппировано {len(grouped_df)} уникальных исполнителей для AI-аналитики.", to_terminal=True)

            # --- Этап 3: AI-аналитика по ГО (Суммирующий анализ) ---
            self.set_status(f"Этап 3: AI-аналитика по ГО ({len(grouped_df)} исполнителей)...")
            self.ui_queue.put(('progress_reset', ('summarize_ai', len(grouped_df))))

            summarize_tasks = []
            for i, row_data in enumerate(grouped_df.to_dict('records')):
                if stop_event_async.is_set(): break
                summarize_tasks.append(
                    self._run_ai_summarize_task(
                        client=clients[i % len(clients)],
                        semaphore=semaphore,
                        system_prompt=config['summarize_prompt'], # Теперь это system_prompt для async_generate_ai_analytics
                        functions_text=row_data[COL_ALL_FUNCTIONS_IB],
                        config=config,
                        row_data=row_data
                    )
                )

            summarized_results = await asyncio.gather(*summarize_tasks)

            if stop_event_async.is_set():
                self.ui_queue.put(('worker_done', "Процесс остановлен пользователем."))
                return

            df_final_report = pd.DataFrame([res for res in summarized_results if res is not None])

            if df_final_report.empty:
                self.log("AI-аналитика не смогла сформировать отчеты. Отчет будет пустым.", to_terminal=True)
                # Устанавливаем пустой DataFrame для сохранения
                self.last_report_df = pd.DataFrame(columns=[COL_GO_PARENT, COL_GO_EXECUTOR, COL_ALL_FUNCTIONS_IB, COL_AI_ANALYTICS])
                self.ui_queue.put(('worker_done', "Анализ завершен, аналитика не сгенерирована."))
                return

            # --- Этап 4: Сохранение итогового отчета ---
            self.set_status("Формирование и сохранение итогового отчета...")
            self.log(f"Итоговый отчет готов. Вы можете сохранить его, нажав 'Сохранить отчет'.", to_terminal=True)

            # Выбираем только необходимые колонки для финального отчета
            final_output_df = df_final_report[[
                COL_GO_PARENT,
                COL_GO_EXECUTOR,
                COL_ALL_FUNCTIONS_IB,
                COL_AI_ANALYTICS
            ]].copy()

            self.last_report_df = final_output_df # Сохраняем отчет в переменную класса

            # Теперь не сохраняем автоматически, а ждем нажатия кнопки "Сохранить отчет"
            # final_output_df.to_excel(config['output_file'], index=False, engine='openpyxl')
            # self.log(f"Отчет успешно сохранен в: {config['output_file']}", to_terminal=True)

            self.ui_queue.put(('worker_done', "Анализ успешно завершен!"))
            self.ui_queue.put(('enable_save_button', self.last_report_df)) # Передаем DataFrame для сохранения

        except Exception as e:
            self.log(f"Критическая ошибка: {e}", to_terminal=True)
            self.log(traceback.format_exc(), to_terminal=True)
            self.ui_queue.put(('worker_done', f"Ошибка: {e}"))
        finally:
            if clients:
                await asyncio.gather(*(client.close() for client in clients))


    async def _run_ai_filter_task(self, client: AsyncOpenAI, semaphore: asyncio.Semaphore, prompt_template: str, text_to_check: str, config: dict, row_data: Dict) -> Optional[Dict]:
        """Выполняет один запрос к AI для фильтрации функций по ИБ."""
        if stop_event_async.is_set():
            return None

        row_id = row_data.get(COL_ID, 'N/A')
        messages = [
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": text_to_check}
        ]

        response_text = await _call_api_with_backoff(
            client=client,
            model=config['model'],
            messages=messages,
            temperature=config['temperature'],
            semaphore=semaphore,
            max_tokens=config['max_tokens'],
            max_retries=3 # Можно настроить количество повторов
        )
        verdict = parse_json_verdict(response_text)

        self.ui_queue.put(('progress_update', ('filter_ib', 1)))

        if verdict == "IS_RELATED":
            self.log(f"[AI-ФИЛЬТР] ID: {row_id} -> СВЯЗАНО С ИБ (Вердикт: {verdict})")
            # Возвращаем копию данных строки для дальнейшей обработки
            result_row = row_data.copy()
            result_row[COL_IS_RELATED_VERDICT] = "IS_RELATED"
            return result_row
        else:
            self.log(f"[AI-ФИЛЬТР] ID: {row_id} -> НЕ СВЯЗАНО С ИБ (Вердикт: {verdict}, Ответ: {response_text[:100].strip()})")
            return None

    async def _run_ai_summarize_task(self, client: AsyncOpenAI, semaphore: asyncio.Semaphore, system_prompt: str, functions_text: str, config: dict, row_data: Dict) -> Optional[Dict]:
        """
        Выполняет один запрос к AI для суммирования функций ГО в сфере ИБ,
        используя улучшенную функцию async_generate_ai_analytics.
        """
        if stop_event_async.is_set():
            return None

        executor_go = row_data.get(COL_GO_EXECUTOR, 'N/A')

        # Используем интегрированную функцию async_generate_ai_analytics
        response_text = await async_generate_ai_analytics(
            client=client,
            semaphore=semaphore,
            functions_text=functions_text,
            system_prompt=system_prompt,
            model=config['model'],
            max_tokens=config['max_tokens'],
            temperature=config['temperature'],
            top_p=0.0, # Фиксировано по дизайну analisys_new.py
            max_retries=3
        )

        self.ui_queue.put(('progress_update', ('summarize_ai', 1)))

        if response_text.startswith("ОШИБКА API") or response_text == "ОСТАНОВЛЕНО" or response_text.startswith("нет данных"):
            self.log(f"[AI-АНАЛИТИКА] ГО: {executor_go} -> ОШИБКА/ОСТАНОВЛЕНО/НЕТ ДАННЫХ. Подробности: {response_text}")
            result_row = row_data.copy()
            result_row[COL_AI_ANALYTICS] = response_text
            return result_row
        else:
            self.log(f"[AI-АНАЛИТИКА] ГО: {executor_go} -> УСПЕХ. Аналитика: {response_text[:100].strip()}...")
            result_row = row_data.copy()
            result_row[COL_AI_ANALYTICS] = response_text
            return result_row

    def _on_save_report(self):
        """Обрабатывает нажатие кнопки 'Сохранить отчет'."""
        if self.last_report_df is None or self.last_report_df.empty:
            messagebox.showinfo("Сохранение отчета", "Нет данных для сохранения. Проведите анализ сначала.")
            return

        output_file = self.output_file_var.get().strip()
        if not output_file:
            messagebox.showerror("Ошибка сохранения", "Укажите файл для сохранения отчета.")
            return

        try:
            self.last_report_df.to_excel(output_file, index=False, engine='openpyxl')
            self.log(f"Отчет успешно сохранен в: {output_file}", to_terminal=True)
            messagebox.showinfo("Сохранение отчета", f"Отчет успешно сохранен в: {output_file}")
        except Exception as e:
            self.log(f"Ошибка при сохранении отчета: {e}", to_terminal=True)
            messagebox.showerror("Ошибка сохранения", f"Не удалось сохранить отчет: {e}")

async def _call_api_with_backoff(client: AsyncOpenAI, model: str, messages: List[Dict], temperature: float, semaphore: asyncio.Semaphore, max_tokens: int, max_retries: int) -> str:
    """Выполняет вызов API с обработкой ошибок и экспоненциальной задержкой."""
    last_exception_str = "Unknown error."
    for attempt in range(max_retries):
        if stop_event_async.is_set():
            return "ОСТАНОВЛЕНО"
        try:
            async with semaphore: # Управление параллельными запросами
                # Note: max_completion_tokens is the correct parameter name for OpenAI API >= 1.0.0
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_completion_tokens=max_tokens # Правильное имя параметра
                )
                return resp.choices[0].message.content or ""
        except APIStatusError as e:
            error_body = "N/A"
            try:
                error_body = e.response.json()
            except Exception:
                error_body = e.response.text
            last_exception_str = f"APIStatusError (HTTP {e.status_code}): {error_body}"
            # self.log(f"Ошибка API (попытка {attempt+1}/{max_retries}): {last_exception_str}. Повтор через {2**attempt} сек...", to_terminal=True) # Отладочный вывод
            await asyncio.sleep(2**attempt)
        except (APIConnectionError, RateLimitError, httpx.TimeoutException) as e:
            last_exception_str = f"Network/API Error ({type(e).__name__}): {e}"
            # self.log(f"Ошибка сети/API (попытка {attempt+1}/{max_retries}): {last_exception_str}. Повтор через {2**attempt} сек...", to_terminal=True) # Отладочный вывод
            await asyncio.sleep(2**attempt)
        except AuthenticationError as e:
            auth_error_msg = e.body.get('message') if e.body else str(e)
            last_exception_str = f"Authentication Error: {auth_error_msg}"
            # self.log(f"КРИТИЧЕСКАЯ ОШИБКА АУТЕНТИФИКАЦИИ: {last_exception_str}. Проверьте API ключ.", to_terminal=True) # Отладочный вывод
            stop_event_async.set() # Останавливаем все воркеры при ошибке аутентификации
            return "ОШИБКА АУТЕНТИФИКАЦИИ"
        except Exception as e:
            last_exception_str = f"Неизвестная ошибка: {e}"
            # self.log(f"Неизвестная ошибка при вызове API (попытка {attempt+1}/{max_retries}): {last_exception_str}. Повтор через {2**attempt} сек...", to_terminal=True) # Отладочный вывод
            await asyncio.sleep(2**attempt)

    # self.log(f"Не удалось выполнить вызов API после {max_retries} попыток. Последняя ошибка: {last_exception_str}", to_terminal=True) # Отладочный вывод
    return f"ОШИБКА API: {last_exception_str}"

def _initialize_clients(config: dict) -> Tuple[List[AsyncOpenAI], asyncio.Semaphore]:
    """Инициализирует клиентов AsyncOpenAI и семафор на основе конфигурации."""
    clients = []
    timeout = httpx.Timeout(20.0, connect=10.0) # Увеличим таймаут для стабильности

    if config['verification_mode'] == 'Онлайн':
        client = AsyncOpenAI(api_key=config['api_key'], http_client=httpx.AsyncClient(timeout=timeout))
        clients.append(client)
        semaphore = asyncio.Semaphore(config['workers'])
        print(f"Инициализирован онлайн клиент. Модель: {config['model']}. Воркеров: {config['workers']}.", file=sys.stderr) # Логирование в stderr для отладки
    else: # Локальная
        # Динамическое распределение воркеров по серверам
        num_local_servers = len(config['local_servers'])
        workers_per_server = max(1, config['workers'] // num_local_servers) if num_local_servers > 0 else 0

        for url in config['local_servers']:
            client = AsyncOpenAI(base_url=prepare_api_base_url(url), api_key="not-needed", http_client=httpx.AsyncClient(timeout=timeout))
            clients.append(client)
            print(f"Подготовлен клиент для локального сервера: {url}", file=sys.stderr) # Логирование в stderr для отладки

        # Общий семафор для всех локальных воркеров
        semaphore = asyncio.Semaphore(config['workers']) # Общее количество воркеров
        print(f"Инициализированы {len(clients)} локальных клиентов. Модель: {config['model']}. Всего воркеров: {config['workers']}.", file=sys.stderr) # Логирование в stderr для отладки

    return clients, semaphore


def main():
    """Точка входа в приложение."""
    if AsyncOpenAI is None or openpyxl is None:
        return
    # Для Windows, чтобы избежать проблем с asyncio в потоках
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    root = tk.Tk()
    ISBA_App(root)
    root.mainloop()

if __name__ == "__main__":
    main()

