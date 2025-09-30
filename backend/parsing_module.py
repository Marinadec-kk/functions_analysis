import os
import queue
import threading
import json
import re
from datetime import datetime
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional

# Placeholder for win32com.client if not available (for non-Windows environments or mocking)
try:
    import win32com.client
except ImportError:
    print("win32com.client not found. Document parsing will be limited or mocked.")
    win32com = None

from backend.utils import (
    log_message,
    update_status,
    update_progress,
    fast_clean_and_format_text,
    create_clean_tag,  # Potentially used for function names/IDs
    sanitize_filename,
    _sanitize_json_string,
)


class ParsingModule:
    """
    Модуль для парсинга документов и извлечения информации о функциях.
    Вся логика парсинга из 1_parsing.py переносится сюда.
    """

    def __init__(self, ui_queue: queue.Queue = None):
        self.stop_event = threading.Event()
        self.ui_queue = ui_queue  # Для отправки логов/статусов в UI через utils
        log_message(
            "ParsingModule инициализирован.", to_ui=False
        )  # Log to console only during init

    def set_stop_event(self):
        """Sets the stop event to signal workers to terminate."""
        self.stop_event.set()
        log_message("Сигнал остановки установлен для модуля парсинга.", level="info")

    def clear_stop_event(self):
        """Clears the stop event for a new run."""
        self.stop_event.clear()
        log_message("Сигнал остановки сброшен для модуля парсинга.", level="info")

    def read_doc_paragraphs_win32(self, doc_path: str) -> List[str]:
        """
        Reads paragraphs from a .doc or .docx file using win32com.client.
        Returns a list of paragraph texts.
        """
        if win32com is None:
            log_message(
                f"win32com.client не найден. Невозможно разобрать Word документ: {doc_path}",
                level="error",
            )
            return [f"Error: win32com.client not available for {doc_path}"]

        try:
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(doc_path)
            paragraphs = [
                para.Range.Text.strip()
                for para in doc.Paragraphs
                if para.Range.Text.strip()
            ]
            doc.Close()
            word.Quit()
            log_message(f"Успешно извлечено {len(paragraphs)} параграфов из {doc_path}")
            return paragraphs
        except Exception as e:
            log_message(f"Ошибка чтения Word документа {doc_path}: {e}", level="error")
            if "word" in locals() and word:
                word.Quit()
            return []

    def create_abbreviation(self, text: str, max_len: int = 20) -> str:
        """
        Creates an abbreviation from a given text.
        """
        if not text:
            return ""
        words = text.split()
        if len(words) <= 1:
            return text[:max_len]

        abbrev = "".join(word[0].upper() for word in words if word)
        if len(abbrev) > max_len:
            abbrev = text[
                :max_len
            ]  # Fallback to truncating if abbreviation is too long
        return abbrev

    def enumerate_functions(self, paragraphs: List[str]) -> List[Dict[str, Any]]:
        """
        Iterates through paragraphs and extracts potential function definitions.
        This is a placeholder and would need a more sophisticated NLP/regex
        engine to accurately identify functions based on context.
        """
        functions_data = []
        for i, para_text in enumerate(paragraphs):
            cleaned_text = fast_clean_and_format_text(para_text)
            if cleaned_text:
                # Placeholder: In a real scenario, this would involve complex regex or NLP
                # to detect function signatures, names, descriptions, etc.
                # For demonstration, let's assume each non-empty paragraph *could* be
                # a function description or contain function info.
                function_name = (
                    f"Function_{i + 1}_{self.create_abbreviation(cleaned_text)}"
                )
                functions_data.append(
                    {
                        "id": i,
                        "raw_text": para_text,
                        "cleaned_text": cleaned_text,
                        "potential_function_name": function_name,
                        "description": cleaned_text,  # For now, description is the cleaned text
                    }
                )
        return functions_data

    def _parse_full_go_name(self, name_text: str) -> Tuple[str, str, str]:
        """
        Parses a full Go-style name (e.g., "package.Subsystem.FunctionName")
        into its components. This is a simplified placeholder.
        """
        parts = name_text.split(".")
        package_name = ""
        subsystem_name = ""
        function_name = name_text

        if len(parts) >= 3:
            package_name = parts[0]
            subsystem_name = parts[1]
            function_name = ".".join(parts[2:])
        elif len(parts) == 2:
            package_name = parts[0]
            function_name = parts[1]
        elif len(parts) == 1:
            function_name = parts[0]

        return package_name, subsystem_name, function_name

    def _extract_full_go_name(self, text: str) -> Tuple[str, str, str, str]:
        """
        Attempts to extract a full Go-style name from a given text.
        This is a placeholder and needs robust regex/NLP for actual Go code.
        """
        # This is a very basic example. Real Go name extraction would be more complex.
        match = re.search(r"([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)", text)
        if match:
            full_name = match.group(0)
            package, subsystem, func_name = self._parse_full_go_name(full_name)
            return full_name, package, subsystem, func_name

        # If no full Go-style name found, try to extract a simple name
        match = re.search(r"([a-zA-Z_]\w*\.[a-zA-Z_]\w*)", text)
        if match:
            full_name = match.group(0)
            package, subsystem, func_name = self._parse_full_go_name(full_name)
            return full_name, package, subsystem, func_name

        return "", "", "", ""

    def process_single_file(self, file_path: str) -> pd.DataFrame:
        """
        Processes a single document file to extract function data.
        """
        log_message(f"Начало обработки файла: {file_path}", level="info")
        file_extension = os.path.splitext(file_path)[1].lower()
        paragraphs = []

        if file_extension in [".doc", ".docx"]:
            paragraphs = self.read_doc_paragraphs_win32(file_path)
        elif file_extension == ".txt":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    paragraphs = [line.strip() for line in f if line.strip()]
            except Exception as e:
                log_message(
                    f"Ошибка чтения текстового файла {file_path}: {e}", level="error"
                )
                return pd.DataFrame()
        else:
            log_message(
                f"Неподдерживаемый тип файла для парсинга: {file_path}", level="error"
            )
            return pd.DataFrame()

        log_message(f"Извлечено {len(paragraphs)} параграфов из {file_path}")

        functions_data = []
        for i, para_text in enumerate(paragraphs):
            if self.stop_event.is_set():
                log_message(
                    f"Остановка обработки файла {file_path} по запросу.", level="info"
                )
                break

            cleaned_text = fast_clean_and_format_text(para_text)
            full_go_name, pkg, sub, func = self._extract_full_go_name(cleaned_text)

            entry = {
                "file_path": file_path,
                "paragraph_index": i,
                "raw_text": para_text,
                "cleaned_text": cleaned_text,
                "extracted_go_full_name": full_go_name,
                "extracted_go_package": pkg,
                "extracted_go_subsystem": sub,
                "extracted_go_function": func,
                "description": cleaned_text,  # Placeholder, might be refined later
            }
            functions_data.append(entry)

        df = pd.DataFrame(functions_data)
        log_message(
            f"Обработка файла {file_path} завершена. Извлечено {len(df)} записей."
        )
        return df

    def run_parsing_pipeline(
        self,
        input_folder: str,
        output_file: str,
        progress_callback: Optional[callable] = None,
        status_callback: Optional[callable] = None,
    ) -> pd.DataFrame:
        """
        Main entry point for the parsing module. Processes all files in the input folder.
        """
        self.clear_stop_event()
        all_parsed_data = []

        if not os.path.isdir(input_folder):
            log_message(f"Входная папка не найдена: {input_folder}", level="error")
            if status_callback:
                status_callback(f"Ошибка: Входная папка не найдена: {input_folder}")
            return pd.DataFrame()

        file_list = [
            os.path.join(input_folder, f)
            for f in os.listdir(input_folder)
            if os.path.isfile(os.path.join(input_folder, f))
            and f.lower().endswith((".doc", ".docx", ".txt"))
        ]

        if not file_list:
            log_message(
                f"Во входной папке {input_folder} не найдено поддерживаемых файлов.",
                level="info",
            )
            if status_callback:
                status_callback(
                    f"Парсинг: В папке {input_folder} нет файлов для обработки."
                )
            return pd.DataFrame()

        update_status(f"Начало парсинга {len(file_list)} файлов.", to_ui=True)

        for i, file_path in enumerate(file_list):
            if self.stop_event.is_set():
                log_message("Парсинг отменен по запросу пользователя.", level="info")
                if status_callback:
                    status_callback("Парсинг отменен.")
                break

            log_message(
                f"Обработка файла {i + 1}/{len(file_list)}: {file_path}", level="info"
            )
            if status_callback:
                status_callback(f"Парсинг файла: {os.path.basename(file_path)}")
            if progress_callback:
                progress_callback(i + 1, len(file_list))

            df_file = self.process_single_file(file_path)
            if not df_file.empty:
                all_parsed_data.append(df_file)

        final_df = (
            pd.concat(all_parsed_data, ignore_index=True)
            if all_parsed_data
            else pd.DataFrame()
        )

        if not final_df.empty:
            try:
                final_df.to_excel(output_file, index=False)
                log_message(
                    f"Сохранены результаты парсинга в: {output_file}", level="info"
                )
            except Exception as e:
                log_message(
                    f"Ошибка сохранения файла Excel {output_file}: {e}", level="error"
                )
        else:
            log_message("Нет данных для сохранения после парсинга.", level="info")

        update_status("Парсинг завершен.", to_ui=True)
        if progress_callback:
            progress_callback(len(file_list), len(file_list))  # Ensure progress is 100%
        return final_df


