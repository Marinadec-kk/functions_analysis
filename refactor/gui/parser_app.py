from __future__ import annotations

import os
import threading
import time
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox

try:
    import sv_ttk  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    sv_ttk = None

from ..core.analysis.parsing import ParsingAnalyzer, ParsingConfig, ParsingResult
from ..core.ai.client import prepare_api_base_url
from ..core.file_io import write_dataframe
from ..gui.app_base import AppBase
from ..gui import widgets


def log_to_terminal(message: str) -> None:
    """Replicate legacy terminal logging with timestamps."""
    timestamp = time.strftime("%H:%M:%S")
    thread_id = threading.get_ident()
    print(f"[{timestamp}][Thread-{thread_id}] {message}")


class ParserApp(AppBase):
    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root.title("Парсер функций ГО v6.0 (AI Extraction + Python Parsing)")
        self.root.geometry("1000x700")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.progress_bar = None
        self.start_btn = None
        self.stop_btn = None

        self.input_folder_var = tk.StringVar()
        self.ai_mode_var = tk.StringVar(value="Онлайн")
        self.timeout_var = tk.StringVar(value="60.0")
        self.workers_var = tk.StringVar(value="8")

        self.api_key_var = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.online_model_var = tk.StringVar(value="gpt-4o-mini")

        self.local_model_var = tk.StringVar(value="local-model")
        self.local_url_text = None  # type: Optional[tk.Text]

        self.ap_url_var = tk.StringVar(value="https://llm.govplan.kz")
        self.ap_key_var = tk.StringVar(value="sk-...")
        self.ap_model_var = tk.StringVar(value="openai/gpt-oss-120b")

        self.online_frame: Optional[ttk.Frame] = None
        self.local_frame: Optional[ttk.Frame] = None
        self.ap_frame: Optional[ttk.Frame] = None

        self._current_result: Optional[ParsingResult] = None
        self._last_config: Optional[ParsingConfig] = None

        self.build_ui()

    # ------------------------------------------------------------------ UI base
    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(main, width=400)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        right_panel = ttk.Frame(main)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        files_frame = ttk.LabelFrame(left_panel, text="Этап 1: Выбор папки")
        files_frame.pack(fill=tk.X, pady=5)
        widgets.create_file_row(
            files_frame,
            "Папка с документами:",
            self.input_folder_var,
            self._choose_input_folder,
        )

        ai_frame = ttk.LabelFrame(left_panel, text="Этап 2: Настройки ИИ")
        ai_frame.pack(fill=tk.X, pady=5)
        self._create_ai_mode_selector(ai_frame)
        self._create_ai_config_frames(ai_frame)
        self._on_ai_mode_change()

        perf_frame = ttk.LabelFrame(
            left_panel, text="Этап 3: Настройки производительности"
        )
        perf_frame.pack(fill=tk.X, pady=5)
        widgets.create_entry_row(
            perf_frame, "Количество воркеров:", self.workers_var
        )

        controls_frame = ttk.LabelFrame(right_panel, text="Управление")
        controls_frame.pack(fill=tk.X, pady=5)
        self.start_btn = ttk.Button(
            controls_frame,
            text="Старт анализа",
            command=self._on_start,
            style="Accent.TButton",
        )
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5, padx=5, pady=5)
        self.stop_btn = ttk.Button(
            controls_frame, text="Стоп", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5, padx=5, pady=5)

        progress_frame = ttk.LabelFrame(right_panel, text="Прогресс")
        progress_frame.pack(fill=tk.X, pady=5, expand=True)
        self.progress_bar = ttk.Progressbar(progress_frame)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=10)

    # ------------------------------------------------------------------ helpers
    def _create_ai_mode_selector(self, parent: ttk.Frame) -> None:
        mode_frame = ttk.Frame(parent)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        for text in ("Онлайн", "Локальный", "АП"):
            ttk.Radiobutton(
                mode_frame,
                text=text,
                variable=self.ai_mode_var,
                value=text,
                command=self._on_ai_mode_change,
            ).pack(side=tk.LEFT, padx=(0, 10))

    def _create_ai_config_frames(self, parent: ttk.Frame) -> None:
        self.online_frame = ttk.Frame(parent)
        widgets.create_entry_row(
            self.online_frame, "Ключ API:", self.api_key_var, show="*"
        )
        widgets.create_entry_row(
            self.online_frame, "Модель:", self.online_model_var
        )
        widgets.create_entry_row(
            self.online_frame, "Таймаут (сек):", self.timeout_var
        )

        self.local_frame = ttk.Frame(parent)
        row = ttk.Frame(self.local_frame)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text="URL серверов (1 на строку):", width=25).pack(
            side=tk.LEFT, anchor="n"
        )
        self.local_url_text = tk.Text(row, height=4, width=30)
        self.local_url_text.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.local_url_text.insert(tk.END, "http://localhost:1234")
        widgets.create_entry_row(
            self.local_frame, "Модель:", self.local_model_var
        )
        widgets.create_entry_row(
            self.local_frame, "Таймаут (сек):", self.timeout_var
        )

        self.ap_frame = ttk.Frame(parent)
        widgets.create_entry_row(
            self.ap_frame, "URL сервера:", self.ap_url_var
        )
        widgets.create_entry_row(
            self.ap_frame, "Ключ API:", self.ap_key_var, show="*"
        )
        widgets.create_entry_row(
            self.ap_frame, "Модель:", self.ap_model_var
        )
        widgets.create_entry_row(
            self.ap_frame, "Таймаут (сек):", self.timeout_var
        )

    # ----------------------------------------------------------------- callbacks
    def handle_ui_event(self, event: str, payload) -> None:
        if event == "progress_reset":
            maximum = payload or 1
            self.progress_bar["maximum"] = maximum
            self.progress_bar["value"] = 0
        elif event == "progress_update":
            self.progress_bar.step(payload or 1)
        elif event == "analysis_success":
            self._on_analysis_success(payload)
        elif event == "analysis_error":
            self._on_analysis_error(payload)
        elif event == "analysis_cancelled":
            self._on_analysis_cancelled()
        elif event == "log":
            log_to_terminal(str(payload))

    def _choose_input_folder(self) -> None:
        directory = widgets.ask_directory(
            "Выберите корневую папку с документами"
        )
        if directory:
            self.input_folder_var.set(directory)

    def _on_ai_mode_change(self) -> None:
        for frame in (self.online_frame, self.local_frame, self.ap_frame):
            if frame:
                frame.pack_forget()
        mode = self.ai_mode_var.get()
        frame_map = {
            "Онлайн": self.online_frame,
            "Локальный": self.local_frame,
            "АП": self.ap_frame,
        }
        frame = frame_map.get(mode)
        if frame:
            frame.pack(fill=tk.X, padx=5, pady=2)

    def _on_start(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("Выполняется", "Анализ уже запущен.")
            return
        try:
            config = self._get_config()
        except ValueError as exc:
            messagebox.showerror("Ошибка в настройках", str(exc))
            return

        try:
            total_files = self._count_word_files(config.input_folder)
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc))
            return

        if total_files == 0:
            messagebox.showwarning(
                "Нет файлов", "В указанной папке нет документов .doc/.docx."
            )
            return

        self.post_ui_event("progress_reset", total_files)
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._last_config = config
        log_to_terminal("=" * 50)
        log_to_terminal("ЗАПУСК АНАЛИЗА ДОКУМЕНТОВ")
        self.run_in_thread(self._run_analysis, config)

    def _run_analysis(self, config: ParsingConfig) -> None:
        analyzer = ParsingAnalyzer(
            logger=lambda msg: self.post_ui_event("log", msg),
            progress_callback=lambda delta: self.post_ui_event("progress_update", delta),
        )
        try:
            result = analyzer.analyze(config=config, stop_event=self.stop_event)
            self.post_ui_event("analysis_success", result)
        except InterruptedError:
            self.post_ui_event("analysis_cancelled", None)
        except Exception as exc:
            self.post_ui_event("analysis_error", exc)

    def _on_stop(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            log_to_terminal("Запрошена остановка... Завершаю текущий файл.")
            self.stop_worker()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_analysis_success(self, result: ParsingResult) -> None:
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._current_result = result
        log_to_terminal(result.report)
        log_to_terminal("=" * 56)
        self._prompt_save_result(result)
        messagebox.showinfo("Анализ завершен!", result.report)

    def _prompt_save_result(self, result: ParsingResult) -> None:
        if not result.rows:
            log_to_terminal("Нет данных для сохранения. Отчет не создан.")
            messagebox.showwarning("Нет данных", "Не удалось извлечь ни одной функции.")
            return

        folder_name = "отчет"
        if self._last_config:
            folder_name = os.path.basename(
                self._last_config.input_folder.rstrip("/\\")
            ) or "отчет"
        elif result.documents:
            folder_name = os.path.basename(result.documents[0].filepath)
        suggested = f"Итоговая матрица функций — {folder_name}.xlsx"
        output_file = widgets.ask_save_file(
            "Сохранить итоговый Excel как…",
            defaultextension=".xlsx",
            initialfile=suggested,
            filetypes=[("Excel", "*.xlsx")],
        )
        if not output_file:
            log_to_terminal("Сохранение отменено.")
            return
        try:
            import pandas as pd

            df = pd.DataFrame(result.rows)
            df = df.reindex(columns=["ID", "Госорган", "Вышестоящий", "Текст_функции"])
            write_dataframe(df, output_file, index=False)
            log_to_terminal(f"🎉 Отчет сохранен: {output_file}")
        except Exception as exc:
            log_to_terminal(f"❌ Ошибка при сохранении отчета: {exc}")
            messagebox.showerror(
                "Ошибка сохранения",
                f"Не удалось сохранить Excel-файл.\n\nОшибка: {exc}",
            )

    def _on_analysis_error(self, exc: Exception) -> None:
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        messagebox.showerror("Критическая ошибка", f"Произошла ошибка:\n{exc}")
        log_to_terminal(f"Ошибка: {exc}")

    def _on_analysis_cancelled(self) -> None:
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        log_to_terminal("Процесс остановлен пользователем.")

    # ---------------------------------------------------------------- utilities
    def _get_config(self) -> ParsingConfig:
        input_folder = self.input_folder_var.get().strip()
        if not input_folder or not os.path.isdir(input_folder):
            raise ValueError("Укажите корректную папку с документами.")

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

        mode = self.ai_mode_var.get()
        config = ParsingConfig(
            input_folder=input_folder,
            ai_mode=mode,
            timeout=timeout,
            num_workers=num_workers,
            model="gpt-4o-mini",
        )

        if mode == "Онлайн":
            api_key = self.api_key_var.get().strip()
            if not api_key:
                raise ValueError("В режиме 'Онлайн' нужен ключ API.")
            config.api_key = api_key
            config.model = self.online_model_var.get().strip()
            config.base_url = "https://api.openai.com/v1"
        elif mode == "Локальный":
            text_widget = self.local_url_text
            if text_widget is None:
                raise ValueError("Введите URL локальных серверов.")
            urls_text = text_widget.get("1.0", tk.END).strip()
            raw_urls = [url.strip() for url in urls_text.splitlines() if url.strip()]
            if not raw_urls:
                raise ValueError("В режиме 'Локальный' укажите хотя бы один URL сервера.")
            config.base_urls = [prepare_api_base_url(url) for url in raw_urls]
            config.model = self.local_model_var.get().strip()
            config.api_key = "not-needed"
        elif mode == "АП":
            config.base_url = prepare_api_base_url(self.ap_url_var.get().strip())
            config.api_key = self.ap_key_var.get().strip()
            config.model = self.ap_model_var.get().strip()
        else:
            raise ValueError(f"Неизвестный режим: {mode}")
        return config

    @staticmethod
    def _count_word_files(folder: str) -> int:
        docx_files = 0
        doc_files = 0
        for root_dir, _, files in os.walk(folder):
            for name in files:
                if name.startswith(("~$", "~", ".")):
                    continue
                low = name.lower()
                if low.endswith(".docx"):
                    docx_files += 1
                elif low.endswith(".doc"):
                    doc_files += 1
        return docx_files + doc_files


def launch() -> None:
    root = tk.Tk()
    app = ParserApp(root)
    root.mainloop()


__all__ = ["ParserApp", "launch"]
