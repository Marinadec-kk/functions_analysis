import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import queue
import threading
import os
from typing import Dict, Any, Optional, Callable


class GUIApp:
    def __init__(
        self,
        master: tk.Tk,
        backend_pipeline: Any,  # Type hint for FullAnalysisPipeline
        ui_queue: queue.Queue,
        initial_config: Dict[str, Any],
    ):
        self.master = master
        self.master.title("Function Analysis Pipeline")
        self.backend_pipeline = backend_pipeline
        self.ui_queue = ui_queue
        self.current_config = initial_config

        self.worker_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._set_initial_ui_values()
        self._drain_ui_queue()  # Start polling the queue

    def _build_ui(self):
        # Notebook for tabs
        self.notebook = ttk.Notebook(self.master)
        self.notebook.pack(expand=True, fill="both", padx=10, pady=10)

        # Tabs for each stage
        self.tab_frames: Dict[str, ttk.Frame] = {}
        self.entry_widgets: Dict[str, tk.StringVar] = {}

        self._create_parsing_tab()
        self._create_types_tab()
        self._create_spheres_tab()
        self._create_group_dub_tab()
        self._create_dub_check_tab()
        self._create_md_sharding_tab()
        self._create_ai_config_tab()

        # Global controls and status
        control_frame = ttk.Frame(self.master)
        control_frame.pack(fill="x", padx=10, pady=5)

        self.start_button = ttk.Button(
            control_frame, text="Запустить конвейер", command=self._on_start
        )
        self.start_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(
            control_frame, text="Остановить", command=self._on_stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=5)

        self.status_label = ttk.Label(control_frame, text="Статус: Готов")
        self.status_label.pack(side="right", padx=5)

        self.progress_bar = ttk.Progressbar(
            self.master, orient="horizontal", mode="determinate"
        )
        self.progress_bar.pack(fill="x", padx=10, pady=5)

        # Log output
        log_frame = ttk.LabelFrame(self.master, text="Логи")
        log_frame.pack(expand=True, fill="both", padx=10, pady=5)
        self.log_text = tk.Text(log_frame, height=10, state="disabled")
        self.log_text.pack(expand=True, fill="both", padx=5, pady=5)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=log_scroll.set)

        self.log_text.tag_config("error", foreground="red")
        self.log_text.tag_config("warning", foreground="orange")
        self.log_text.tag_config("info", foreground="black")

    def _create_input_field(
        self,
        parent_frame: ttk.Frame,
        label_text: str,
        config_key: str,
        is_folder: bool = False,
        file_ext: str = "",
        default_value: str = "",
    ):
        ttk.Label(parent_frame, text=label_text).pack(padx=5, pady=5, anchor="w")
        entry_var = tk.StringVar(value=default_value)
        entry = ttk.Entry(parent_frame, width=80, textvariable=entry_var)
        entry.pack(padx=5, pady=2, fill="x")
        self.entry_widgets[config_key] = entry_var

        if is_folder:
            button_command = lambda: self._choose_directory(entry_var)
        else:
            button_command = lambda: self._choose_file(entry_var, file_ext)

        ttk.Button(parent_frame, text="Выбрать", command=button_command).pack(
            padx=5, pady=2, anchor="w"
        )

    def _create_parsing_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="1. Парсинг")
        self.tab_frames["parsing"] = frame

        self._create_input_field(
            frame,
            "Входная папка с документами:",
            "parsing_input_folder",
            is_folder=True,
            default_value=self.current_config.get("DEFAULT_INPUT_DOCS_DIR", ""),
        )
        # Note: parsing_output_file is now handled internally by backend.parsing_module.py
        # if the module needs to save intermediate files. It's not directly exposed in UI config.

    def _create_types_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="2. Классификация типов")
        self.tab_frames["types"] = frame

        # Input to this stage is output of parsing. Not explicit UI field.
        # Output of this stage is input to next.
        self._create_input_field(
            frame,
            "Выходной файл (после классификации типов):",
            "types_output_file",
            file_ext=".xlsx",
            default_value=os.path.join(
                self.current_config.get("DEFAULT_OUTPUT_DIR", ""),
                "functions_with_types.xlsx",
            ),
        )

    def _create_spheres_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="3. Классификация сфер")
        self.tab_frames["spheres"] = frame

        # Input to this stage is output of types. Not explicit UI field.
        self._create_input_field(
            frame,
            "Файл с определениями сфер (Excel/CSV):",
            "spheres_definitions_file",
            file_ext=".xlsx",
            default_value=os.path.join(
                self.current_config.get("DEFAULT_INPUT_DOCS_DIR", ""), "spheres.xlsx"
            ),
        )
        self._create_input_field(
            frame,
            "Выходной файл (после классификации сфер):",
            "spheres_output_file",
            file_ext=".xlsx",
            default_value=os.path.join(
                self.current_config.get("DEFAULT_OUTPUT_DIR", ""),
                "functions_with_spheres.xlsx",
            ),
        )

    def _create_group_dub_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="4. Группировка и дубликаты")
        self.tab_frames["group_dub"] = frame

        # Input to this stage is output of spheres. Not explicit UI field.
        self._create_input_field(
            frame,
            "Порог сходства для дубликатов (0.0-1.0):",
            "similarity_threshold",
            default_value=str(self.current_config.get("similarity_threshold", 0.8)),
        )
        self._create_input_field(
            frame,
            "Выходной файл (после группировки и кандидатов):",
            "group_dub_output_file",
            file_ext=".xlsx",
            default_value=os.path.join(
                self.current_config.get("DEFAULT_OUTPUT_DIR", ""),
                "functions_with_candidates.xlsx",
            ),
        )

    def _create_dub_check_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="5. Проверка дубликатов")
        self.tab_frames["dub_check"] = frame

        # Input to this stage is output of group_dub. Not explicit UI field.
        self._create_input_field(
            frame,
            "Выходной файл (после проверки дубликатов):",
            "duplicates_verified_output_file",
            file_ext=".xlsx",
            default_value=os.path.join(
                self.current_config.get("DEFAULT_OUTPUT_DIR", ""),
                "functions_verified_duplicates.xlsx",
            ),
        )

    def _create_md_sharding_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="6. Шардирование MD")
        self.tab_frames["md_sharding"] = frame

        # Input to this stage is output of dub_check. Not explicit UI field.
        self._create_input_field(
            frame,
            "Выходная папка для MD-отчетов:",
            "markdown_output_dir",
            is_folder=True,
            default_value=os.path.join(
                self.current_config.get("DEFAULT_OUTPUT_DIR", ""), "markdown_reports"
            ),
        )

    def _create_ai_config_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Настройки AI")
        self.tab_frames["ai_config"] = frame

        # AI API Base URL
        self._create_input_field(
            frame,
            "Базовый URL AI API:",
            "ai_api_base_url",
            default_value=self.current_config.get("ai_api_base_url", ""),
        )

        # AI API Key (sensitive, handle carefully)
        ttk.Label(frame, text="AI API Ключ:").pack(padx=5, pady=5, anchor="w")
        entry_var_key = tk.StringVar(value=self.current_config.get("ai_api_key", ""))
        entry_key = ttk.Entry(frame, width=80, textvariable=entry_var_key, show="*")
        entry_key.pack(padx=5, pady=2, fill="x")
        self.entry_widgets["ai_api_key"] = entry_var_key

        # Other AI related settings (models, temperature, max_tokens, retries, etc.)
        self._create_input_field(
            frame,
            "Модель для эмбеддингов:",
            "embedding_model_name",
            default_value=self.current_config.get(
                "embedding_model_name", "text-embedding-ada-002"
            ),
        )
        self._create_input_field(
            frame,
            "Модель для классификации типов:",
            "classification_model_name",
            default_value=self.current_config.get(
                "classification_model_name", "gpt-3.5-turbo"
            ),
        )
        self._create_input_field(
            frame,
            "Модель для LLM верификации дубликатов:",
            "llm_duplicate_verification_model_name",
            default_value=self.current_config.get(
                "llm_duplicate_verification_model_name", "gpt-3.5-turbo"
            ),
        )
        self._create_input_field(
            frame,
            "Количество повторных попыток API:",
            "api_max_retries",
            default_value=str(self.current_config.get("api_max_retries", 3)),
        )
        self._create_input_field(
            frame,
            "Таймаут API (секунды):",
            "api_timeout",
            default_value=str(self.current_config.get("api_timeout", 60)),
        )

    def _set_initial_ui_values(self):
        # This function would populate default values from self.current_config
        # It's partially covered by `default_value` in _create_input_field,
        # but could be used for more complex initializations.
        pass

    def _choose_directory(self, entry_var: tk.StringVar):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            entry_var.set(folder_selected)

    def _choose_file(self, entry_var: tk.StringVar, file_ext: str):
        file_types = []
        if file_ext == ".xlsx":
            file_types.append(("Excel files", "*.xlsx"))
            file_types.append(
                ("CSV files", "*.csv")
            )  # Allow CSV as well for Excel fields
        elif file_ext == ".json":
            file_types.append(("JSON files", "*.json"))
        else:
            file_types.append(("All files", "*.*"))

        file_selected = filedialog.askopenfilename(filetypes=file_types)
        if file_selected:
            entry_var.set(file_selected)

    def _on_start(self):
        if (
            self.backend_pipeline.worker_thread
            and self.backend_pipeline.worker_thread.is_alive()
        ):
            self.log("Конвейер уже запущен.", level="warning")
            return

        # Disable start button, enable stop button
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.progress_bar.config(value=0)
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")
        self.set_status("Запуск конвейера...")

        # Gather all current configurations from UI entry widgets
        ui_driven_config: Dict[str, Any] = {}
        for key, var in self.entry_widgets.items():
            value = var.get()
            # Attempt to convert to int/float if applicable
            if value.isdigit():
                ui_driven_config[key] = int(value)
            elif value.replace(".", "", 1).isdigit():  # Check for float
                ui_driven_config[key] = float(value)
            else:
                ui_driven_config[key] = value

        # Merge with initial_config, UI values take precedence
        final_config_for_pipeline = self.current_config.copy()
        final_config_for_pipeline.update(ui_driven_config)

        # Call backend to start analysis. It will run in its own thread.
        self.backend_pipeline.start_analysis(final_config_for_pipeline)

    def _on_stop(self):
        self.log("Пользователь запросил остановку конвейера.")
        self.backend_pipeline.stop_analysis()
        self.stop_button.config(state="disabled")  # Disable immediately

    def _drain_ui_queue(self):
        """Processes messages from the UI queue to update the UI."""
        while True:
            try:
                message = self.ui_queue.get_nowait()
                message_type = message.get("type")

                if message_type == "log":
                    self.log(
                        message.get("message", ""), level=message.get("level", "info")
                    )
                elif message_type == "status":
                    self.set_status(
                        message.get("message", ""), level=message.get("level", "info")
                    )
                elif message_type == "progress":
                    self.set_progress(
                        message.get("current", 0),
                        message.get("total", 0),
                        message.get("stage", ""),
                    )
                else:
                    self.log(
                        f"Неизвестный тип сообщения из UI очереди: {message_type}",
                        level="warning",
                    )

            except queue.Empty:
                break
            except Exception as e:
                self.log(
                    f"Ошибка при обработке сообщения из UI очереди: {e}", level="error"
                )

        # Schedule itself to run again after 100 milliseconds
        self.master.after(100, self._drain_ui_queue)

    # --- UI Update Methods (called via callbacks from _drain_ui_queue) ---

    def log(self, message: str, level: str = "info"):
        """Appends a message to the log text widget."""
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, f"{message}\\n", level)
        self.log_text.config(state="disabled")
        self.log_text.see(tk.END)  # Scroll to end

    def set_status(self, status_text: str, level: str = "info"):
        """Sets the status label text."""
        color = "black"
        if level == "error":
            color = "red"
        elif level == "warning":
            color = "orange"
        self.status_label.config(text=f"Статус: {status_text}", foreground=color)

    def set_progress(self, current: int, total: int, stage: str = ""):
        """Updates the progress bar."""
        if total > 0:
            value = (current / total) * 100
            self.progress_bar.config(value=value)
            # self.log(f"Прогресс: {stage} - {current}/{total} ({value:.1f}%)", level="debug")
        else:
            self.progress_bar.config(value=0)

    def on_pipeline_finished(
        self, success: Optional[bool], error: Optional[str] = None
    ):
        """Callback from backend when pipeline finishes."""
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.progress_bar.config(value=100)  # Always set to 100 on finish

        if success is True:
            self.set_status("Конвейер завершен успешно!", level="info")
            self.log("Все этапы конвейера успешно выполнены.", level="info")
            messagebox.showinfo("Готово", "Конвейер анализа функций завершен успешно!")
        elif success is False:
            self.set_status(f"Конвейер завершен с ошибками: {error}", level="error")
            self.log(f"Конвейер завершен с ошибками: {error}", level="error")
            messagebox.showerror("Ошибка", f"Конвейер завершен с ошибками:\\n{error}")
        else:  # success is None, means it was stopped
            self.set_status("Конвейер остановлен пользователем.", level="warning")
            self.log("Конвейер был остановлен пользователем.", level="warning")
            messagebox.showwarning(
                "Остановлено", "Конвейер анализа функций был остановлен."
            )
