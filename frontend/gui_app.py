import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional

from frontend.tabs.ai_config_tab import create_ai_config_tab
from frontend.tabs.verify_duplicates_tab import create_verify_duplicates_tab
from frontend.tabs.group_dub_tab import create_group_dub_tab
from frontend.tabs.hierarchy_tab import create_hierarchy_tab
from frontend.tabs.md_sharding_tab import create_md_sharding_tab
from frontend.tabs.parsing_tab import create_parsing_tab
from frontend.tabs.spheres_tab import create_spheres_tab
from frontend.tabs.types_tab import create_types_tab


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
        self._drain_ui_queue()  # Start polling the queue

    def _build_ui(self):
        # Notebook for tabs
        self.notebook = ttk.Notebook(self.master)
        self.notebook.pack(expand=True, fill="both", padx=10, pady=10)

        # Tabs for each stage
        self.tab_frames: Dict[str, ttk.Frame] = {}
        self.entry_widgets: Dict[str, tk.StringVar] = {}

        self.tab_frames["parsing"] = create_parsing_tab(
            self.notebook, self.entry_widgets, self.current_config
        )
        self.tab_frames["types"] = create_types_tab(
            self.notebook, self.entry_widgets, self.current_config
        )
        self.tab_frames["spheres"] = create_spheres_tab(
            self.notebook, self.entry_widgets, self.current_config
        )
        self.tab_frames["group_dub"] = create_group_dub_tab(
            self.notebook, self.entry_widgets, self.current_config
        )
        self.tab_frames["verify_duplicates"] = create_verify_duplicates_tab(
            self.notebook, self.entry_widgets, self.current_config
        )
        self.tab_frames["hierarchy_analysis"] = create_hierarchy_tab(
            self.notebook, self.entry_widgets, self.current_config
        )
        self.tab_frames["md_sharding"] = create_md_sharding_tab(
            self.notebook, self.entry_widgets, self.current_config
        )
        self.tab_frames["ai_config"] = create_ai_config_tab(
            self.notebook, self.entry_widgets, self.current_config
        )

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

        self.continue_button = ttk.Button(
            control_frame,
            text="Продолжить",
            command=self._on_continue,
            state="disabled",
        )
        self.continue_button.pack(side="left", padx=5)

        self.auto_advance_var = tk.BooleanVar(value=True)  # Default to auto-advance
        self.auto_advance_check = ttk.Checkbutton(
            control_frame,
            text="Автоматический переход к следующему этапу",
            variable=self.auto_advance_var,
        )
        self.auto_advance_check.pack(side="left", padx=15)

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

        # Add the auto-advance setting
        ui_driven_config["auto_advance_stages"] = self.auto_advance_var.get()

        # Merge with initial_config, UI values take precedence
        final_config_for_pipeline = self.current_config.copy()
        final_config_for_pipeline.update(ui_driven_config)

        # Get the current tab to determine the start stage
        current_tab_index = self.notebook.index(self.notebook.select())
        start_stage_id = list(self.tab_frames.keys())[current_tab_index]

        # Get the input file if it exists in the config
        input_file = final_config_for_pipeline.get("input_file")

        # Call backend to start analysis. It will run in its own thread.
        self.backend_pipeline.start_analysis(
            final_config_for_pipeline, input_file, start_stage_id
        )

    def _on_stop(self):
        self.log("Пользователь запросил остановку конвейера.")
        self.backend_pipeline.stop_analysis()
        self.stop_button.config(state="disabled")  # Disable immediately
        self.continue_button.config(state="disabled")  # Disable continue button as well

    def _on_continue(self):
        self.log("Пользователь запросил продолжение конвейера.")
        self.continue_button.config(state="disabled")
        self.backend_pipeline.continue_pipeline()

    def _drain_ui_queue(self):
        """Processes messages from the UI queue to update the UI."""
        while True:
            try:
                message = self.ui_queue.get_nowait()
                message_type = message.get("type")

                if message_type == "status":
                    self.set_status(
                        message.get("message", ""), level=message.get("level", "info")
                    )
                elif message_type == "progress":
                    self.set_progress(
                        message.get("current", 0),
                        message.get("total", 0),
                        message.get("stage", ""),
                    )
                elif message_type == "waiting_for_manual_advance":  # New message type
                    self.set_status(
                        f"Ожидание ручного перехода после этапа: {message.get('stage_name', 'Неизвестный этап')}"
                    )
                    self.continue_button.config(state="normal")
                    self.start_button.config(
                        state="disabled"  # Ensure start button is disabled
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
        self.log_text.insert(tk.END, f"{message}\n", level)
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
        self,
        success: Optional[bool],
        error: Optional[str] = None,
    ):
        """Callback from backend when pipeline finishes."""
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.continue_button.config(
            state="disabled"  # Disable continue button on finish
        )
        self.progress_bar.config(value=100)  # Always set to 100 on finish

        if success is True:
            self.set_status("Конвейер завершен успешно!", level="info")
            self.log("Все этапы конвейера успешно выполнены.", level="info")
            messagebox.showinfo("Готово", "Конвейер анализа функций завершен успешно!")
        elif success is False:
            self.set_status(f"Конвейер завершен с ошибками: {error}", level="error")
            self.log(f"Конвейер завершен с ошибками: {error}", level="error")
            messagebox.showerror("Ошибка", f"Конвейер завершен с ошибками:\n{error}")
        else:  # success is None, means it was stopped
            self.set_status("Конвейер остановлен пользователем.", level="warning")
            self.log("Конвейер был остановлен пользователем.", level="warning")
            messagebox.showwarning(
                "Остановлено", "Конвейер анализа функций был остановлен."
            )
