from __future__ import annotations

import asyncio
import sys
import threading
import time
from typing import Any, Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import sv_ttk  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    sv_ttk = None

from ..core.analysis.typing import (
    ALL_CATEGORIES,
    DEFAULT_EMBEDDING_MODEL_NAME,
    DEFAULT_EMBEDDING_SERVER,
    DEFAULT_LOCAL_CONCURRENT,
    DEFAULT_LOCAL_MAX_TOKENS,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_LOCAL_RETRIES,
    DEFAULT_LOCAL_TEMP,
    DEFAULT_NEEDS_REVIEW_LABEL,
    DEFAULT_ONLINE_CONCURRENT,
    DEFAULT_ONLINE_MAX_TOKENS,
    DEFAULT_ONLINE_MODEL,
    DEFAULT_ONLINE_RETRIES,
    DEFAULT_ONLINE_TEMP,
    TypologyConfig,
    run_typology_analysis,
    SYS_PROMPT_1_TEMPLATE,
    SYS_PROMPT_1_REFINEMENT_TEMPLATE,
    SYS_PROMPT_2_TEMPLATE,
    SYS_PROMPT_3_TEMPLATE,
)
from ..gui.app_base import AppBase
from ..gui import widgets


class FunctionTypologyApp(AppBase):
    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root.title("Анализатор типов функций v4.5 (Дополнено)")
        self.root.geometry("1200x950")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self._timer_running = False
        self._start_time = 0.0

        self.progress_bars: Dict[str, ttk.Progressbar] = {}
        self.stage_progress: Dict[str, int] = {}
        self.etr_labels: Dict[str, tk.Label] = {}

        self._worker_thread: Optional[threading.Thread] = None

        # UI variables
        self.gold_path_var = tk.StringVar()
        self.input_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()

        self.ai_mode_var = tk.StringVar(value="Онлайн")

        self.api_key_var = tk.StringVar()
        self.online_model_var = tk.StringVar(value=DEFAULT_ONLINE_MODEL)
        self.online_concurrent_var = tk.IntVar(value=DEFAULT_ONLINE_CONCURRENT)
        self.online_retries_var = tk.IntVar(value=DEFAULT_ONLINE_RETRIES)
        self.online_temp_var = tk.DoubleVar(value=DEFAULT_ONLINE_TEMP)
        self.online_tokens_var = tk.IntVar(value=DEFAULT_ONLINE_MAX_TOKENS)

        self.local_model_var = tk.StringVar(value=DEFAULT_LOCAL_MODEL)
        self.local_concurrent_var = tk.IntVar(value=DEFAULT_LOCAL_CONCURRENT)
        self.local_retries_var = tk.IntVar(value=DEFAULT_LOCAL_RETRIES)
        self.local_temp_var = tk.DoubleVar(value=DEFAULT_LOCAL_TEMP)
        self.local_tokens_var = tk.IntVar(value=DEFAULT_LOCAL_MAX_TOKENS)
        self.local_servers_text: Optional[tk.Text] = None

        self.use_embeddings_var = tk.BooleanVar(value=False)
        self.embed_server_var = tk.StringVar(value=DEFAULT_EMBEDDING_SERVER)
        self.embed_model_var = tk.StringVar(value=DEFAULT_EMBEDDING_MODEL_NAME)

        self.prompt1_text: Optional[tk.Text] = None
        self.prompt1_refine_text: Optional[tk.Text] = None
        self.prompt2_text: Optional[tk.Text] = None
        self.prompt3_text: Optional[tk.Text] = None

        self.status_label: Optional[ttk.Label] = None
        self.timer_label: Optional[ttk.Label] = None
        self.log_text: Optional[tk.Text] = None
        self.start_btn: Optional[ttk.Button] = None
        self.stop_btn: Optional[ttk.Button] = None

        self.online_frame: Optional[ttk.Frame] = None
        self.local_frame: Optional[ttk.Frame] = None

        self.build_ui()
        self.root.after(500, self._tick_ui)

    # ------------------------------------------------------------------ UI setup
    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main, width=360)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        files_group = ttk.LabelFrame(left, text="Файлы")
        files_group.pack(fill=tk.X, pady=5)
        widgets.create_file_row(
            files_group,
            "Золотой стандарт:",
            self.gold_path_var,
            lambda: self._choose_file(self.gold_path_var, "Выберите файл с золотым стандартом"),
        )
        widgets.create_file_row(
            files_group,
            "Входной файл:",
            self.input_path_var,
            lambda: self._choose_file(self.input_path_var, "Выберите файл для обработки"),
        )
        widgets.create_file_row(
            files_group,
            "Выходной файл:",
            self.output_path_var,
            lambda: self._choose_file(self.output_path_var, "Укажите путь для сохранения результата", is_save=True),
        )

        ai_group = ttk.LabelFrame(left, text="Настройки ИИ")
        ai_group.pack(fill=tk.X, pady=5)
        mode_row = ttk.Frame(ai_group)
        mode_row.pack(fill=tk.X, padx=5, pady=5)
        for mode in ("Онлайн", "Локальный"):
            ttk.Radiobutton(
                mode_row,
                text=mode,
                value=mode,
                variable=self.ai_mode_var,
                command=self._on_ai_mode_change,
            ).pack(side=tk.LEFT, padx=(0, 10))

        self.online_frame = ttk.Frame(ai_group)
        widgets.create_entry_row(self.online_frame, "OpenAI API Key:", self.api_key_var, show="*")
        widgets.create_entry_row(self.online_frame, "Модель:", self.online_model_var)
        widgets.create_entry_row(self.online_frame, "Параллельность:", self.online_concurrent_var)
        widgets.create_entry_row(self.online_frame, "Повторы:", self.online_retries_var)
        widgets.create_entry_row(self.online_frame, "Температура:", self.online_temp_var)
        widgets.create_entry_row(self.online_frame, "Макс. токены:", self.online_tokens_var)

        self.local_frame = ttk.Frame(ai_group)
        server_row = ttk.Frame(self.local_frame)
        server_row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(server_row, text="Серверы (по строке):", width=20).pack(side=tk.LEFT, anchor="n")
        self.local_servers_text = tk.Text(server_row, height=4, width=26)
        self.local_servers_text.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.local_servers_text.insert("1.0", "http://localhost:1234")
        widgets.create_entry_row(self.local_frame, "Модель:", self.local_model_var)
        widgets.create_entry_row(self.local_frame, "Параллельность:", self.local_concurrent_var)
        widgets.create_entry_row(self.local_frame, "Повторы:", self.local_retries_var)
        widgets.create_entry_row(self.local_frame, "Температура:", self.local_temp_var)
        widgets.create_entry_row(self.local_frame, "Макс. токены:", self.local_tokens_var)

        self._on_ai_mode_change()

        embed_group = ttk.LabelFrame(left, text="Подсказки эмбеддингов")
        embed_group.pack(fill=tk.X, pady=5)
        ttk.Checkbutton(
            embed_group,
            text="Использовать эмбеддинги",
            variable=self.use_embeddings_var,
        ).pack(anchor="w", padx=5, pady=(5, 0))
        widgets.create_entry_row(embed_group, "Сервер:", self.embed_server_var)
        widgets.create_entry_row(embed_group, "Модель:", self.embed_model_var)

        controls_group = ttk.LabelFrame(right, text="Управление")
        controls_group.pack(fill=tk.X, pady=5)
        self.start_btn = ttk.Button(
            controls_group,
            text="Старт анализа",
            style="Accent.TButton",
            command=self._on_start,
        )
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, pady=5, ipady=4)
        self.stop_btn = ttk.Button(
            controls_group,
            text="Стоп",
            command=self._on_stop,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, pady=5, ipady=4)

        status_group = ttk.Frame(right)
        status_group.pack(fill=tk.X)
        self.status_label = ttk.Label(status_group, text="Готово.", anchor="w")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.timer_label = ttk.Label(status_group, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT, padx=5)

        progress_group = ttk.LabelFrame(right, text="Прогресс")
        progress_group.pack(fill=tk.X, pady=5)
        for key, title in [("ai1", "ИИ1"), ("ai2", "ИИ2"), ("ai3", "ИИ3"), ("embed", "Эмбеддинги")]:
            row = ttk.Frame(progress_group)
            row.pack(fill=tk.X, padx=5, pady=3)
            ttk.Label(row, text=title, width=15).pack(side=tk.LEFT)
            bar = ttk.Progressbar(row)
            bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            self.progress_bars[key] = bar
            self.stage_progress[key] = 0

        notebook = ttk.Notebook(right)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self.prompt1_text = self._create_prompt_tab(notebook, "Промпт ИИ1", SYS_PROMPT_1_TEMPLATE.strip())
        self.prompt1_refine_text = self._create_prompt_tab(
            notebook, "Промпт ИИ1 (повтор)", SYS_PROMPT_1_REFINEMENT_TEMPLATE.strip()
        )
        self.prompt2_text = self._create_prompt_tab(notebook, "Промпт ИИ2", SYS_PROMPT_2_TEMPLATE.strip())
        self.prompt3_text = self._create_prompt_tab(notebook, "Промпт ИИ3", SYS_PROMPT_3_TEMPLATE.strip())

        log_group = ttk.LabelFrame(right, text="Журнал")
        log_group.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.log_text = tk.Text(log_group, height=12, state="disabled")
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _create_prompt_tab(self, notebook: ttk.Notebook, title: str, content: str) -> tk.Text:
        frame = ttk.Frame(notebook, padding=10)
        notebook.add(frame, text=title)
        widget = tk.Text(frame, wrap=tk.WORD, height=10)
        widget.pack(fill=tk.BOTH, expand=True)
        widget.insert("1.0", content)
        return widget

    # ---------------------------------------------------------------- callbacks
    def handle_ui_event(self, event: str, payload: Any) -> None:
        if event == "status":
            if self.status_label:
                self.status_label.config(text=str(payload))
        elif event == "log":
            if self.log_text:
                self.log_text.config(state="normal")
                timestamp = time.strftime("%H:%M:%S")
                self.log_text.insert(tk.END, f"[{timestamp}] {payload}\n")
                self.log_text.config(state="disabled")
                self.log_text.see(tk.END)
        elif event == "progress":
            channel, data = payload
            if channel == "reset":
                bar_key, total = data
                bar = self.progress_bars.get(bar_key)
                if bar:
                    bar["maximum"] = max(1, total)
                    bar["value"] = 0
                    self.stage_progress[bar_key] = 0
            else:
                bar = self.progress_bars.get(channel)
                if bar:
                    bar.step(data)
                    self.stage_progress[channel] += data
        elif event == "worker_done":
            self._on_worker_finished()

    def _log(self, message: str, to_terminal: bool = False) -> None:
        self.post_ui_event("log", message)
        if to_terminal:
            print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def _progress_callback(self, command: str, data: Any) -> None:
        if command == "status":
            self.post_ui_event("status", data)
        elif command == "reset":
            self.post_ui_event("progress", ("reset", data))
        else:
            self.post_ui_event("progress", (command, data))

    # ----------------------------------------------------------------- helpers
    def _choose_file(self, var: tk.StringVar, title: str, is_save: bool = False) -> None:
        opts = {"title": title, "filetypes": [("Excel/CSV", "*.xlsx *.csv"), ("Все файлы", "*.*")]}
        path = (
            filedialog.asksaveasfilename(defaultextension=".xlsx", **opts)
            if is_save
            else filedialog.askopenfilename(**opts)
        )
        if path:
            var.set(path)

    def _on_ai_mode_change(self) -> None:
        if self.ai_mode_var.get() == "Онлайн":
            if self.local_frame:
                self.local_frame.pack_forget()
            if self.online_frame:
                self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            if self.online_frame:
                self.online_frame.pack_forget()
            if self.local_frame:
                self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    # ---------------------------------------------------------------- start/stop
    def _on_start(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("В процессе", "Анализ уже запущен.")
            return
        try:
            config = self._collect_config()
        except ValueError as exc:
            messagebox.showerror("Ошибка валидации", str(exc))
            return

        self._timer_running = True
        self._start_time = time.time()
        self.stop_event.clear()
        if self.log_text:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.config(state="disabled")
        for bar in self.progress_bars.values():
            bar["value"] = 0
        self.start_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]
        self.stop_btn.config(state=tk.NORMAL)  # type: ignore[union-attr]

        def worker() -> None:
            try:
                run_typology_analysis(
                    config=config,
                    progress_callback=self._progress_callback,
                    stop_event=self.stop_event,
                    log=lambda msg: self._log(msg, to_terminal=True),
                )
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._log(f"Произошла критическая ошибка: {exc}", to_terminal=True)
            finally:
                self.post_ui_event("worker_done", None)

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _on_stop(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self._log("Запрошена остановка... Завершаю текущие операции.", to_terminal=True)
            self.stop_event.set()
            self.stop_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]

    def _on_worker_finished(self) -> None:
        self.start_btn.config(state=tk.NORMAL)  # type: ignore[union-attr]
        self.stop_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]
        self._timer_running = False
        status = "Процесс остановлен пользователем." if self.stop_event.is_set() else "Готово."
        self.post_ui_event("status", status)

    # ------------------------------------------------------------------ timers
    def _tick_ui(self) -> None:
        if self._timer_running and self.timer_label:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(500, self._tick_ui)

    # ---------------------------------------------------------------- validate
    def _collect_config(self) -> TypologyConfig:
        gold_path = self.gold_path_var.get().strip()
        input_path = self.input_path_var.get().strip()
        output_path = self.output_path_var.get().strip()
        if not all([gold_path, input_path, output_path]):
            raise ValueError("Необходимо указать все три пути к файлам.")

        prompt1_initial = self.prompt1_text.get("1.0", tk.END) if self.prompt1_text else SYS_PROMPT_1_TEMPLATE
        prompt1_refinement = (
            self.prompt1_refine_text.get("1.0", tk.END) if self.prompt1_refine_text else SYS_PROMPT_1_REFINEMENT_TEMPLATE
        )
        prompt2 = self.prompt2_text.get("1.0", tk.END) if self.prompt2_text else SYS_PROMPT_2_TEMPLATE
        prompt3 = self.prompt3_text.get("1.0", tk.END) if self.prompt3_text else SYS_PROMPT_3_TEMPLATE

        use_embeddings = self.use_embeddings_var.get()
        embed_server = self.embed_server_var.get().strip()
        embed_model = self.embed_model_var.get().strip()

        mode = self.ai_mode_var.get()
        if mode not in {"Онлайн", "Локальный"}:
            raise ValueError("Выберите режим работы ИИ (онлайн или локальный).")

        cfg = TypologyConfig(
            gold_path=gold_path,
            input_path=input_path,
            output_path=output_path,
            ai_mode=mode,
            prompt1_initial=prompt1_initial,
            prompt1_refinement=prompt1_refinement,
            prompt2=prompt2,
            prompt3=prompt3,
            use_embeddings=use_embeddings,
            embed_server=embed_server,
            embed_model=embed_model,
        )

        if mode == "Онлайн":
            api_key = self.api_key_var.get().strip()
            if not api_key:
                raise ValueError("В режиме 'Онлайн' необходимо указать OpenAI API Key.")
            cfg.api_key = api_key
            cfg.model = self.online_model_var.get().strip()
            cfg.concurrent_requests = self._ensure_positive_int(self.online_concurrent_var.get(), "Параллельность")
            cfg.max_retries = self._ensure_positive_int(self.online_retries_var.get(), "Повторы")
            cfg.temperature = self._ensure_temperature(self.online_temp_var.get())
            cfg.max_tokens = self._ensure_positive_int(self.online_tokens_var.get(), "Макс. токены")
        else:
            servers_raw = self.local_servers_text.get("1.0", tk.END).strip().splitlines() if self.local_servers_text else []
            cfg.local_servers = [s.strip() for s in servers_raw if s.strip()]
            if not cfg.local_servers:
                raise ValueError("В режиме 'Локальный' необходимо указать хотя бы один адрес сервера.")
            cfg.model = self.local_model_var.get().strip()
            cfg.concurrent_requests = self._ensure_positive_int(self.local_concurrent_var.get(), "Параллельность")
            cfg.max_retries = self._ensure_positive_int(self.local_retries_var.get(), "Повторы")
            cfg.temperature = self._ensure_temperature(self.local_temp_var.get())
            cfg.max_tokens = self._ensure_positive_int(self.local_tokens_var.get(), "Макс. токены")

        if cfg.use_embeddings and not cfg.embed_server:
            raise ValueError("Если включена помощь эмбеддингов, необходимо указать адрес сервера.")

        return cfg

    @staticmethod
    def _ensure_positive_int(value: int, field_name: str) -> int:
        if int(value) <= 0:
            raise ValueError(f"{field_name} должно быть > 0.")
        return int(value)

    @staticmethod
    def _ensure_temperature(value: float) -> float:
        val = float(value)
        if not (0.0 <= val <= 2.0):
            raise ValueError("Температура должна быть в диапазоне от 0.0 до 2.0.")
        return val


def launch() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # pragma: no cover - platform specific
    root = tk.Tk()
    app = FunctionTypologyApp(root)
    root.mainloop()


__all__ = ["FunctionTypologyApp", "launch"]

