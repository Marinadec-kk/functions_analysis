import os
import pandas as pd
import argparse
import sys
from pathlib import Path

try:
    from docx import Document
except ImportError:
    Document = None

# --- ANSI Color Codes ---
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# --- Helper Functions ---

def clear_screen():
    """Clears the console screen."""
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header(text):
    """Prints a formatted header."""
    print(f"""{Colors.HEADER}{Colors.BOLD}
==================================================
{text:^50}
=================================================={Colors.ENDC}""")

def get_human_readable_size(size_in_bytes):
    """Converts size in bytes to a human-readable format."""
    if size_in_bytes is None:
        return ""
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'КБ', 2: 'МБ', 3: 'ГБ'}
    while size_in_bytes >= power and n < len(power_labels) -1 :
        size_in_bytes /= power
        n += 1
    return f"{size_in_bytes:.1f} {power_labels[n]}"

def get_files_recursively(directory, extensions):
    """Recursively finds files, returning path, size, and modification time."""
    found_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(tuple(extensions)):
                try:
                    filepath = Path(root) / file
                    stat = filepath.stat()
                    found_files.append((str(filepath), stat.st_size))
                except (FileNotFoundError, PermissionError):
                    # Skip files that can't be accessed
                    continue
    return found_files

def browse_path(start_path, supported_extensions):
    """
    Allows interactive browsing of directories and selection of a file.
    Returns the absolute path of the selected file or None if exited.
    """
    current_path = Path(start_path).resolve()
    while True:
        clear_screen()
        print_header("Выбор файла для анализа")
        print(f"\n{Colors.BOLD}Текущий каталог:{Colors.ENDC} {Colors.CYAN}{current_path}{Colors.ENDC}")

        items = []
        display_items = []

        # Add directories
        for item in sorted(current_path.iterdir()):
            if item.is_dir():
                items.append({'type': 'dir', 'path': item})
                display_items.append(f"[{Colors.BLUE}Папка{Colors.ENDC}] {item.name}")

        # Add supported files
        for item in sorted(current_path.iterdir()):
            if item.is_file() and item.suffix in supported_extensions:
                try:
                    stat = item.stat()
                    items.append({'type': 'file', 'path': item, 'size': stat.st_size})
                    display_items.append(f"[{Colors.GREEN}Файл{Colors.ENDC}] {item.name} ({get_human_readable_size(stat.st_size)})")
                except (FileNotFoundError, PermissionError):
                    # Skip inaccessible files
                    continue

        if not items:
            print(f"\n{Colors.WARNING}В текущем каталоге нет папок или поддерживаемых файлов.{Colors.ENDC}")

        choice_idx = choose_from_list(display_items, "Выберите элемент:", allow_go_back=True)

        if choice_idx == -1:  # Go Back
            if current_path == Path(start_path).resolve().anchor: # If at root, cannot go up further
                print(f"{Colors.FAIL}Вы уже в корневой директории. Невозможно подняться выше.{Colors.ENDC}")
                input(f"\n{Colors.BOLD}Нажмите Enter для продолжения...{Colors.ENDC}")
            else:
                current_path = current_path.parent
        elif choice_idx is not None: # An item was selected
            selected_item = items[choice_idx]
            if selected_item['type'] == 'dir':
                current_path = selected_item['path']
            elif selected_item['type'] == 'file':
                return str(selected_item['path'])
        # If choice_idx is None, sys.exit() was called by choose_from_list

    return None # Should not be reached if sys.exit() is called or file is returned

def read_docx_tables(filepath):
    """Reads tables from a .docx file, handling errors gracefully."""
    try:
        document = Document(filepath)
        if not document.tables:
            return [], "В документе не найдено таблиц."
        
        dataframes = []
        for i, table in enumerate(document.tables):
            data = [[cell.text for cell in row.cells] for row in table.rows]
            if not data or not data[0]:
                print(f"{Colors.WARNING}  - Пропуск пустой или некорректной таблицы #{i+1}{Colors.ENDC}")
                continue
            
            header = data[0]
            df_data = data[1:]
            # Ensure all rows have the same number of columns as the header
            df_data_corrected = [row for row in df_data if len(row) == len(header)]
            if len(df_data_corrected) != len(df_data):
                 print(f"{Colors.WARNING}  - В таблице #{i+1} исправлены строки с некорректным числом столбцов.{Colors.ENDC}")

            df = pd.DataFrame(df_data_corrected, columns=header)
            dataframes.append(df)
        
        if not dataframes:
            return [], "Таблицы в документе пусты или имеют неверный формат."

        return dataframes, None
    except Exception as e:
        return [], f"Не удалось прочитать DOCX файл: {e}"