# Для автономного тестирования, если это необходимо
if __name__ == "__main__":
    import sys

    sys.path.append(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    from backend.utils import set_ui_queue_callback

    # Имитация UI очереди для логирования
    mock_ui_queue = queue.Queue()
    set_ui_queue_callback(mock_ui_queue.put)

    input_folder_path = "temp_input_docs_parsing_test"
    output_excel_path = os.path.join(input_folder_path, "parsed_functions.xlsx")

    os.makedirs(input_folder_path, exist_ok=True)
    with open(
        os.path.join(input_folder_path, "test_doc_1.txt"), "w", encoding="utf-8"
    ) as f:
        f.write("Это первый параграф.\n")
        f.write("Второй параграф, содержащий SomePackage.SubSystem.MyFunction.\n")
    with open(
        os.path.join(input_folder_path, "test_doc_2.txt"), "w", encoding="utf-8"
    ) as f:
        f.write("Еще один параграф. AnotherPackage.AnotherFunc.\n")
        f.write("Последний параграф.\n")

    parser = ParsingModule()

    def run_parser_in_thread():
        log_message("Запуск симуляции парсинга.", level="info")
        df_result = parser.run_parsing_pipeline(
            input_folder=input_folder_path,
            output_file=output_excel_path,
            progress_callback=lambda c, t: update_progress(c, t, "Parsing"),
            status_callback=lambda msg: update_status(msg),
        )
        if not df_result.empty:
            log_message("Полученные данные:\n" + str(df_result.head()), level="info")
        else:
            log_message("DataFrame после парсинга пуст.", level="info")

        # Clean up
        if os.path.exists(output_excel_path):
            os.remove(output_excel_path)
        if os.path.exists(os.path.join(input_folder_path, "test_doc_1.txt")):
            os.remove(os.path.join(input_folder_path, "test_doc_1.txt"))
        if os.path.exists(os.path.join(input_folder_path, "test_doc_2.txt")):
            os.remove(os.path.join(input_folder_path, "test_doc_2.txt"))
        os.rmdir(input_folder_path)
        log_message("Очистка временных файлов завершена.", level="info")

    parser_thread = threading.Thread(target=run_parser_in_thread)
    parser_thread.start()
    parser_thread.join()

    print("\\n--- Сообщения из UI очереди ---")
    while not mock_ui_queue.empty():
        print(mock_ui_queue.get())
    print("Демонстрация parsing_module.py завершена.")
