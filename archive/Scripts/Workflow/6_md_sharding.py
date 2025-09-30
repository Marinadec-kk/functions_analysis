# -*- coding: utf-8 -*-
"""
MD Sharding v3.8 with GUI
================================================================
Автор: Gemini (на основе запросов пользователя Кастер)
Версия: 3.8 (20.08.2025)

Что нового:
- В файлы функций (уровни 1-3) теперь добавляется информация о типах и сферах
  деятельности (Sphere_1, Sphere_2, Sphere_3) в виде тегов.
- В файлах коллизий уровней 4-8 убраны вики-ссылки с ID функций (теперь ID отображается простым текстом).
- Переработана логика обработки уровней:
  - Функции уровней 4-8 теперь не создают отдельные файлы и их текст встраивается напрямую в портреты исполнителей.
  - Уровень 3 теперь обрабатывается аналогично уровням 1 и 2 (для его функций создаются отдельные файлы).
- Обновлена карта уровней (LEVEL_TO_TAG_MAP) в соответствии с новой структурой (до 8 уровня).
- [УБРАНО] Правило: для портретов исполнителей 5-8 уровней к "Вышестоящему ГО"
  больше не добавляется текст "Агентства по делам государственной службы".
- Секция статистики в "Портретах исполнителей" теперь генерируется в виде таблиц
  (аналогично старому скрипту md_sharding.py) для наглядного отображения долей.
- [ДОБАВЛЕНО] В "Портретах исполнителей" в списке функций теперь отображается
  тип и сфера деятельности 2-го уровня для каждой функции.
"""

import os
import re
import time
import queue
import threading
from pathlib import Path
from collections import Counter, deque, defaultdict

import pandas as pd

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- ЗАВИСИСИМОСТИ ---
try:
    import sv_ttk
except ImportError:
    print("="*80)
    print("ПРЕДУПРЕЖДЕНИЕ: Не найдена тема оформления 'sv-ttk'.")
    print("Интерфейс будет стандартным. Для улучшения вида установите тему: pip install sv-ttk")
    print("="*80)
    sv_ttk = None

# =============================== Глобальные константы ===============================
LEVEL_TO_TAG_MAP = {
    '1': '#Центральные_аппараты',
    '2': '#Комитеты_и_департаменты',
    '3': '#Управления',
    '4': '#Руководители_ЦА',
    '5': '#Директоры',
    '6': '#Руководители_отделов',
    '7': '#Главные_специалисты',
    '8': '#Специалисты'
}

# =============================== Утилиты (без изменений) ===============================

def sanitize_filename(name):
    """
    Очищает строку, чтобы она была безопасным именем файла или папки.
    """
    invalid_chars = r'<>:"/\\|?*'
    for ch in invalid_chars:
        name = name.replace(ch, '')
    return name.strip()