def choose_from_list(items, title, allow_go_back=False, exit_message="Выход из программы."):
    """Generic function to let a user choose an item from a list, with optional 'Go Back' and 'Exit' options."""
    print(f"\n{Colors.CYAN}{title}{Colors.ENDC}")

    for i, item_text in enumerate(items):
        print(f"  {Colors.GREEN}{i + 1}:{Colors.ENDC} {item_text}")

    # Options for navigation
    nav_options_start_idx = len(items) + 1
    if allow_go_back:
        print(f"  {Colors.BLUE}{nav_options_start_idx}: Назад{Colors.ENDC}")
        nav_options_start_idx += 1
    print(f"  {Colors.FAIL}{nav_options_start_idx}: Выход{Colors.ENDC}")

    while True:
        try:
            choice = int(input(f"\n{Colors.BOLD}Введите ваш выбор: {Colors.ENDC}"))
            if 1 <= choice <= len(items):
                return choice - 1  # Return 0-based index
            elif allow_go_back and choice == (len(items) + 1):
                return -1  # Special value for "Go Back"
            elif choice == nav_options_start_idx: # This will be the exit option
                print(f"\n{Colors.WARNING}{exit_message}{Colors.ENDC}")
                sys.exit()
            else:
                max_choice = nav_options_start_idx
                print(f"{Colors.FAIL}Неверный номер. Пожалуйста, выберите от 1 до {max_choice}.{Colors.ENDC}")
        except ValueError:
            print(f"{Colors.FAIL}Неверный ввод. Пожалуйста, введите число.{Colors.ENDC}")

def analyze_column(df, column_name):
    """Performs and prints a smart analysis of a DataFrame column."""
    print_header("Результаты анализа")
    
    if column_name not in df.columns:
        print(f"{Colors.FAIL}Ошибка: столбец '{column_name}' не найден в выбранной таблице.{Colors.ENDC}")
        return

    column_series = df[column_name].dropna()
    
    print(f"Анализ для столбца: {Colors.BOLD}{Colors.CYAN}'{column_name}'{Colors.ENDC}")
    print("---")
    
    if column_series.empty:
        print(f"{Colors.WARNING}Столбец пуст или содержит только пропущенные значения.{Colors.ENDC}")
        return

    total_values = len(column_series)
    unique_values = column_series.nunique()
    
    print(f"Общее количество непустых значений: {Colors.GREEN}{total_values}{Colors.ENDC}")
    print(f"Количество уникальных значений: {Colors.GREEN}{unique_values}{Colors.ENDC}")
    
    # Smart Analysis based on data type
    if pd.api.types.is_numeric_dtype(column_series):
        print(f"\n{Colors.BOLD}Числовая статистика:{Colors.ENDC}")
        print(f"  - Среднее: {Colors.GREEN}{column_series.mean():.2f}{Colors.ENDC}")
        print(f"  - Медиана: {Colors.GREEN}{column_series.median():.2f}{Colors.ENDC}")
        print(f"  - Стандартное отклонение: {Colors.GREEN}{column_series.std():.2f}{Colors.ENDC}")
        print(f"  - Минимум: {Colors.GREEN}{column_series.min()}{Colors.ENDC}")
        print(f"  - Максимум: {Colors.GREEN}{column_series.max()}{Colors.ENDC}")
    else:
        print(f"\n{Colors.BOLD}Топ-5 самых частых значений:{Colors.ENDC}")
        value_counts = column_series.value_counts().nlargest(5)
        for value, count in value_counts.items():
            # Truncate long value strings for display
            display_value = str(value) if len(str(value)) < 60 else str(value)[:57] + '...'
            print(f"  - '{Colors.CYAN}{display_value}{Colors.ENDC}': {Colors.GREEN}{count}{Colors.ENDC} раз")

