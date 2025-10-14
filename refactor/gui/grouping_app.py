from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

try:
    import sv_ttk  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    sv_ttk = None

from ..core.analysis.grouping import (
    CollisionAnalysisResult,
    CollisionConfig,
    DEFAULT_AI_SYSTEM_PROMPT,
    DEFAULT_CHAT_MODEL_NAME,
    DEFAULT_EMBEDDING_MODEL_NAME,
    DEFAULT_GROUPING_COLUMNS,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD,
    DEFAULT_AI_WORKERS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_EMBEDDING_WORKERS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    run_collision_analysis,
)

from ..core.file_io import read_table_auto
from ..gui import widgets
from ..gui.app_base import AppBase


class CollisionAnalyzerApp(AppBase):
    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root.title("Анализатор коллизий v14.3 (Распределение нагрузки)")
        self.root.geometry("1200x950")
        if sv_ttk:
            sv_ttk.set_theme("light")

        # --- state ---
        self._timer_running = False
        self._start_time = 0.0

        self.progress_bars: Dict[str, ttk.Progressbar] = {}
        self.etr_labels: Dict[str, ttk.Label] = {}
        self.stage_progress: Dict[str, int] = {}
        self.stage_start_times: Dict[str, float] = {}
        self._worker_thread: Optional[threading.Thread] = None

        # --- tk variables ---
        self.input_file_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self.universal_json_var = tk.StringVar()

        self.embedding_model_var = tk.StringVar(value=DEFAULT_EMBEDDING_MODEL_NAME)
        self.embed_server_var = tk.StringVar(value="http://localhost:1234")
        self.embedding_workers_var = tk.IntVar(value=DEFAULT_EMBEDDING_WORKERS)
        self.batch_size_var = tk.IntVar(value=DEFAULT_BATCH_SIZE)

        self.sim_threshold_var = tk.DoubleVar(value=DEFAULT_SIMILARITY_THRESHOLD)
        self.univ_threshold_var = tk.DoubleVar(
            value=DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD
        )

        self.ai_verify_var = tk.BooleanVar(value=True)
        self.verification_mode_var = tk.StringVar(value="Онлайн")
        self.openai_api_key_var = tk.StringVar()
        self.chat_model_var = tk.StringVar(value=DEFAULT_CHAT_MODEL_NAME)
        self.ai_workers_var = tk.IntVar(value=DEFAULT_AI_WORKERS)

        self.local_model_var = tk.StringVar(value="google/gemma-3-12b")
        self.temperature_var = tk.DoubleVar(value=DEFAULT_TEMPERATURE)
        self.max_tokens_var = tk.IntVar(value=DEFAULT_MAX_TOKENS)
        self.json_mode_var = tk.BooleanVar(value=True)

        self.local_servers_text: Optional[tk.Text] = None
        self.ai_prompt_text: Optional[tk.Text] = None
        self.system_prompt_cache = DEFAULT_AI_SYSTEM_PROMPT

        self.available_cols_listbox: Optional[tk.Listbox] = None
        self.selected_grouping_cols_listbox: Optional[tk.Listbox] = None

        self.status_label: Optional[ttk.Label] = None
        self.timer_label: Optional[ttk.Label] = None
        self.log_text: Optional[tk.Text] = None
        self.candidates_found_var = tk.StringVar(value="Найдено кандидатов: 0")
        self.start_btn: Optional[ttk.Button] = None
        self.stop_btn: Optional[ttk.Button] = None

        self._build_ui()
        self.root.after(500, self._tick_ui)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=450)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- Левая панель ---
        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Данные и группировка")
        files_frame.pack(fill=tk.X, pady=5)

        widgets.create_file_row(
            files_frame, "Исходный файл:", self.input_file_var, self._choose_input_file
        )
        widgets.create_file_row(
            files_frame,
            "Папка для отчета:",
            self.output_dir_var,
            self._choose_output_dir,
        )
        widgets.create_file_row(
            files_frame,
            "JSON универс. функций:",
            self.universal_json_var,
            self._choose_json,
        )

        grouping_frame = ttk.LabelFrame(files_frame, text="Столбцы для группировки")
        grouping_frame.pack(fill=tk.X, pady=5, padx=5)
        listboxes_frame = ttk.Frame(grouping_frame)
        listboxes_frame.pack(fill=tk.BOTH, expand=True)

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

        buttons_frame = ttk.Frame(listboxes_frame)
        buttons_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(
            buttons_frame,
            text=">>",
            command=self._add_grouping_column,
            width=4,
        ).pack(pady=5)
        ttk.Button(
            buttons_frame,
            text="<<",
            command=self._remove_grouping_column,
            width=4,
        ).pack(pady=5)

        selected_frame = ttk.Frame(listboxes_frame)
        selected_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        ttk.Label(selected_frame, text="Выбранные:").pack(anchor=tk.W)
        self.selected_grouping_cols_listbox = tk.Listbox(
            selected_frame, selectmode=tk.EXTENDED, height=6
        )
        self.selected_grouping_cols_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        selected_scrollbar = ttk.Scrollbar(
            selected_frame,
            orient="vertical",
            command=self.selected_grouping_cols_listbox.yview,
        )
        selected_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.selected_grouping_cols_listbox.config(
            yscrollcommand=selected_scrollbar.set
        )

        # Настройки эмбеддингов
        embed_frame = ttk.LabelFrame(left_panel, text="Этап 2: Векторизация")
        embed_frame.pack(fill=tk.X, pady=5)
        widgets.create_entry_row(
            embed_frame, "Сервер эмбеддингов:", self.embed_server_var
        )
        widgets.create_entry_row(embed_frame, "Модель:", self.embedding_model_var)
        widgets.create_entry_row(
            embed_frame, "Воркеры эмбеддингов:", self.embedding_workers_var
        )
        widgets.create_entry_row(embed_frame, "Batch size:", self.batch_size_var)
        widgets.create_entry_row(
            embed_frame, "Порог схожести:", self.sim_threshold_var
        )
        widgets.create_entry_row(
            embed_frame, "Порог универсальных:", self.univ_threshold_var
        )

        # Настройки AI
        ai_frame = ttk.LabelFrame(left_panel, text="Этап 3: AI-верификация")
        ai_frame.pack(fill=tk.X, pady=5)
        ttk.Checkbutton(
            ai_frame, text="Проводить AI-верификацию", variable=self.ai_verify_var
        ).pack(anchor="w", padx=5, pady=(5, 0))

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

        self.online_frame = ttk.Frame(ai_frame)
        widgets.create_entry_row(self.online_frame, "OpenAI API Key:", self.openai_api_key_var, show="*")
        widgets.create_entry_row(self.online_frame, "Модель:", self.chat_model_var)
        widgets.create_entry_row(self.online_frame, "Воркеры:", self.ai_workers_var)

        self.local_frame = ttk.Frame(ai_frame)
        widgets.create_entry_row(self.local_frame, "Модель:", self.local_model_var)
        ttk.Label(self.local_frame, text="Серверы (по одному на строку):").pack(
            anchor="w", padx=5, pady=(5, 0)
        )
        self.local_servers_text = tk.Text(self.local_frame, height=3, wrap=tk.WORD)
        self.local_servers_text.pack(fill=tk.X, expand=True, padx=5, pady=(2, 5))
        self.local_servers_text.insert("1.0", "http://localhost:1234\n")

        common_ai_frame = ttk.Frame(ai_frame)
        common_ai_frame.pack(fill=tk.X, padx=5, pady=0)
        widgets.create_entry_row(
            common_ai_frame, "Температура (0.0-2.0):", self.temperature_var
        )
        widgets.create_entry_row(
            common_ai_frame, "Макс. токенов ответа:", self.max_tokens_var
        )
        ttk.Checkbutton(
            common_ai_frame,
            text="JSON режим (только для Онлайн)",
            variable=self.json_mode_var,
        ).pack(anchor="w", padx=5, pady=3)

        self._on_verification_mode_change()

        # --- Правая панель ---
        controls_frame = ttk.LabelFrame(right_panel, text="Управление")
        controls_frame.pack(fill=tk.X, pady=5)
        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(btn_row, text="Старт", style="Accent.TButton", command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(btn_row, text="Стоп", state=tk.DISABLED, command=self._on_stop)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        progress_frame = ttk.LabelFrame(right_panel, text="Прогресс по этапам")
        progress_frame.pack(fill=tk.X, pady=5)

        self.progress_bars = {}
        self.stage_progress = {"discovery": 0, "verification": 0}

        discovery_frame = ttk.Frame(progress_frame)
        discovery_frame.pack(fill=tk.X, padx=5, pady=(8, 0))
        ttk.Label(discovery_frame, text="Поиск кандидатов (группы):").pack(side=tk.LEFT)
        self.etr_labels["discovery"] = ttk.Label(discovery_frame, text="ETR: --:--")
        self.etr_labels["discovery"].pack(side=tk.RIGHT)
        self.progress_bars["discovery"] = ttk.Progressbar(progress_frame)
        self.progress_bars["discovery"].pack(fill=tk.X, padx=5, pady=(2, 0))
        ttk.Label(progress_frame, textvariable=self.candidates_found_var).pack(
            fill=tk.X, padx=7, pady=(0, 5)
        )

        verification_frame = ttk.Frame(progress_frame)
        verification_frame.pack(fill=tk.X, padx=5, pady=(8, 0))
        ttk.Label(verification_frame, text="AI-верификация (пары):").pack(side=tk.LEFT)
        self.etr_labels["verification"] = ttk.Label(
            verification_frame, text="ETR: --:--"
        )
        self.etr_labels["verification"].pack(side=tk.RIGHT)
        self.progress_bars["verification"] = ttk.Progressbar(progress_frame)
        self.progress_bars["verification"].pack(fill=tk.X, padx=5, pady=(2, 5))

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

        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.status_label = ttk.Label(status_frame, text="Готово")
        self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)

    # ---------------------------------------------------------------- handlers
    def handle_ui_event(self, event: str, payload: Any) -> None:
        if event == "log":
            if self.log_text:
                self.log_text.config(state="normal")
                timestamp = time.strftime("%H:%M:%S")
                self.log_text.insert(tk.END, f"[{timestamp}] {payload}\n")
                self.log_text.config(state="disabled")
                self.log_text.see(tk.END)
        elif event == "status":
            if self.status_label:
                self.status_label.config(text=str(payload))
        elif event == "reset":
            bar_key, total = payload
            bar = self.progress_bars.get(bar_key)
            if bar:
                bar["maximum"] = max(1, total)
                bar["value"] = 0
                self.stage_progress[bar_key] = 0
                self.stage_start_times[bar_key] = time.time()
                if bar_key in self.etr_labels:
                    self.etr_labels[bar_key].config(text="ETR: --:--")
        elif event in {"discovery", "verification"}:
            bar = self.progress_bars.get(event)
            if bar:
                increment = payload or 1
                bar.step(increment)
                current = self.stage_progress[event] = self.stage_progress.get(event, 0) + increment
                total = bar["maximum"]
                if event in self.stage_start_times and event in self.etr_labels:
                    if current >= total:
                        self.etr_labels[event].config(text="ETR: Завершено")
                    elif current > 0:
                        elapsed = time.time() - self.stage_start_times[event]
                        ips = current / max(elapsed, 1e-6)
                        remaining = max(total - current, 0)
                        if ips > 0:
                            etr_sec = int(remaining / ips)
                            self.etr_labels[event].config(
                                text=f"ETR: {time.strftime('%M:%S', time.gmtime(etr_sec))}"
                            )
        elif event == "update_candidates_count":
            self.candidates_found_var.set(f"Найдено кандидатов: {payload}")
        elif event == "analysis_success":
            self._on_analysis_success(payload)
        elif event == "analysis_error":
            self._on_analysis_error(payload)
        elif event == "analysis_cancelled":
            self._on_analysis_cancelled()

    def _analysis_log(self, message: str) -> None:
        self.post_ui_event("log", message)

    def _progress_callback(self, event: str, payload: Any) -> None:
        self.post_ui_event(event, payload)

    # ---------------------------------------------------------------- columns chooser
    def _update_column_listboxes(self, columns: Iterable[str]) -> None:
        if not self.available_cols_listbox or not self.selected_grouping_cols_listbox:
            return
        self.available_cols_listbox.delete(0, tk.END)
        self.selected_grouping_cols_listbox.delete(0, tk.END)

        default_cols = set(DEFAULT_GROUPING_COLUMNS)
        for col in sorted(columns):
            target = (
                self.selected_grouping_cols_listbox
                if col in default_cols
                else self.available_cols_listbox
            )
            target.insert(tk.END, col)

    def _choose_input_file(self) -> None:
        filename = filedialog.askopenfilename(
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"), ("Все", "*.*")]
        )
        if not filename:
            return
        self.input_file_var.set(filename)
        try:
            df = read_table_auto(filename)
            self._update_column_listboxes(df.columns.tolist())
        except Exception as exc:
            messagebox.showerror(
                "Ошибка чтения файла",
                f"Не удалось прочитать файл для получения заголовков: {exc}",
            )
            if self.available_cols_listbox:
                self.available_cols_listbox.delete(0, tk.END)
            if self.selected_grouping_cols_listbox:
                self.selected_grouping_cols_listbox.delete(0, tk.END)

    def _choose_output_dir(self) -> None:
        directory = filedialog.askdirectory()
        if directory:
            self.output_dir_var.set(directory)

    def _choose_json(self) -> None:
        filename = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("Все", "*.*")])
        if filename:
            self.universal_json_var.set(filename)

    def _add_grouping_column(self) -> None:
        if not self.available_cols_listbox or not self.selected_grouping_cols_listbox:
            return
        for index in reversed(self.available_cols_listbox.curselection()):
            col = self.available_cols_listbox.get(index)
            if col not in self.selected_grouping_cols_listbox.get(0, tk.END):
                self.selected_grouping_cols_listbox.insert(tk.END, col)
            self.available_cols_listbox.delete(index)

    def _remove_grouping_column(self) -> None:
        if not self.available_cols_listbox or not self.selected_grouping_cols_listbox:
            return
        for index in reversed(self.selected_grouping_cols_listbox.curselection()):
            col = self.selected_grouping_cols_listbox.get(index)
            if col not in self.available_cols_listbox.get(0, tk.END):
                self.available_cols_listbox.insert(tk.END, col)
            self.selected_grouping_cols_listbox.delete(index)
        items = sorted(self.available_cols_listbox.get(0, tk.END))
        self.available_cols_listbox.delete(0, tk.END)
        for item in items:
            self.available_cols_listbox.insert(tk.END, item)

    # ---------------------------------------------------------------- verification mode toggle
    def _on_verification_mode_change(self) -> None:
        if self.verification_mode_var.get() == "Онлайн":
            if self.local_frame:
                self.local_frame.pack_forget()
            if self.online_frame:
                self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            if self.online_frame:
                self.online_frame.pack_forget()
            if self.local_frame:
                self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    # ---------------------------------------------------------------- timer
    def _tick_ui(self) -> None:
        if self._timer_running and self.timer_label:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(100, self._tick_ui)

    # ---------------------------------------------------------------- start/stop
    def _on_start(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showinfo("Выполняется", "Анализ уже запущен.")
            return
        try:
            config = self._collect_config()
        except ValueError as exc:
            messagebox.showerror("Ошибка в настройках", str(exc))
            return

        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]
        self.stop_btn.config(state=tk.NORMAL)  # type: ignore[union-attr]
        if self.log_text:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.config(state="disabled")
        for bar in self.progress_bars.values():
            bar["value"] = 0
        self.stage_progress = {key: 0 for key in self.stage_progress}
        self.stage_start_times.clear()
        self.candidates_found_var.set("Найдено кандидатов: 0")
        self.post_ui_event("log", "=" * 50)
        self.post_ui_event("log", "ЗАПУСК АНАЛИЗА КОЛЛИЗИЙ")
        self.post_ui_event("status", "Подготовка...")

        def worker() -> None:
            try:
                result = run_collision_analysis(
                    config=config,
                    progress_callback=self._progress_callback,
                    stop_event=self.stop_event,
                    log=self._analysis_log,
                )
                self.post_ui_event("analysis_success", result)
            except InterruptedError:
                self.post_ui_event("analysis_cancelled", None)
            except Exception as exc:
                self.post_ui_event("analysis_error", exc)

        self.run_in_thread(worker)

    def _on_stop(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self.post_ui_event(
                "log", "Запрошена остановка... Завершаю текущие операции."
            )
            self.stop_worker()
            self.stop_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]

    def _finalize_controls(self) -> None:
        self._timer_running = False
        self.start_btn.config(state=tk.NORMAL)  # type: ignore[union-attr]
        self.stop_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]
        self.post_ui_event("log", "=" * 50)

    # ---------------------------------------------------------------- results
    def _on_analysis_success(self, result: CollisionAnalysisResult) -> None:
        self._finalize_controls()
        summary = (
            f"Найдено кандидатов: {result.candidate_pairs}\n"
            f"Подтверждено коллизий: {result.confirmed_pairs}\n"
            f"Файл сохранен: {result.output_path}"
        )
        self.post_ui_event("status", "Готово.")
        messagebox.showinfo("Анализ завершен", summary)

    def _on_analysis_error(self, exc: Exception) -> None:
        self._finalize_controls()
        self.post_ui_event("status", f"Ошибка: {exc}")
        messagebox.showerror("Критическая ошибка", f"Произошла ошибка:\n{exc}")

    def _on_analysis_cancelled(self) -> None:
        self._finalize_controls()
        self.post_ui_event("status", "Процесс остановлен пользователем.")

    # ---------------------------------------------------------------- collect config
    def _collect_config(self) -> CollisionConfig:
        input_file = self.input_file_var.get().strip()
        output_dir = self.output_dir_var.get().strip()
        if not input_file or not output_dir:
            raise ValueError("Укажите исходный файл и папку для отчета.")

        grouping_cols = list(
            self.selected_grouping_cols_listbox.get(0, tk.END)  # type: ignore[union-attr]
        )
        if not grouping_cols:
            raise ValueError("Укажите хотя бы один столбец для группировки.")

        config = CollisionConfig(
            input_file=input_file,
            output_dir=output_dir,
            grouping_cols=grouping_cols,
            univ_json_path=self.universal_json_var.get().strip(),
            sim_thr=self.sim_threshold_var.get(),
            univ_thr=self.univ_threshold_var.get(),
            embedding_workers=self.embedding_workers_var.get(),
            batch_size=self.batch_size_var.get(),
            embed_server_url=self.embed_server_var.get().strip(),
            embed_model=self.embedding_model_var.get().strip(),
            ai_should_verify=self.ai_verify_var.get(),
            verification_mode=self.verification_mode_var.get(),
            ai_system_prompt=self.ai_prompt_text.get("1.0", tk.END).strip() if self.ai_prompt_text else DEFAULT_AI_SYSTEM_PROMPT,
            ai_temperature=self.temperature_var.get(),
            ai_max_tokens=self.max_tokens_var.get(),
            use_json_mode=self.json_mode_var.get(),
        )

        if config.ai_max_tokens <= 0:
            raise ValueError("Максимальное количество токенов должно быть > 0.")

        if config.ai_should_verify:
            if config.verification_mode == "Онлайн":
                api_key = self.openai_api_key_var.get().strip()
                if not api_key:
                    raise ValueError("В режиме 'Онлайн' необходимо указать ключ OpenAI API.")
                config.openai_api_key = api_key
                config.ai_chat_model = self.chat_model_var.get().strip() or DEFAULT_CHAT_MODEL_NAME
                workers = self.ai_workers_var.get()
                if workers <= 0:
                    raise ValueError("Количество воркеров верификации должно быть > 0.")
                config.ai_workers = workers
            else:
                raw_servers = (
                    self.local_servers_text.get("1.0", tk.END).strip().splitlines()
                    if self.local_servers_text
                    else []
                )
                config.local_servers = [server.strip() for server in raw_servers if server.strip()]
                if not config.local_servers:
                    raise ValueError("В режиме 'Локальная' нужен хотя бы один адрес сервера.")
                config.local_chat_model = self.local_model_var.get().strip() or "local-model"

        return config


def launch() -> None:
    root = tk.Tk()
    app = CollisionAnalyzerApp(root)
    root.mainloop()


__all__ = ["CollisionAnalyzerApp", "launch"]
