# shared_utils/gui_components.py
from typing import List, Dict, Optional
import tkinter as tk
from tkinter import ttk, filedialog


class FileSelectionWidget(ttk.Frame):
    """Виджет для выбора файла или папки."""

    def __init__(
        self, parent, label_text: str, var: tk.StringVar, is_dir: bool = False
    ):
        super().__init__(parent)
        self.var = var
        self.is_dir = is_dir

        ttk.Label(self, text=label_text, width=22).pack(side=tk.LEFT)
        ttk.Entry(self, textvariable=self.var).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )
        ttk.Button(self, text="...", command=self._choose, width=4).pack(side=tk.LEFT)

    def _choose(self):
        if self.is_dir:
            path = filedialog.askdirectory()
        else:
            path = filedialog.askopenfilename(
                filetypes=[
                    ("Excel/CSV", "*.xlsx *.xls *.csv"),
                    ("JSON", "*.json"),
                    ("Все файлы", "*.*"),
                ]
            )
        if path:
            self.var.set(path)


class AdvancedSettingsFrame(ttk.LabelFrame):
    """Панель с расширенными настройками производительности и сети."""

    def __init__(self, parent, config_vars: dict):
        super().__init__(parent, text="Расширенные настройки")
        self.config_vars = config_vars

        self._create_entry("Кол-во воркеров (AI):", self.config_vars["ai_workers"])
        self._create_entry(
            "Кол-во воркеров (Embed):", self.config_vars["embedding_workers"]
        )
        self._create_entry(
            "Воркеров на лок. сервер:", self.config_vars["workers_per_local_server"]
        )
        self._create_entry("Размер пакета (batch):", self.config_vars["batch_size"])
        self._create_entry("Таймаут API (сек):", self.config_vars["api_timeout"])
        self._create_entry("Попыток при ошибке API:", self.config_vars["api_retries"])
        self._create_entry("Задержка при ошибке (сек):", self.config_vars["api_delay"])

    def _create_entry(self, label, var):
        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=22).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )


class AISettingsFrame(ttk.LabelFrame):
    """Унифицированный компонент для всех настроек AI."""

    def __init__(self, parent, config_vars: dict):
        super().__init__(parent, text="Настройки AI")
        self.config_vars = config_vars

        # --- Переключатель режима ---
        mode_frame = ttk.Frame(self)
        mode_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(
            mode_frame,
            text="Онлайн",
            variable=self.config_vars["ai_mode"],
            value="Онлайн",
            command=self._on_mode_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_frame,
            text="Локальная",
            variable=self.config_vars["ai_mode"],
            value="Локальная",
            command=self._on_mode_change,
        ).pack(side=tk.LEFT, padx=10)

        # --- Панель Онлайн ---
        self.online_frame = ttk.Frame(self)
        self._create_entry(
            self.online_frame,
            "OpenAI API Key:",
            self.config_vars["openai_api_key"],
            show="*",
        )
        self._create_entry(
            self.online_frame, "Модель (онлайн):", self.config_vars["online_model"]
        )

        # --- Панель Локальная ---
        self.local_frame = ttk.Frame(self)
        self._create_entry(
            self.local_frame, "Модель (локально):", self.config_vars["local_model"]
        )
        ttk.Label(
            self.local_frame, text="Адреса серверов (каждый с новой строки):"
        ).pack(anchor="w", pady=(5, 0), padx=5)
        self.local_servers_text = tk.Text(self.local_frame, height=3)
        self.local_servers_text.pack(fill=tk.X, expand=True, pady=2, padx=5)
        self.local_servers_text.insert(
            tk.END, self.config_vars["local_servers_text"].get()
        )
        self.local_servers_text.bind("<KeyRelease>", self._update_local_servers_var)

        # --- Общие настройки модели ---
        common_frame = ttk.Frame(self)
        common_frame.pack(fill=tk.X, pady=5)
        self._create_entry(
            common_frame, "Температура (0.0-2.0):", self.config_vars["ai_temperature"]
        )
        ttk.Checkbutton(
            common_frame,
            text="JSON режим (для Онлайн)",
            variable=self.config_vars["use_json_mode"],
        ).pack(anchor="w", padx=5, pady=3)

        self._on_mode_change()

    def _on_mode_change(self):
        if self.config_vars["ai_mode"].get() == "Онлайн":
            self.local_frame.pack_forget()
            self.online_frame.pack(fill=tk.X, padx=5, pady=2)
        else:
            self.online_frame.pack_forget()
            self.local_frame.pack(fill=tk.X, padx=5, pady=2)

    def _create_entry(self, parent, label, var, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3, padx=5)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var, **kwargs).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=5
        )

    def _update_local_servers_var(self, event):
        """Обновляет переменную StringVar при изменении текста в Text виджете."""
        self.config_vars["local_servers_text"].set(
            self.local_servers_text.get("1.0", tk.END)
        )


class ProgressPanel(ttk.Frame):
    """Панель для динамического отображения одного или нескольких прогресс-баров."""

    def __init__(self, parent, stage_names: List[str]):
        super().__init__(parent)
        self.progress_bars = {}
        self.etr_labels = {}
        self.stage_progress = {}
        self.stage_start_times = {}

        for name in stage_names:
            frame = ttk.Frame(self)
            frame.pack(fill=tk.X, padx=5, pady=(8, 0))
            ttk.Label(frame, text=f"{name}:").pack(side=tk.LEFT)
            self.etr_labels[name] = ttk.Label(frame, text="ETR: --:--")
            self.etr_labels[name].pack(side=tk.RIGHT)
            self.progress_bars[name] = ttk.Progressbar(self)
            self.progress_bars[name].pack(fill=tk.X, padx=5, pady=(2, 5))

    def reset(self, stage_name: str, total: int):
        self.progress_bars[stage_name]["maximum"] = max(1, total)
        self.progress_bars[stage_name]["value"] = 0
        self.stage_progress[stage_name] = 0
        self.stage_start_times[stage_name] = time.time()
        self.etr_labels[stage_name].config(text="ETR: вычисление...")

    def step(self, stage_name: str, increment: int = 1):
        if stage_name not in self.progress_bars:
            return

        self.stage_progress[stage_name] = (
            self.stage_progress.get(stage_name, 0) + increment
        )
        current = self.stage_progress[stage_name]
        total = self.progress_bars[stage_name]["maximum"]
        self.progress_bars[stage_name]["value"] = current

        if current >= total:
            self.etr_labels[stage_name].config(text="ETR: завершено")
        elif current > 2 and stage_name in self.stage_start_times:
            elapsed = time.time() - self.stage_start_times[stage_name]
            ips = current / elapsed
            remaining = total - current
            if ips > 0:
                etr_sec = int(remaining / ips)
                self.etr_labels[stage_name].config(
                    text=f"ETR: {time.strftime('%M:%S', time.gmtime(etr_sec))}"
                )