def interactive_mode():
    """Runs the main interactive loop of the application."""
    supported_extensions = ['.csv', '.xlsx']
    if Document:
        supported_extensions.append('.docx')
    else:
        print(f"{Colors.WARNING}Внимание: библиотека python-docx не найдена. Анализ .docx файлов недоступен.{Colors.ENDC}")
        print(f"{Colors.WARNING}Для установки выполните: pip install python-docx{Colors.ENDC}")

    filepath = None
    df = None
    column_name = None

    while True: # Main interactive loop
        # --- 1. File Selection ---
        if filepath is None:
            clear_screen()
            print_header("Анализатор таблиц")
            filepath = browse_path('.', supported_extensions)
            if filepath is None: # User exited from browse_path
                return

        # --- 2. Table/Sheet Selection ---
        if df is None:
            clear_screen()
            print_header("Чтение файла и выбор таблицы")
            print(f"\n{Colors.BOLD}Выбранный файл:{Colors.ENDC} {Colors.CYAN}{os.path.relpath(filepath)}{Colors.ENDC}")
            print(f"\nЧитаю файл: {Colors.CYAN}{os.path.relpath(filepath)}{Colors.ENDC}")

            dfs, error_message = [], None
            try:
                if filepath.endswith('.csv'):
                    dfs.append(pd.read_csv(filepath))
                elif filepath.endswith('.xlsx'):
                    xls = pd.read_excel(filepath, sheet_name=None)
                    for sheet_name, df_sheet in xls.items():
                        print(f"  - Найден лист: '{Colors.GREEN}{sheet_name}{Colors.ENDC}' (строк: {len(df_sheet)}) ")
                        dfs.append(df_sheet)
                elif filepath.endswith('.docx') and Document:
                    dfs, error_message = read_docx_tables(filepath)

                if error_message:
                    print(f"\n{Colors.FAIL}{error_message}{Colors.ENDC}")
                    filepath = None # Reset filepath to re-select
                    input(f"\n{Colors.BOLD}Нажмите Enter для продолжения...{Colors.ENDC}")
                    continue # Go back to file selection
                if not dfs:
                    print(f"\n{Colors.FAIL}Не удалось извлечь таблицы из файла.{Colors.ENDC}")
                    filepath = None # Reset filepath to re-select
                    input(f"\n{Colors.BOLD}Нажмите Enter для продолжения...{Colors.ENDC}")
                    continue # Go back to file selection

            except Exception as e:
                print(f"\n{Colors.FAIL}Критическая ошибка при чтении файла: {e}{Colors.ENDC}")
                filepath = None # Reset filepath to re-select
                input(f"\n{Colors.BOLD}Нажмите Enter для продолжения...{Colors.ENDC}")
                continue # Go back to file selection

            table_idx = 0 # Default for single table
            if len(dfs) > 1:
                table_options = [f"Таблица/Лист #{i+1} (строк: {len(d)}, столбцов: {len(d.columns)})" for i, d in enumerate(dfs)]
                table_idx = choose_from_list(table_options, "В файле несколько таблиц/листов. Выберите одну:", allow_go_back=True)
                if table_idx == -1: # Go Back
                    filepath = None # Reset filepath to re-select
                    df = None # Reset df
                    continue # Go back to file selection
                df = dfs[table_idx]
            else:
                df = dfs[0]

        # --- 3. Column Selection ---
        if column_name is None:
            clear_screen()
            print_header("Выбор столбца для анализа")
            print(f"\n{Colors.BOLD}Выбранный файл:{Colors.ENDC} {Colors.CYAN}{os.path.relpath(filepath)}{Colors.ENDC}")
            if len(dfs) > 1:
                print(f"{Colors.BOLD}Выбранная таблица/лист:{Colors.ENDC} {Colors.CYAN}#{table_idx + 1}{Colors.ENDC}")

            col_idx = choose_from_list(df.columns, "Выберите столбец для анализа:", allow_go_back=True)
            if col_idx == -1: # Go Back
                df = None # Reset df to re-select table
                column_name = None # Reset column_name
                continue # Go back to table selection
            column_name = df.columns[col_idx]

        # --- 4. Analysis ---
        clear_screen()
        analyze_column(df, column_name)

        # --- Summary ---
        print("\n---")
        print(f"{Colors.BOLD}Резюме анализа:{Colors.ENDC}")
        print(f"  - Файл: {Colors.CYAN}{os.path.relpath(filepath)}{Colors.ENDC}")
        if len(dfs) > 1:
            print(f"  - Выбранная таблица/лист: {Colors.CYAN}#{table_idx + 1}{Colors.ENDC}")
        print(f"  - Выбранный столбец: {Colors.CYAN}{column_name}{Colors.ENDC}")

        # Ask to analyze another column or file
        print(f"\n{Colors.BOLD}Что дальше?{Colors.ENDC}")
        print(f"  {Colors.GREEN}1:{Colors.ENDC} Анализировать другой столбец в этой таблице")
        print(f"  {Colors.GREEN}2:{Colors.ENDC} Анализировать другую таблицу в этом файле")
        print(f"  {Colors.GREEN}3:{Colors.ENDC} Анализировать другой файл")
        print(f"  {Colors.FAIL}0:{Colors.ENDC} Выход")

        while True:
            try:
                next_action = int(input(f"\n{Colors.BOLD}Введите ваш выбор: {Colors.ENDC}"))
                if next_action == 1:
                    column_name = None # Reset column to re-select
                    break # Continue main loop to column selection
                elif next_action == 2:
                    df = None # Reset df to re-select table
                    column_name = None # Reset column
                    break # Continue main loop to table selection
                elif next_action == 3:
                    filepath = None # Reset filepath to re-select file
                    df = None # Reset df
                    column_name = None # Reset column
                    break # Continue main loop to file selection
                elif next_action == 0:
                    print(f"\n{Colors.WARNING}Выход из программы.{Colors.ENDC}")
                    sys.exit()
                else:
                    print(f"{Colors.FAIL}Неверный номер. Пожалуйста, выберите от 0 до 3.{Colors.ENDC}")
            except ValueError:
                print(f"{Colors.FAIL}Неверный ввод. Пожалуйста, введите число.{Colors.ENDC}")

