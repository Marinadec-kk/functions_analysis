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

from ..core.analysis.spheres import (
    SphereAnalysisResult,
    SphereConfig,
    SYSTEM_PROMPT_TEMPLATE,
    run_sphere_analysis,
)
from ..gui.app_base import AppBase
from ..gui import widgets


class SphereClassifierApp(AppBase):
    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root.title("Классификатор функций по сферам v3.3")
        self.root.geometry("1200x900")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self._timer_running = False
        self._start_time = 0.0

        # Paths
        self.spheres_path_var = tk.StringVar()
        self.functions_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()

        # AI settings
        self.ai_mode_var = tk.StringVar(value="Онлайн")
        self.api_key_var = tk.StringVar()
        self.online_model_var = tk.StringVar(value="gpt-4o-mini")
        self.online_concurrent_var = tk.IntVar(value=30)

        self.local_model_var = tk.StringVar(value="google/gemma-3-12b")
        self.local_concurrent_var = tk.IntVar(value=5)
        self.local_servers_text: Optional[tk.Text] = None

        # Embedding / processing settings
        self.embed_server_var = tk.StringVar(value="http://localhost:1234")
        self.embed_model_var = tk.StringVar(value="Qwen/Qwen3-Embedding-8B-GGUF")
        self.top_k_var = tk.IntVar(value=90)
        self.batch_size_var = tk.IntVar(value=20)
        self.retries_var = tk.IntVar(value=5)
        self.temp_var = tk.DoubleVar(value=1.0)
        self.max_tokens_var = tk.IntVar(value=4096)

        # Widgets placeholders
        self.system_prompt_text: Optional[tk.Text] = None
        self.online_frame: Optional[ttk.Frame] = None
        self.local_frame: Optional[ttk.Frame] = None
        self.log_text: Optional[tk.Text] = None
        self.status_label: Optional[ttk.Label] = None
        self.timer_label: Optional[ttk.Label] = None
        self.start_btn: Optional[ttk.Button] = None
        self.stop_btn: Optional[ttk.Button] = None

        self.progress_bars: Dict[str, ttk.Progressbar] = {}
        self.etr_labels: Dict[str, ttk.Label] = {}
        self.stage_progress: Dict[str, int] = {}
        self.stage_start_times: Dict[str, float] = {}

        self._build_ui()
        self.root.after(500, self._tick_timer)

    # ------------------------------------------------------------------ UI base
    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=420)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))

        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Файлы")
        files_frame.pack(fill=tk.X, pady=5)
        widgets.create_file_row(
            files_frame,
            "Файл со сферами:",
            self.spheres_path_var,
            lambda: self._choose_file(self.spheres_path_var, "Выберите файл со сферами"),
        )
        widgets.create_file_row(
            files_frame,
            "Файл с функциями:",
            self.functions_path_var,
            lambda: self._choose_file(self.functions_path_var, "Выберите файл с функциями"),
        )
        widgets.create_file_row(
            files_frame,
            "Итоговый файл:",
            self.output_path_var,
            lambda: self._choose_file(
                self.output_path_var, "Укажите, куда сохранить результат", is_save=True
            ),
        )

        ai_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки ИИ")
        ai_frame.pack(fill=tk.X, pady=5)
        mode_row = ttk.Frame(ai_frame)
        mode_row.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(
            mode_row,
            text="Онлайн (OpenAI API)",
            variable=self.ai_mode_var,
            value="Онлайн",
            command=self._on_ai_mode_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_row,
            text="Локальная (LM Studio)",
            variable=self.ai_mode_var,
            value="Локальная",
            command=self._on_ai_mode_change,
        ).pack(side=tk.LEFT, padx=10)

        self.online_frame = ttk.Frame(ai_frame)
        widgets.create_entry_row(self.online_frame, "OpenAI API Key:", self.api_key_var, show="*")
        widgets.create_entry_row(self.online_frame, "Модель:", self.online_model_var)
        widgets.create_entry_row(
            self.online_frame, "Параллельных запросов:", self.online_concurrent_var
        )

        self.local_frame = ttk.Frame(ai_frame)
        widgets.create_entry_row(self.local_frame, "Модель:", self.local_model_var)
        widgets.create_entry_row(
            self.local_frame, "Параллельных запросов:", self.local_concurrent_var
        )
        ttk.Label(self.local_frame, text="Адреса серверов (каждый с новой строки):").pack(
            anchor="w", pady=(8, 0), padx=5
        )
        self.local_servers_text = tk.Text(self.local_frame, height=3, wrap=tk.WORD)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=(2, 5), padx=5)
        self.local_servers_text.insert("1.0", "http://localhost:1234\n")
        self._on_ai_mode_change()

        params_frame = ttk.LabelFrame(left_panel, text="Этап 3: Параметры обработки")
        params_frame.pack(fill=tk.X, pady=5)
        widgets.create_entry_row(params_frame, "Сервер эмбеддингов:", self.embed_server_var)
        widgets.create_entry_row(params_frame, "Модель эмбеддингов:", self.embed_model_var)
        widgets.create_entry_row(params_frame, "Кандидатов (Top-K):", self.top_k_var)
        widgets.create_entry_row(params_frame, "Размер порции LLM:", self.batch_size_var)
        widgets.create_entry_row(params_frame, "Макс. попыток API:", self.retries_var)
        widgets.create_entry_row(params_frame, "Температура (0.0-2.0):", self.temp_var)
        widgets.create_entry_row(params_frame, "Макс. токенов:", self.max_tokens_var)

        controls_frame = ttk.LabelFrame(left_panel, text="Этап 4: Управление и прогресс")
        controls_frame.pack(fill=tk.X, pady=5)
        button_row = ttk.Frame(controls_frame)
        button_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(
            button_row, text="Старт", command=self._on_start, style="Accent.TButton"
        )
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(
            button_row, text="Стоп", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        for key, label in [
            ("embed_spheres", "Векторизация Сфер"),
            ("embed_funcs", "Векторизация Функций"),
            ("classify", "Классификация"),
        ]:
            row = ttk.Frame(controls_frame)
            row.pack(fill=tk.X, padx=5, pady=(8, 0))
            ttk.Label(row, text=f"{label}:").pack(side=tk.LEFT)
            etr_label = ttk.Label(row, text="ETR: --:--")
            etr_label.pack(side=tk.RIGHT)
            bar = ttk.Progressbar(controls_frame)
            bar.pack(fill=tk.X, padx=5, pady=(2, 5))
            self.progress_bars[key] = bar
            self.etr_labels[key] = etr_label
            self.stage_progress[key] = 0

        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        prompts_notebook = ttk.Notebook(right_panel)
        prompts_notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        self.system_prompt_text = self._create_prompt_tab(
            prompts_notebook, "Системный промпт", SYSTEM_PROMPT_TEMPLATE
        )

        log_frame = ttk.LabelFrame(right_panel, text="Журнал")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.log_text = tk.Text(log_frame, state="disabled", height=15, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        status_frame = ttk.Frame(right_panel)
        status_frame.pack(fill=tk.X, pady=(5, 0))
        self.status_label = ttk.Label(status_frame, text="Готово")
        self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)

    def _create_prompt_tab(self, notebook: ttk.Notebook, title: str, content: str) -> tk.Text:
        frame = ttk.Frame(notebook, padding=10)
        notebook.add(frame, text=title)
        widget = tk.Text(frame, wrap=tk.WORD, height=10)
        widget.pack(fill=tk.BOTH, expand=True)
        widget.insert("1.0", content)
        return widget

    # ---------------------------------------------------------------- callbacks
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
        elif event in {"embed_spheres", "embed_funcs", "classify"}:
            bar = self.progress_bars.get(event)
            if bar:
                increment = payload or 1
                bar.step(increment)
                self.stage_progress[event] += increment
                if event in self.etr_labels and event in self.stage_start_times:
                    current = self.stage_progress[event]
                    total = bar["maximum"]
                    if current >= total:
                        self.etr_labels[event].config(text="ETR: Завершено")
                    elif current > 0:
                        elapsed = time.time() - self.stage_start_times[event]
                        ips = current / max(elapsed, 1e-6)
                        remaining = max(total - current, 0)
                        if ips > 0:
                            etr = int(remaining / ips)
                            self.etr_labels[event].config(
                                text=f"ETR: {time.strftime('%M:%S', time.gmtime(etr))}"
                            )
        elif event == "analysis_success":
            self._on_analysis_success(payload)
        elif event == "analysis_error":
            self._on_analysis_error(payload)
        elif event == "analysis_cancelled":
            self._on_analysis_cancelled()

    def _analysis_log(self, message: str) -> None:
        print(f"[Sphere] {message}", flush=True)
        self.post_ui_event("log", message)

    def _progress_callback(self, event: str, payload: Any) -> None:
        self.post_ui_event(event, payload)

    # ----------------------------------------------------------------- actions
    def _choose_file(self, var: tk.StringVar, title: str, is_save: bool = False) -> None:
        opts = {
            "title": title,
            "filetypes": [("Excel/CSV", "*.xlsx *.csv"), ("Все файлы", "*.*")],
        }
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
        self.stop_btn.config(state=tk.NORMAL)  # type: ignore[union-attr]
        self.start_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]
        if self.log_text:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.config(state="disabled")
        for bar in self.progress_bars.values():
            bar["value"] = 0
        self.stage_progress = {k: 0 for k in self.stage_progress}
        self.stage_start_times.clear()
        self.post_ui_event("status", "В работе...")

        def worker() -> None:
            try:
                result = run_sphere_analysis(
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

        self.run_in_thread(lambda: worker())

    def _on_stop(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self.post_ui_event("log", "Запрошена остановка... Завершаю текущие операции.")
            self.stop_worker()
            self.stop_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]

    # ---------------------------------------------------------------- results
    def _on_analysis_success(self, result: SphereAnalysisResult) -> None:
        self._finalize_controls()
        summary = (
            f"Обработано функций: {result.processed_count}\n"
            f"Пропущено (уже классифицированы): {result.skipped_count}\n"
            f"Файл сохранен: {result.output_path}"
        )
        self.post_ui_event("status", "Готово.")
        messagebox.showinfo("Классификация завершена", summary)

    def _on_analysis_error(self, exc: Exception) -> None:
        self._finalize_controls()
        self.post_ui_event("status", f"Ошибка: {exc}")
        messagebox.showerror("Критическая ошибка", f"Произошла ошибка:\n{exc}")

    def _on_analysis_cancelled(self) -> None:
        self._finalize_controls()
        self.post_ui_event("status", "Процесс остановлен пользователем.")

    def _finalize_controls(self) -> None:
        self._timer_running = False
        self.start_btn.config(state=tk.NORMAL)  # type: ignore[union-attr]
        self.stop_btn.config(state=tk.DISABLED)  # type: ignore[union-attr]

    # ---------------------------------------------------------------- helpers
    def _tick_timer(self) -> None:
        if self._timer_running and self.timer_label:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(500, self._tick_timer)

    def _collect_config(self) -> SphereConfig:
        spheres_path = self.spheres_path_var.get().strip()
        functions_path = self.functions_path_var.get().strip()
        output_path = self.output_path_var.get().strip()
        if not spheres_path or not functions_path or not output_path:
            raise ValueError("Необходимо указать все три пути к файлам.")

        system_prompt = (
            self.system_prompt_text.get("1.0", tk.END) if self.system_prompt_text else SYSTEM_PROMPT_TEMPLATE
        )
        embed_server = self.embed_server_var.get().strip()
        embed_model = self.embed_model_var.get().strip()
        if not embed_server or not embed_model:
            raise ValueError("Укажите сервер и модель эмбеддингов.")

        mode = self.ai_mode_var.get()
        if mode not in {"Онлайн", "Локальная"}:
            raise ValueError("Выберите режим работы ИИ.")

        top_k = self._require_positive(self.top_k_var.get(), "Кандидатов (Top-K)")
        batch_size = self._require_positive(self.batch_size_var.get(), "Размер порции LLM")
        retries = self._require_positive(self.retries_var.get(), "Макс. попыток API")
        temperature = self._require_temperature(self.temp_var.get())
        max_tokens = self._require_positive(self.max_tokens_var.get(), "Макс. токенов")

        if mode == "Онлайн":
            api_key = self.api_key_var.get().strip()
            if not api_key:
                raise ValueError("В режиме 'Онлайн' необходимо указать OpenAI API Key.")
            model = self.online_model_var.get().strip()
            concurrent = self._require_positive(self.online_concurrent_var.get(), "Параллельных запросов")
            local_servers: List[str] = []
        else:
            raw_servers = self.local_servers_text.get("1.0", tk.END).strip().splitlines() if self.local_servers_text else []
            local_servers = [s.strip() for s in raw_servers if s.strip()]
            if not local_servers:
                raise ValueError("Укажите хотя бы один адрес локального сервера.")
            model = self.local_model_var.get().strip()
            concurrent = self._require_positive(self.local_concurrent_var.get(), "Параллельных запросов")
            api_key = ""

        return SphereConfig(
            spheres_path=spheres_path,
            functions_path=functions_path,
            output_path=output_path,
            ai_mode=mode,
            system_prompt=system_prompt,
            embed_server=embed_server,
            embed_model=embed_model,
            top_k_filter=top_k,
            classification_batch_size=batch_size,
            max_retries=retries,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            concurrent_requests=concurrent,
            api_key=api_key,
            local_servers=local_servers if mode == "Локальная" else [],
        )

    @staticmethod
    def _require_positive(value: int, label: str) -> int:
        val = int(value)
        if val <= 0:
            raise ValueError(f"{label} должно быть > 0.")
        return val

    @staticmethod
    def _require_temperature(value: float) -> float:
        val = float(value)
        if not (0.0 <= val <= 2.0):
            raise ValueError("Температура должна быть в диапазоне 0.0-2.0.")
        return val


def launch() -> None:
    root = tk.Tk()
    app = SphereClassifierApp(root)
    root.mainloop()


__all__ = ["SphereClassifierApp", "launch"]
