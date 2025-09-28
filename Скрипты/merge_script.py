import pandas as pd
import re
from thefuzz import fuzz
from tqdm import tqdm
import sys
import os
import io  # Import io module
import argparse  # Import argparse for command-line arguments

# --- Set UTF-8 encoding for stdout and stderr ---
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# --- 1. Normalization Functions ---
def normalize_text(text):
    """Converts text to lowercase, removes punctuation and extra whitespace."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = re.sub(r"[\d\W_]+", " ", text)  # Keep only words and spaces
    text = " ".join(text.split())
    return text


def normalize_gov_body_name(name):
    if pd.isna(name):
        return name
    name = str(name).strip().lower()

    # 1. Remove quotes and text in parentheses
    name = name.replace('"', "").replace("«", "").replace("»", "")
    name = re.sub(r"\s*\(.*\)", "", name)

    # 2. Standardize all mentions of "Kazakhstan" to 'рк'
    name = name.replace("республики казахстан", "рк")
    name = name.replace("республика казахстан", "рк")

    # 3. Remove common prefixes (like ГУ, РГУ)
    prefixes = ["гу ", "ргу ", "государственное учреждение ", "р "]
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix) :]

    # 4. General, safe 'рк' removal
    # Removes ' рк' from the end of the name (e.g., "министерство финансов рк")
    name = re.sub(r"\s+рк$", "", name)
    # Removes 'рк ' from the middle in specific contexts (e.g., "агентство рк по...")
    name = re.sub(r"^(.*?)\s+рк\s+(по|при|о)\s+", r"\1 \2 ", name)

    # 5. Final cleanup of extra spaces
    name = re.sub(r"\s+", " ", name).strip()

    return name


# --- Main execution block ---
if __name__ == "__main__":
    # --- DEBUG: Define the problematic strings for specific logging ---
    PROBLEM_STRING_A_RAW = "разрабатывает и утверждает подзаконные нормативные правовые акты определяющие порядок оказания государственных услуг"
    PROBLEM_STRING_B_RAW = "разрабатывает и утверждает подзаконные нормативные правовые акты определяющие порядок оказания государственных услуг в регулируемой сфере"
    NORMALIZED_PROBLEM_STRING_A = normalize_text(PROBLEM_STRING_A_RAW)
    NORMALIZED_PROBLEM_STRING_B = normalize_text(PROBLEM_STRING_B_RAW)
    # --- END DEBUG definitions ---

    parser = argparse.ArgumentParser(
        description="Скрипт для слияния данных о функциях из двух Excel файлов с использованием нечеткого сопоставления.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--file1",
        type=str,
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "Таблицы",
            "Функции от Лаборатории.xlsx",
        ),
        help="Путь к первому Excel файлу (от Лаборатории). По умолчанию: \n%(default)s",
    )
    parser.add_argument(
        "--file2",
        type=str,
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "Таблицы",
            "Функции от МФ.xlsx",
        ),
        help="Путь ко второму Excel файлу (от МФ). По умолчанию: \n%(default)s",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "Таблицы",
            "Функции сводная.xlsx",
        ),
        help="Путь к выходному Excel файлу. По умолчанию: \n%(default)s",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=90,
        help="Основной порог схожести (0-100). Используется для первого прохода в --two-pass режиме.",
    )
    parser.add_argument(
        "--two-pass",
        action="store_true",
        help="Включить двухпроходный режим сопоставления (строгий, затем мягкий).",
    )
    parser.add_argument(
        "--second-pass-threshold",
        type=int,
        default=70,
        help="Порог схожести для второго, 'мягкого' прохода в --two-pass режиме.",
    )

    args = parser.parse_args()

    # --- 2. Load and Prepare Data ---
    print("\n--- Запуск скрипта слияния данных ---\n")
    print(f"Входной файл 1 (Лаборатория): {args.file1}")
    print(f"Входной файл 2 (МФ): {args.file2}")
    print(f"Выходной файл: {args.output}")
    print(f"Порог нечеткого сопоставления: {args.threshold}\n")

    print("Шаг 1/5: Загрузка и подготовка данных...")

    try:
        df1 = pd.read_excel(args.file1)
        df2 = pd.read_excel(args.file2)
        print(f"  Загружено {len(df1)} строк из '{os.path.basename(args.file1)}'.")
        print(f"  Загружено {len(df2)} строк из '{os.path.basename(args.file2)}'.")
    except FileNotFoundError as e:
        print(f"Ошибка: Файл не найден: {e.filename}.")
        print(
            "Пожалуйста, убедитесь, что пути к файлам указаны верно и файлы существуют."
        )
        sys.exit(1)
    except Exception as e:
        print(f"Произошла ошибка при чтении Excel файлов: {e}")
        sys.exit(1)

    unmatched_lab_indices = set(df1.index)

    # Add prefixes to column names to avoid collision
    df1.columns = [f"lab_{c}" for c in df1.columns]
    df2.columns = [f"mf_{c}" for c in df2.columns]

    # Identify the key function and government body columns by their new names
    lab_function_col = next((col for col in df1.columns if "FunctionText" in col), None)
    mf_function_col = next(
        (col for col in df2.columns if "Наименование функции" in col), None
    )
    lab_gov_body_col = next(
        (col for col in df1.columns if "Исполняющий ГО" in col), None
    )
    mf_gov_body_col = next((col for col in df2.columns if "Госорган" in col), None)

    missing_cols = []
    if not lab_function_col:
        missing_cols.append(f"'FunctionText' в '{os.path.basename(args.file1)}'")
    if not mf_function_col:
        missing_cols.append(
            f"'Наименование функции' в '{os.path.basename(args.file2)}'"
        )
    if not lab_gov_body_col:
        missing_cols.append(f"'Исполняющий ГО' в '{os.path.basename(args.file1)}'")
    if not mf_gov_body_col:
        missing_cols.append(f"'Госорган' в '{os.path.basename(args.file2)}'")

    if missing_cols:
        print(f"Ошибка: Не найдены все необходимые колонки: {', '.join(missing_cols)}.")
        print(
            "Пожалуйста, убедитесь, что имена колонок в ваших файлах соответствуют ожидаемым."
        )
        sys.exit(1)

    # Create normalized text columns for functions
    print("Шаг 2/5: Нормализация текстовых данных функций...")
    df1["clean_text"] = df1[lab_function_col].apply(normalize_text)
    df2["clean_text"] = df2[mf_function_col].apply(normalize_text)

    # *** FIX: Rename the helper column in the second dataframe to prevent index collision ***
    df2.rename(columns={"clean_text": "clean_text_mf"}, inplace=True)

    initial_df1_rows = len(df1)
    initial_df2_rows = len(df2)

    # Drop rows where the normalized function text is empty
    df1.dropna(subset=["clean_text", lab_function_col], inplace=True)
    df1 = df1[df1["clean_text"] != ""]
    df2.dropna(subset=["clean_text_mf", mf_function_col], inplace=True)
    df2 = df2[df2["clean_text_mf"] != ""]

    if len(df1) < initial_df1_rows:
        print(
            f"  Внимание: Удалено {initial_df1_rows - len(df1)} строк из файла Лаборатории из-за отсутствия текста функции."
        )
    if len(df2) < initial_df2_rows:
        print(
            f"  Внимание: Удалено {initial_df2_rows - len(df2)} строк из файла МФ из-за отсутствия текста функции."
        )

    # Apply government body name normalization
    print("Шаг 3/5: Нормализация названий госорганов...")
    df1["lab_normalized_gov_body"] = df1[lab_gov_body_col].apply(
        normalize_gov_body_name
    )
    df2["mf_normalized_gov_body"] = df2[mf_gov_body_col].apply(normalize_gov_body_name)

    # --- DEBUG: Check Gov Body groups for problem strings in BOTH dataframes ---
    print("\n--- Отладка принадлежности проблемных строк к ГО ---")

    # Check for String A in df1
    row_a_df1 = df1[df1["clean_text"] == NORMALIZED_PROBLEM_STRING_A]
    if not row_a_df1.empty:
        gov_body = row_a_df1["lab_normalized_gov_body"].iloc[0]
        print(f"  [DF1] Найдена точная Проблемная строка A. ГО: '{gov_body}'")
    else:
        print("  [DF1] Точная Проблемная строка A не найдена.")

    # Check for String A in df2
    row_a_df2 = df2[df2["clean_text_mf"] == NORMALIZED_PROBLEM_STRING_A]
    if not row_a_df2.empty:
        gov_body = row_a_df2["mf_normalized_gov_body"].iloc[0]
        print(f"  [DF2] Найдена точная Проблемная строка A. ГО: '{gov_body}'")
    else:
        print("  [DF2] Точная Проблемная строка A не найдена.")

    print("--------------------")

    # Check for String B in df1
    rows_b_df1_sub = df1[
        df1["clean_text"].str.contains("государственных услуг в регулируемой сфере")
    ]
    if not rows_b_df1_sub.empty:
        print(
            f"  [DF1] Найдено {len(rows_b_df1_sub)} совпадений по подстроке '...в регулируемой сфере':"
        )
        for index, row in rows_b_df1_sub.iterrows():
            print(
                f"    - Индекс: {index}, ГО: '{row['lab_normalized_gov_body']}', Текст: '{row['clean_text']}'"
            )
    else:
        print("  [DF1] Проблемная строка B (по подстроке) не найдена.")

    # Check for String B in df2
    rows_b_df2_sub = df2[
        df2["clean_text_mf"].str.contains("государственных услуг в регулируемой сфере")
    ]
    if not rows_b_df2_sub.empty:
        print(
            f"  [DF2] Найдено {len(rows_b_df2_sub)} совпадений по подстроке '...в регулируемой сфере':"
        )
        for index, row in rows_b_df2_sub.iterrows():
            print(
                f"    - Индекс: {index}, ГО: '{row['mf_normalized_gov_body']}', Текст: '{row['clean_text_mf']}'"
            )
    else:
        print("  [DF2] Проблемная строка B (по подстроке) не найдена.")

    print("--- Конец отладки ГО ---\n")

    initial_df1_rows = len(df1)
    initial_df2_rows = len(df2)

    # Drop rows where normalized government body name is empty
    df1.dropna(subset=["lab_normalized_gov_body"], inplace=True)
    df2.dropna(subset=["mf_normalized_gov_body"], inplace=True)

    if len(df1) < initial_df1_rows:
        print(
            f"  Внимание: Удалено {initial_df1_rows - len(df1)} строк из файла Лаборатории из-за отсутствия нормализованного имени госоргана."
        )
    if len(df2) < initial_df2_rows:
        print(
            f"  Внимание: Удалено {initial_df2_rows - len(df2)} строк из файла МФ из-за отсутствия нормализованного имени госоргана."
        )

    # --- 3. Matching Process (Grouped by Normalized Government Body) ---
    print("Шаг 4/5: Запуск процесса группового сопоставления...")
    merged_data = []
    FUZZY_MATCH_THRESHOLD = args.threshold

    # Get all unique normalized government bodies from both dataframes
    all_normalized_gov_bodies = (
        pd.concat([df1["lab_normalized_gov_body"], df2["mf_normalized_gov_body"]])
        .dropna()
        .unique()
    )

    globally_matched_mf_indices = set()
    matched_count = 0
    unmatched_df1_count = 0
    unmatched_df2_count = 0

    for gov_body in tqdm(all_normalized_gov_bodies, desc="Обработка госорганов"):
        df1_subset = df1[df1["lab_normalized_gov_body"] == gov_body].copy()
        df2_subset = df2[df2["mf_normalized_gov_body"] == gov_body].copy()

        # Track matches for this subset
        df1_subset["matched_pass"] = 0
        matched_mf_indices_pass1 = set()

        # --- PASS 1: Strict, One-to-One Matching ---
        for index1, row1 in df1_subset.iterrows():
            text_to_find = row1["clean_text"]
            best_match_index = -1
            best_match_score = -1

            # Find best match in available df2 rows
            available_df2_subset = df2_subset[
                ~df2_subset.index.isin(matched_mf_indices_pass1)
            ]
            for index2, row2 in available_df2_subset.iterrows():
                score = fuzz.token_set_ratio(text_to_find, row2["clean_text_mf"])
                if score > best_match_score:
                    best_match_score = score
                    best_match_index = index2

            if best_match_score >= args.threshold and best_match_index != -1:
                row2 = df2_subset.loc[best_match_index]
                temp_row1 = row1.copy()
                temp_row1["match_score"] = best_match_score
                temp_row1["match_pass"] = 1
                combined_row = pd.concat([temp_row1, row2])
                merged_data.append(combined_row)

                df1_subset.loc[index1, "matched_pass"] = 1
                matched_mf_indices_pass1.add(best_match_index)
                globally_matched_mf_indices.add(best_match_index)
                matched_count += 1
                unmatched_lab_indices.discard(index1)

        # --- PASS 2: Lenient, One-to-One Matching (with remaining) ---
        if args.two_pass:
            df1_unmatched_pass1 = df1_subset[df1_subset["matched_pass"] == 0]
            for index1, row1 in df1_unmatched_pass1.iterrows():
                text_to_find = row1["clean_text"]
                best_match_index = -1
                best_match_score = -1

                # Find best match in AVAILABLE df2 rows for this GO
                available_df2_pass2 = df2_subset[
                    ~df2_subset.index.isin(matched_mf_indices_pass1)
                ]
                for index2, row2 in available_df2_pass2.iterrows():
                    score = fuzz.token_set_ratio(text_to_find, row2["clean_text_mf"])
                    if score > best_match_score:
                        best_match_score = score
                        best_match_index = index2

                if (
                    best_match_score >= args.second_pass_threshold
                    and best_match_index != -1
                ):
                    row2 = df2_subset.loc[best_match_index]
                    temp_row1 = row1.copy()
                    temp_row1["match_score"] = best_match_score
                    temp_row1["match_pass"] = 2
                    combined_row = pd.concat([temp_row1, row2])
                    merged_data.append(combined_row)

                    df1_subset.loc[index1, "matched_pass"] = 2
                    matched_mf_indices_pass1.add(
                        best_match_index
                    )  # Prevent re-use in this pass
                    globally_matched_mf_indices.add(best_match_index)
                    matched_count += 1
                    unmatched_lab_indices.discard(index1)

        # --- Add remaining unmatched df1 rows for this subset ---
        final_unmatched_df1 = df1_subset[df1_subset["matched_pass"] == 0]
        for index1, row1 in final_unmatched_df1.iterrows():
            temp_row1 = row1.copy()
            temp_row1["match_score"] = 0
            temp_row1["match_pass"] = 0
            combined_row = pd.concat(
                [temp_row1, pd.Series(index=df2.columns, dtype="object")]
            )
            merged_data.append(combined_row)
            unmatched_df1_count += 1

    # Recalculate the count of unmatched df2 rows correctly
    unmatched_df2_count = len(df2) - len(globally_matched_mf_indices)

    # --- 5b. Add Unmatched Rows from DF2 (for Full Outer Join) ---
    unmatched_mf_indices = set(df2.index) - globally_matched_mf_indices
    if unmatched_mf_indices:
        unmatched_mf_df = df2.loc[list(unmatched_mf_indices)]

        # Prepare the column list for the empty df1 part
        empty_df1_cols = list(df1.columns)
        empty_df1_cols.append("match_score")
        if args.two_pass:
            empty_df1_cols.append("match_pass")

        for index, row2 in unmatched_mf_df.iterrows():
            # Create an empty Series for the df1 part
            empty_df1_part = pd.Series(index=empty_df1_cols, dtype="object")
            empty_df1_part["match_score"] = 0
            if args.two_pass:
                empty_df1_part["match_pass"] = 0

            # Concat with the df2 row
            combined_row = pd.concat([empty_df1_part, row2])
            merged_data.append(combined_row)

    # --- 6. Finalize and Save ---
    print("\nШаг 5/5: Создание и сохранение итоговой таблицы...")
    if merged_data:
        final_df = pd.DataFrame(merged_data).reset_index(drop=True)

        # Bring match score and key text columns to the front
        cols_to_front = [
            "match_score",
            lab_function_col,
            mf_function_col,
            "clean_text",
            "clean_text_mf",
            lab_gov_body_col,
            mf_gov_body_col,
            "lab_normalized_gov_body",
            "mf_normalized_gov_body",
        ]

        # Ensure all cols_to_front actually exist in final_df
        cols_to_front = [col for col in cols_to_front if col in final_df.columns]

        other_cols = [col for col in final_df.columns if col not in cols_to_front]
        final_df = final_df[cols_to_front + other_cols]

        try:
            final_df.to_excel(args.output, index=False)
            print(
                f"Готово! Создан файл '{args.output}'. Всего строк в итоговой таблице: {len(final_df)}"
            )
            print(f"  Найдено совпадений: {matched_count}")
            print(f"  Строк из файла Лаборатории без совпадений: {unmatched_df1_count}")
            print(f"  Строк из файла МФ без совпадений: {unmatched_df2_count}")
        except Exception as e:
            print(f"Ошибка при сохранении итогового файла Excel: {e}")
            sys.exit(1)
    else:
        print(
            "Не удалось создать объединенные данные. Проверьте входные файлы и параметры."
        )
        sys.exit(1)

    print("\n--- Скрипт завершен ---\n")