def main():
    """Main function to handle argument parsing and execution mode."""
    parser = argparse.ArgumentParser(
        description="Инструмент для анализа столбцов в файлах CSV, XLSX и DOCX.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=f"""Примеры использования:
  1. Интерактивный режим (если не указаны флаги):
     {Colors.GREEN}python analyze_table.py{Colors.ENDC}

  2. Прямой анализ (указан файл и столбец):
     {Colors.GREEN}python analyze_table.py -f "path/to/your/file.xlsx" -c "Название Столбца"{Colors.ENDC}
  
  3. Прямой анализ с указанием номера таблицы/листа (для XLSX/DOCX):
     {Colors.GREEN}python analyze_table.py -f "file.docx" -c "Столбец" -t 2{Colors.ENDC}
"""
    )
    parser.add_argument('-f', '--file', type=str, help="Путь к файлу для анализа.")
    parser.add_argument('-c', '--column', type=str, help="Название столбца для анализа.")
    parser.add_argument('-t', '--table', type=int, default=1, help="Номер таблицы или листа для анализа (по умолчанию: 1).")

    args = parser.parse_args()

    if args.file and args.column:
        # --- Direct Mode ---
        filepath = args.file
        column_name = args.column
        table_index = args.table - 1

        if not os.path.exists(filepath):
            print(f"{Colors.FAIL}Ошибка: Файл не найден по пути: {filepath}{Colors.ENDC}")
            return

        dfs, error_message = [], None
        try:
            if filepath.endswith('.csv'):
                dfs.append(pd.read_csv(filepath))
            elif filepath.endswith('.xlsx'):
                xls = pd.read_excel(filepath, sheet_name=None)
                dfs.extend(list(xls.values()))
            elif filepath.endswith('.docx') and Document:
                dfs, error_message = read_docx_tables(filepath)
            
            if error_message:
                print(f"\n{Colors.FAIL}{error_message}{Colors.ENDC}")
                return
            
            if not dfs or table_index >= len(dfs):
                print(f"{Colors.FAIL}Ошибка: Таблица/лист с номером {args.table} не найдена в файле.{Colors.ENDC}")
                print(f"Всего найдено таблиц/листов: {len(dfs)}.")
                return
            
            df = dfs[table_index]
            analyze_column(df, column_name)

        except Exception as e:
            print(f"\n{Colors.FAIL}Критическая ошибка при обработке файла: {e}{Colors.ENDC}")

    else:
        # --- Interactive Mode ---
        try:
            interactive_mode()
        except (KeyboardInterrupt):
            print(f"\n\n{Colors.WARNING}Программа прервана пользователем.{Colors.ENDC}")
        except Exception as e:
            print(f"\n{Colors.FAIL}Произошла непредвиденная ошибка: {e}{Colors.ENDC}")


if __name__ == "__main__":
    main()
    input(f"\n{Colors.BOLD}Анализ завершен. Нажмите Enter для выхода...{Colors.ENDC}")