def create_clean_tag(text):
    """
    Очищает текст для создания тега, заменяя пробелы
    и большинство знаков препинания на '_'.
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    s = re.sub(r'[()\.,;\-\s/]+', '_', text)
    s = re.sub(r'__+', '_', s)
    s = s.strip('_')
    return s

# =============================== Основной класс приложения ===============================

class MarkdownShardingApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MD Sharding GUI v3.8")
        self.root.geometry("900x700")
        if sv_ttk:
            sv_ttk.set_theme("light")

        self.ui_queue = queue.Queue()
        self.root.after(100, self._drain_ui_queue)

        self._timer_running = False
        self._start_time = 0.0
        self.root.after(500, self._tick_ui)

        self._worker_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

        self._build_ui()

    def _drain_ui_queue(self):
        """Обрабатывает сообщения из очереди для обновления интерфейса."""
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
                elif command == 'worker_done':
                    self._on_worker_finished()
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def log(self, message: str):
        """Отправляет сообщение в лог."""
        self.ui_queue.put(('log', message))

    def set_status(self, text: str):
        """Устанавливает текст в строке состояния."""
        self.ui_queue.put(('status', text))

    def _build_ui(self):
        """Создает все элементы интерфейса."""
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        settings_frame = ttk.LabelFrame(main_frame, text="Настройки")
        settings_frame.pack(fill=tk.X, pady=5)

        self.input_file_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()

        self._create_file_row(settings_frame, "Исходный Excel файл:", self.input_file_var, self._choose_input_file)
        self._create_file_row(settings_frame, "Папка для сохранения:", self.output_dir_var, self._choose_output_dir)

        controls_frame = ttk.LabelFrame(main_frame, text="Управление")
        controls_frame.pack(fill=tk.X, pady=10)

        btn_row = ttk.Frame(controls_frame)
        btn_row.pack(fill=tk.X, pady=10, padx=5)

        self.start_btn = ttk.Button(btn_row, text="Старт", command=self._on_start, style="Accent.TButton")
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, ipady=5)

        self.stop_btn = ttk.Button(btn_row, text="Стоп", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10, ipady=5)

        log_frame = ttk.LabelFrame(main_frame, text="Логи выполнения")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = tk.Text(log_frame, state="disabled", height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))

        self.status_label = ttk.Label(status_frame, text="Готово")
        self.status_label.pack(side=tk.LEFT)

        self.timer_label = ttk.Label(status_frame, text="Время: 00:00:00")
        self.timer_label.pack(side=tk.RIGHT)

    def _create_file_row(self, parent, label_text, var, cmd):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=5, padx=5)
        ttk.Label(row, text=label_text, width=22).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(row, text="...", command=cmd, width=4).pack(side=tk.LEFT)

    def _choose_input_file(self):
        f = filedialog.askopenfilename(filetypes=[("Excel файлы", "*.xlsx *.xls"), ("Все файлы", "*.*")])
        if f: self.input_file_var.set(f)

    def _choose_output_dir(self):
        d = filedialog.askdirectory()
        if d: self.output_dir_var.set(d)

    def _tick_ui(self):
        if self._timer_running:
            elapsed = time.time() - self._start_time
            self.timer_label.config(text=f"Время: {time.strftime('%H:%M:%S', time.gmtime(elapsed))}")
        self.root.after(500, self._tick_ui)

    def _on_start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showinfo("Выполняется", "Обработка уже запущена.")
            return

        input_file = self.input_file_var.get().strip()
        output_dir = self.output_dir_var.get().strip()

        if not input_file or not os.path.isfile(input_file):
            messagebox.showerror("Ошибка", "Укажите корректный исходный Excel файл.")
            return
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showerror("Ошибка", "Укажите корректную папку для сохранения результатов.")
            return

        self._stop_flag.clear()
        self._timer_running = True
        self._start_time = time.time()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        self.log("="*50)
        self.log("ЗАПУСК ПРОЦЕССА ОБРАБОТКИ")

        self._worker_thread = threading.Thread(
            target=self._worker_main,
            args=(input_file, output_dir),
            daemon=True
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
        final_message = "Готово." if not self._stop_flag.is_set() else "Процесс остановлен пользователем."
        self.set_status(final_message)
        self.log(final_message.upper())
        self.log("="*50)

    # =============================== Основная логика (перенесена в воркер) ===============================

    def _worker_main(self, excel_file_path: str, output_folder: str):
        try:
            self.set_status("Чтение Excel файла...")
            Path(output_folder).mkdir(parents=True, exist_ok=True)
            ministry_data = {}

            try:
                df = pd.read_excel(excel_file_path, dtype=str).fillna('')
                self.log(f"✅ Успешно загружен файл: {excel_file_path}")
                self.log(f"📊 Найдено строк: {len(df)}")
            except Exception as exc:
                self.log(f"❌ Ошибка при чтении файла: {exc}"); raise

            if self._stop_flag.is_set(): return

            self.set_status("Анализ кластеров (групп коллизий)...")
            self.log("\n--- Анализ кластеров для ВСЕХ функций ---")

            all_function_ids = set(df['ID'].str.strip()); all_function_ids.discard('')
            adj_list = {func_id: [] for func_id in all_function_ids}
            for _, row in df.iterrows():
                func_id = str(row.get("ID", "")).strip()
                collisions = str(row.get("CollisionWith", "")).strip()
                if not func_id or not collisions: continue
                targets = re.split(r"[;,|\n]", collisions)
                for target in targets:
                    target_id = target.strip()
                    if target_id and target_id in all_function_ids:
                        adj_list[func_id].append(target_id)
                        if func_id not in adj_list.get(target_id, []):
                            adj_list.setdefault(target_id, []).append(func_id)

            visited, function_to_hub_map, hub_to_component_map = set(), {}, {}
            for func_id in all_function_ids:
                if self._stop_flag.is_set(): return
                if func_id not in visited:
                    hub_id, component, q = func_id, [], deque([func_id])
                    visited.add(func_id)
                    while q:
                        current_node = q.popleft(); component.append(current_node)
                        for neighbor in adj_list.get(current_node, []):
                            if neighbor not in visited:
                                visited.add(neighbor); q.append(neighbor)
                    hub_to_component_map[hub_id] = component
                    for node_in_component in component:
                        function_to_hub_map[node_in_component] = hub_id

            num_clusters = len(hub_to_component_map)
            num_groups = sum(1 for comp in hub_to_component_map.values() if len(comp) >= 2)
            self.log(f"🔍 Найдено {num_clusters} кластеров, из них {num_groups} являются 'Группами коллизий' (размером 2+).")

            function_to_ministry_map = {
                str(row.get("ID", "")).strip(): str(row.get("Исполняющий ГО", "")).strip()
                for _, row in df.iterrows() if str(row.get("ID", "")).strip() and str(row.get("Исполняющий ГО", "")).strip()
            }

            hub_id_to_group_name = self._create_collision_group_name_map(hub_to_component_map, df)

            if self._stop_flag.is_set(): return

            self.log("\n--- Создание файлов Функций ---")
            total_rows, functions_created_count = len(df), 0
            for index, row in df.iterrows():
                if self._stop_flag.is_set(): return
                self.set_status(f"Обработка функций: строка {index + 1}/{total_rows}")
                try:
                    file_id = str(row.get("ID", f"row_{index}")).strip()
                    if not file_id: self.log(f"⚠️ Пропущена строка #{index + 2} из-за отсутствия ID."); continue

                    level = str(row.get("Level", "")).strip()

                    if level not in ['4', '5', '6', '7', '8']:
                        content_lines = self._generate_function_md_content(row, function_to_hub_map, hub_id_to_group_name)
                        level_folder_name = f"Функции {sanitize_filename(level)} уровня" if level else "Функции без уровня"
                        level_output_folder = os.path.join(output_folder, level_folder_name)
                        Path(level_output_folder).mkdir(parents=True, exist_ok=True)

                        func_file_name = sanitize_filename(file_id)
                        file_path = os.path.join(level_output_folder, f"{func_file_name}.md")
                        with open(file_path, "w", encoding="utf-8") as fp: fp.write("\n".join(content_lines))
                        functions_created_count += 1

                    if executor_name := str(row.get("Исполняющий ГО", "")).strip():
                        ministry_data.setdefault(executor_name, {'functions': [], 'collisions': [], 'levels': [], 'supervisors': set()})
                        ministry_data[executor_name]['functions'].append(file_id)
                        if level: ministry_data[executor_name]['levels'].append(level)
                        if supervisor := str(row.get("Вышестоящий ГО", "")).strip():
                            ministry_data[executor_name]['supervisors'].add(supervisor)
                        collisions_for_stats = [c.strip() for c in re.split(r"[;,|\n]", str(row.get("CollisionWith", ""))) if c.strip()]
                        for coll_target in collisions_for_stats:
                            ministry_data[executor_name]['collisions'].append({'with': coll_target, 'via': file_id})
                except Exception as exc: self.log(f"❌ Ошибка при обработке строки {row.get('ID', index + 2)}: {exc}"); continue
            self.log(f"✅ Создано {functions_created_count} файлов функций.")

            if self._stop_flag.is_set(): return

            self.set_status("Создание файлов групп коллизий...")
            self._generate_collision_group_files(hub_to_component_map, df, output_folder, hub_id_to_group_name)

            if self._stop_flag.is_set(): return

            self.set_status("Создание портретов исполнителей...")
            self._generate_ministry_portraits(ministry_data, function_to_ministry_map, function_to_hub_map, output_folder, df, hub_id_to_group_name)

            self.log(f"\n🎉 Обработка полностью завершена! Файлы сохранены в папке: {output_folder}")

        except Exception as e:
            import traceback
            self.log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
            self.log(f"Traceback: {traceback.format_exc()}")
            self.set_status(f"Ошибка: {e}")
        finally:
            self.ui_queue.put(('worker_done', None))

    def _create_collision_group_name_map(self, hub_to_component_map, df):
        hub_id_to_group_name = {}
        level_counters = defaultdict(int)
        df_by_id = df.set_index('ID')

        for hub_id in sorted(hub_to_component_map.keys()):
            component = hub_to_component_map[hub_id]
            if len(component) < 2: continue

            levels = [lvl for fid in component if fid in df_by_id.index and (lvl := df_by_id.loc[fid].get("Level", "999")).isdigit()]
            min_lvl_val = min([int(l) for l in levels]) if levels else 999

            group_level = str(min_lvl_val) if min_lvl_val != 999 else "X"

            level_counters[group_level] += 1
            hub_id_to_group_name[hub_id] = f"Коллизия-{group_level}-{level_counters[group_level]}"

        return hub_id_to_group_name

    def _generate_function_md_content(self, row, function_to_hub_map, hub_id_to_group_name):
        file_id = str(row.get("ID", "")).strip()
        level = str(row.get("Level", "")).strip()
        level_tag = LEVEL_TO_TAG_MAP.get(level)

        content_lines = ["#функция", f"\n# ⚙️ {file_id}\n"]
        if level_tag: content_lines.append(f"**Уровень:** {level_tag}")

        if executor_name := str(row.get("Исполняющий ГО", "")).strip(): content_lines.append(f"**Исполняющий ГО:** [[{executor_name}]]")

        # --- НАЧАЛО ИЗМЕНЕНИЙ ---
        # Добавление типов функций
        if types_str := str(row.get("Type", "")).strip():
            types = [t.strip() for t in re.split(r'[;,|\n]', types_str) if t.strip()]
            if types:
                type_tags = ' '.join(f'#{create_clean_tag(t)}' for t in sorted(types))
                content_lines.append(f"**Типы:** {type_tags}")

        # Добавление сфер деятельности
        for i in range(1, 4):
            sphere_col = f"Sphere_{i}"
            if sphere_str := str(row.get(sphere_col, "")).strip():
                # Очищаем от ведущих цифр "1. ", "2. " и т.д.
                cleaned_sphere = re.sub(r'^\d+\.?\s*', '', sphere_str)
                if cleaned_sphere:
                    sphere_tag = f'#{create_clean_tag(cleaned_sphere)}'
                    content_lines.append(f"**Сфера (ур. {i}):** {sphere_tag}")
        # --- КОНЕЦ ИЗМЕНЕНИЙ ---

        content_lines.append("\n---\n")
        if func_text := str(row.get("FunctionText", "")).strip(): content_lines.append(f"> {func_text}\n")

        if collision := str(row.get("CollisionWith", "")).strip():
            if coll_list := [c.strip() for c in re.split(r"[;,|\n]", collision) if c.strip()]:
                content_lines.append(f"**Коллизия с:** {', '.join(coll_list)}")

        if file_id in function_to_hub_map:
            hub_id = function_to_hub_map[file_id]
            if group_name := hub_id_to_group_name.get(hub_id):
                content_lines.append(f"**Группа коллизий:** [[{group_name}]]")

        if source := str(row.get("Source", "")).strip(): content_lines.append(f"**Источник:** {source}")

        return content_lines

    def _generate_collision_group_files(self, hub_to_component_map, df, output_folder, hub_id_to_group_name):
        self.log("\n--- Создание файлов Групп коллизий ---")

        df_by_id = df.set_index('ID')
        created_groups_count = 0

        for hub_id, component in hub_to_component_map.items():
            if self._stop_flag.is_set(): return
            if not (group_name := hub_id_to_group_name.get(hub_id)): continue

            group_level = group_name.split('-')[1]
            folder_name = f"Коллизии {group_level} уровня" if group_level.isdigit() else "Коллизии без уровня"
            collision_folder_path = os.path.join(output_folder, folder_name)
            Path(collision_folder_path).mkdir(parents=True, exist_ok=True)

            level_tag = LEVEL_TO_TAG_MAP.get(group_level)
            file_path = os.path.join(collision_folder_path, f"{sanitize_filename(group_name)}.md")

            content = ["#коллизия", f"\n# 💥 {group_name}\n"]
            if level_tag: content.append(f"**Уровень:** {level_tag}\n")

            all_types = {t.strip() for fid in component if fid in df_by_id.index for t in re.split(r"[;,|\n]", df_by_id.loc[fid].get("Type", "")) if t.strip()}
            if all_types:
                content.append("**Типы функций в группе:**")
                content.append(' '.join(f'#{create_clean_tag(t)}' for t in sorted(list(all_types))))

            all_spheres2 = {create_clean_tag(re.sub(r'^\S+\s+', '', df_by_id.loc[fid].get("Sphere_2", ""))) for fid in component if fid in df_by_id.index and df_by_id.loc[fid].get("Sphere_2", "")}
            if all_spheres2:
                content.append("\n**Сферы деятельности (ур. 2) в группе:**")
                content.append(' '.join(f'#{s}' for s in sorted(list(all_spheres2))))

            content.append("\n---\n\n## 🧩 Функции в группе\n")
            for func_id in sorted(component):
                if func_id not in df_by_id.index: continue
                row = df_by_id.loc[func_id]
                executor = row.get("Исполняющий ГО", "Не указан")
                func_type = row.get("Type", "Не указан")
                sphere2 = row.get("Sphere_2", "Не указана")
                func_text = row.get("FunctionText", "Текст отсутствует.")

                # ИЗМЕНЕНО: Отображаем ID как текст для коллизий 4-8 уровней
                if group_level in ['4', '5', '6', '7', '8']:
                    func_id_display = func_id
                else:
                    func_id_display = f"[[{func_id}]]"

                content.append(f"### 🆔 {func_id_display}")
                content.append(f"**Исполнитель:** [[{executor}]]")
                content.append(f"**Тип:** {func_type}")
                content.append(f"**Сфера (ур. 2):** {sphere2}")
                content.append(f"**Текст функции:**\n> {func_text}\n")

            with open(file_path, "w", encoding="utf-8") as fp: fp.write("\n".join(content))
            created_groups_count += 1

        if created_groups_count == 0: self.log("ℹ️ Групп коллизий (размером 2+) не найдено.")
        else: self.log(f"✅ Создано {created_groups_count} файлов групп коллизий.")

    def _add_stats_table_section(self, content, title, table_header, counter, total_functions):
        """Вспомогательная функция для добавления раздела статистики в виде таблицы."""
        if not counter:
            return
        content.append(f"\n{title}")
        content.append(f"| {table_header} | Количество | Доля |")
        content.append("| :--- | :--- | :--- |")
        for item, count in counter.most_common():
            percentage = (count / total_functions) * 100 if total_functions > 0 else 0
            # Используем create_clean_tag для создания тега, как в исходном скрипте
            content.append(f"| #{create_clean_tag(item)} | {count} | {percentage:.1f}% |")
        content.append("")

    def _generate_ministry_portraits(self, ministry_data, function_to_ministry_map, function_to_hub_map, output_folder, df, hub_id_to_group_name):
        self.log("\n--- Создание портретов Исполнителей ---")

        df_by_id = df.set_index('ID')
        total_ministries, processed_ministries = len(ministry_data), 0

        for ministry_name, data in ministry_data.items():
            if self._stop_flag.is_set(): return
            if not ministry_name: continue

            processed_ministries += 1
            self.set_status(f"Создание портретов: {processed_ministries}/{total_ministries}")

            primary_level_str = ""
            if data.get('levels'):
                if numeric_levels := [int(lvl) for lvl in data['levels'] if lvl.isdigit()]:
                    primary_level_str = str(min(numeric_levels))

            portraits_folder_name = f"Портрет исполнителя {primary_level_str} уровня" if primary_level_str else "Портреты исполнителей без уровня"
            portraits_folder_path = os.path.join(output_folder, portraits_folder_name)
            Path(portraits_folder_path).mkdir(parents=True, exist_ok=True)
            file_name = sanitize_filename(ministry_name)
            file_path = os.path.join(portraits_folder_path, f"{file_name}.md")

            level_tag = LEVEL_TO_TAG_MAP.get(primary_level_str)
            content = ["#исполнитель", f"\n# 🏛️ {ministry_name}\n"]
            if level_tag: content.append(f"**Уровень:** {level_tag}")

            if primary_level_str != '1' and data.get('supervisors'):
                supervisors = sorted(list(data['supervisors']))

                # ИЗМЕНЕНИЕ: Логика добавления текста закомментирована
                # if primary_level_str.isdigit() and 5 <= int(primary_level_str) <= 8:
                #     supervisors = [f"{sup} Агентства по делам государственной службы" for sup in supervisors]

                supervisor_links = ', '.join(f"[[{sup}]]" for sup in supervisors)
                content.append(f"**Вышестоящий ГО:** {supervisor_links}")

            # --- СБОР СТАТИСТИКИ ---
            type_counter = Counter()
            sphere1_counter, sphere2_counter, sphere3_counter = Counter(), Counter(), Counter()

            for func_id in data['functions']:
                if func_id not in df_by_id.index: continue
                row = df_by_id.loc[func_id]
                if types_str := row.get("Type", ""):
                    types = [t.strip() for t in re.split(r'[;,|\n]', types_str) if t.strip()]
                    type_counter.update(types)
                for i in range(1, 4):
                    sphere_col = f"Sphere_{i}"
                    counter_map = {1: sphere1_counter, 2: sphere2_counter, 3: sphere3_counter}
                    if sphere_str := row.get(sphere_col, ""):
                        cleaned_sphere = re.sub(r'^\d+\.\s*', '', sphere_str.strip())
                        if cleaned_sphere:
                            counter_map[i].update([cleaned_sphere])

            # --- ФОРМИРОВАНИЕ КОНТЕНТА ---
            content.append("\n---\n\n## 📊 Общая статистика")
            total_functions = len(data['functions'])
            content.append(f"- **Всего функций:** {total_functions}")

            if any([type_counter, sphere1_counter, sphere2_counter, sphere3_counter]):
                self._add_stats_table_section(content, "## 🏷️ Статистика по типам функций", "Тип функции", type_counter, total_functions)
                self._add_stats_table_section(content, "## 🌍 Статистика по сферам деятельности (ур. 1)", "Сфера", sphere1_counter, total_functions)
                self._add_stats_table_section(content, "## 🌐 Статистика по сферам деятельности (ур. 2)", "Сфера", sphere2_counter, total_functions)
                self._add_stats_table_section(content, "## 🎯 Статистика по сферам деятельности (ур. 3)", "Сфера", sphere3_counter, total_functions)

            if data['collisions']:
                content.append("---\n\n## 🔗 Коллизии")
                collisions_grouped = {}
                for coll in data['collisions']:
                    colliding_ministry_name = function_to_ministry_map.get(coll['with'], coll['with'])
                    collisions_grouped.setdefault(colliding_ministry_name, []).append(coll['via'])

                for other_go, funcs in sorted(collisions_grouped.items()):
                    content.append(f"\n**Коллизия с:** [[{other_go}]]")
                    for func_id in sorted(funcs):
                        group_info = ""
                        if func_id in function_to_hub_map:
                            hub_id = function_to_hub_map[func_id]
                            if group_name := hub_id_to_group_name.get(hub_id):
                                group_info = f" (в группе [[{group_name}]])"

                        func_link = f"[[{func_id}]]" if primary_level_str in ['1', '2', '3'] else func_id
                        content.append(f"- **Через функцию:** {func_link}{group_info}")
                content.append("")

            # --- НАЧАЛО ИЗМЕНЕНИЙ ---
            content.append("---\n\n## 📋 Полный список функций\n")
            # Для уровней 4-8 встраиваем полный текст функции с деталями
            if primary_level_str in ['4', '5', '6', '7', '8']:
                for func_id in sorted(data['functions']):
                    if func_id in df_by_id.index:
                        row = df_by_id.loc[func_id]
                        func_text = row.get("FunctionText", "Текст функции не найден.")
                        func_type = row.get("Type", "").strip()
                        sphere_2 = row.get("Sphere_2", "").strip()

                        content.append(f"### 🆔 {func_id}")
                        if func_type:
                            content.append(f"**Тип:** {func_type}")
                        if sphere_2:
                            cleaned_sphere2 = re.sub(r'^\d+\.?\s*', '', sphere_2)
                            content.append(f"**Сфера (ур. 2):** {cleaned_sphere2}")
                        content.append(f"> {func_text}\n")
            # Для уровней 1-3 делаем ссылку на файл функции и добавляем детали в строку
            else:
                for func_id in sorted(data['functions']):
                    details = []
                    if func_id in df_by_id.index:
                        row = df_by_id.loc[func_id]
                        if func_type := row.get("Type", "").strip():
                            details.append(f"Тип: {func_type}")
                        if sphere_2 := row.get("Sphere_2", "").strip():
                            cleaned_sphere2 = re.sub(r'^\d+\.?\s*', '', sphere_2)
                            details.append(f"Сфера (ур. 2): {cleaned_sphere2}")

                    details_str = " | ".join(details)
                    if details_str:
                        content.append(f"- ⚙️ [[{func_id}]] ({details_str})")
                    else:
                        content.append(f"- ⚙️ [[{func_id}]]")
            # --- КОНЕЦ ИЗМЕНЕНИЙ ---

            with open(file_path, "w", encoding="utf-8") as fp: fp.write("\n".join(content))

        self.log(f"✅ Создано {processed_ministries} портретов исполнителей.")

def main():
    root = tk.Tk()
    app = MarkdownShardingApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()