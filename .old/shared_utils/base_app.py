# shared_utils/base_app.py
import tkinter as tk
from tkinter import ttk, messagebox
import queue
import threading
import time
import os

try:
    import sv_ttk
except ImportError:
    sv_ttk = None


class BaseAppTemplate:
    """Базовый шаблон приложения с общей логикой UI и управления потоками."""

    def __init__(self, root: tk.Tk, title: str, geometry: str = "1100x850"):
        self.root = root
        self.root.title(title)
        self.root.geometry(geometry)
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)

        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)

        self._worker_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

        self.config_vars = self._create_config_vars()
        self._build_base_ui()
        self._build_custom_ui()

    def _create_config_vars(self) -> dict:
        """Создает tk.Variables для всех настроек. Переопределяется в дочерних классах."""
        return {
            # Files
            "input_file": tk.StringVar(),
            "output_dir": tk.StringVar(),
            # AI Mode
            "ai_mode": tk.StringVar(value="Локальная"),
            "openai_api_key": tk.StringVar(value=os.getenv("OPENAI_API_KEY", "")),
            "online_model": tk.StringVar(value="gpt-4o-mini"),
            "local_model": tk.StringVar(value="local-model/gguf"),
            "local_servers_text": tk.StringVar(value="http://localhost:1234\n"),
            # Common AI
            "ai_temperature": tk.DoubleVar(value=0.1),
            "use_json_mode": tk.BooleanVar(value=True),
            # Advanced
            "ai_workers": tk.IntVar(value=10),
            "embedding_workers": tk.IntVar(value=4),
            "workers_per_local_server": tk.IntVar(value=4),
            "batch_size": tk.IntVar(value=32),
            "api_timeout": tk.DoubleVar(value=30.0),
            "api_retries": tk.IntVar(value=3),
            "api_delay": tk.DoubleVar(value=2.0),
        }

    def _build_base_ui(self):
        """Строит базовый каркас UI, общий для всех приложений."""
        self.main_frame = ttk.Frame(self.root, padding=15)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.left_panel = ttk.Frame(self.main_frame, width=450)
        self.left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15), anchor="n")

        self.right_panel = ttk.Frame(self.main_frame)
        self.right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- Правая панель (общая) ---
        controls_frame = ttk.LabelFrame(self.right_panel, text="Управление")
        controls_frame.pack(fill=tk.X, pady=5)
        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)
        self.start_btn = ttk.Button(
            btn_row, text="Старт", command=self._on_start, style="Accent.TButton"
        )
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)
        self.stop_btn = ttk.Button(
            btn_row, text="Стоп", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        self.progress_frame = ttk.LabelFrame(
            self.right_panel, text="Прогресс выполнения"
        )
        self.progress_frame.pack(fill=tk.X, pady=5)

        log_frame = ttk.LabelFrame(self.right_panel, text="Логи")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = tk.Text(log_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        status_frame = ttk.Frame(self.right_panel)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))
        self.status_label = ttk.Label(status_frame, text="Готово")
        self.status_label.pack(side=tk.LEFT)
        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)

    def _build_custom_ui(self):
        """Метод для создания кастомных виджетов в дочерних классах."""
        raise NotImplementedError(
            "Этот метод должен быть переопределен в дочернем классе"
        )

    def _get_config(self) -> dict:
        """Собирает конфигурацию из tk.Variables. Может быть дополнен в дочерних классах."""
        config = {key: var.get() for key, var in self.config_vars.items()}
        # Дополнительная обработка, например, для списка серверов
        config["local_servers"] = [
            s.strip() for s in config["local_servers_text"].splitlines() if s.strip()
        ]
        return config

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showinfo("Выполняется", "Обработка уже запущена.")
            return

        try:
            config = self._get_config()
            # Здесь может быть дополнительная валидация конфига
        except Exception as e:
            messagebox.showerror("Ошибка в настройках", str(e))
            return

        self._stop_flag.clear()
        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")
        self.log("=" * 50)
        self.log(f"ЗАПУСК: {self.root.title()}")

        self._worker_thread = threading.Thread(
            target=self._worker_main, args=(config,), daemon=True
        )
        self._worker_thread.start()

    def _on_stop(self):
        if self._worker_thread and self._worker_thread.is_alive():
            self.log("Запрошена остановка... Завершаю текущие операции.")
            self._stop_flag.set()
            self.stop_btn.config(state=tk.DISABLED)

    def _on_worker_finished(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._timer_running = False
        final_message = (
            "Готово."
            if not self._stop_flag.is_set()
            else "Процесс остановлен пользователем."
        )
        self.set_status(final_message)
        self.log(final_message.upper())
        self.log("=" * 50)

    def _worker_main(self, config: dict):
        """Основная логика, выполняемая в потоке. Переопределяется в дочерних классах."""
        try:
            raise NotImplementedError(
                "Этот метод должен быть переопределен в дочернем классе"
            )
        except Exception as e:
            import traceback

            self.log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
            self.log(f"Traceback: {traceback.format_exc()}")
            self.set_status(f"Ошибка: {e}")
        finally:
            self.ui_queue.put(("worker_done", None))

    # --- Методы для взаимодействия с UI из потока ---
    def log(self, message: str):
        self.ui_queue.put(("log", message))

    def set_status(self, text: str):
        self.ui_queue.put(("status", text))

    def _drain_ui_queue(self):
        try:
            while True:
                command, value = self.ui_queue.get_nowait()
                if command == "status":
                    self.status_label.config(text=str(value)[:200])
                elif command == "log":
                    self.log_text.config(state="normal")
                    self.log_text.insert(
                        tk.END, f"[{time.strftime('%H:%M:%S')}] {value}\n"
                    )
                    self.log_text.config(state="disabled")
                    self.log_text.see(tk.END)
                elif command == "worker_done":
                    self._on_worker_finished()
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def _tick_ui(self):
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(
                text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}"
            )
        self.root.after(500, self._tick_ui)
