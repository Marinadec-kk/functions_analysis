import pandas as pd
import os
import sys

# --- Set UTF-8 encoding for stdout and stderr ---
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# --- Build absolute paths to the files ---
script_dir = os.path.dirname(os.path.abspath(__file__))
tables_dir = os.path.join(script_dir, '..', 'Таблицы')
input_path = os.path.join(tables_dir, 'Функции сводная.xlsx')
output_file_path = os.path.join(script_dir, '..', 'Прочее', 'output.txt')


try:
    df = pd.read_excel(input_path)
except FileNotFoundError:
    print(f"Error: File not found at {input_path}. Please run the merge_script.py first.")
    sys.exit()
except Exception as e:
    print(f"An error occurred while reading the Excel file: {e}")
    sys.exit()

# The column names for government bodies are likely 'lab_Исполняющий ГО' and 'mf_Госорган'.
# The normalized columns are 'lab_normalized_gov_body' and 'mf_normalized_gov_body'.
# Let's use the normalized columns as they are cleaner.

# Combine the normalized government body columns into one, prioritizing the lab one.
df['normalized_gov_body'] = df['lab_normalized_gov_body'].fillna(df['mf_normalized_gov_body'])

# Filter for ministries
ministry_df = df[df['normalized_gov_body'].str.contains('министерство', na=False)].copy()

if ministry_df.empty:
    print("No functions found for any ministries.")
    sys.exit()

# --- Gather Statistics ---

# 1. Total number of functions for ministries
total_ministry_functions = len(ministry_df)

# 2. Number of functions per ministry
functions_per_ministry = ministry_df['normalized_gov_body'].value_counts()

# 3. Matched vs. Unmatched functions
# From the merge script, match_score >= 90 is a match.
# match_score == 0 and lab function is null means it's only in mf file.
# match_score < 90 and mf function is null means it's only in lab file.

matched_functions = ministry_df[ministry_df['match_score'] >= 90]
unmatched_lab = ministry_df[(ministry_df['mf_Наименование функции'].isna())]
unmatched_mf = ministry_df[(ministry_df['lab_FunctionText'].isna())]


# --- Format and Print Output ---
output = []
output.append("Статистика по функциям министерств:")
output.append("="*40)
output.append(f"Всего найдено функций министерств: {total_ministry_functions}")
output.append(f"Количество сопоставленных функций (>=90% совпадение): {len(matched_functions)}")
output.append(f"Количество функций, найденных только в файле 'от Лаборатории': {len(unmatched_lab)}")
output.append(f"Количество функций, найденных только в файле 'от МФ': {len(unmatched_mf)}")
output.append("\n" + "="*40)
output.append("Количество функций по каждому министерству:")
output.append(functions_per_ministry.to_string())
output.append("\n" + "="*40)

# Save to file and print to console
output_str = "\n".join(output)
try:
    with open(output_file_path, 'w', encoding='utf-8') as f:
        f.write(output_str)
    print(f"Статистика сохранена в файл: {output_file_path}")
except Exception as e:
    print(f"Ошибка при записи в файл: {e}")

print("\n" + output_str)