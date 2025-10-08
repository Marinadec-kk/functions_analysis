You are a senior Python developer tasked with creating a new module for analyzing the functions of government bodies based on a detailed technical specification.

Your goal is to adapt the existing scripts from `/home/marinadec/Documents/Obsidian/АГУ/FUNCTIONS/functions_analysis/scripts/hierarchy/` (`hierarchy.py` and `hierarchy_gui.py`) to meet the new requirements. You must preserve the GUI for user configuration, but the core analysis logic must be replaced with the new methodology.

**Primary Instructions:**

1.  **Adopt the persona and constraints** outlined in the attached `prompt_example.md`.
2.  **Implement the new analysis logic** precisely as described below.
3.  **The final output must be a single Python script** that includes a Tkinter GUI. The GUI should allow the user to:
    *   Select the input Excel file.
    *   Select the output directory.
    *   Configure the names of the input columns (`ID`, `Вышестоящий ГО`, `Исполняющий ГО`, `Level`, `FunctionText`).
    *   Set the cosine similarity threshold (defaulting to 0.50).
    *   Start and stop the analysis process.
    *   View progress and logs.
4.  **The script's output must be two separate Excel files**:
    *   **`TABLES_S_ORIGINALNAME+hierarchy.xlsx`**: This is the main report. It must contain all columns from the original input file, plus two new columns: `Ссылается на` and `Реализуется (только для ЦГО)`. `ORIGINALNAME` should be replaced with the name of the input file.
    *   **`log.xlsx`**: This is the technical log file. It must contain all columns from the main report, plus additional columns detailing the analysis for each comparison (e.g., `simScore`, `reason`).
5.  **Integrate the system prompt** from the tech spec directly into your operational logic. The script itself should embody the persona of an "analyst of functions of state bodies."

**Key logical steps to implement:**

1.  **Load** the input Excel file. The file contains the columns: `ID`, `Вышестоящий ГО`, `Исполняющий ГО`, `Level`, `FunctionText`.
2.  **Sort by Level**: Sort the dataframe by the `Level` column in ascending order.
3.  **Preprocess Text**: Apply lowercasing, stop-word removal, and lemmatization to the `FunctionText` column.
4.  **Vectorize**: Use `RuBERT-base` embeddings for the processed text.
5.  **Hierarchical Matching**:
    *   Iterate through the hierarchy from level to level (e.g., from level 2 to level 1, level 3 to level 2, etc.).
    *   For each function at a lower level (e.g., Level `N`), consider it a "child".
    *   Find its potential "parent" functions at the level directly above (Level `N-1`). A potential parent is a function where its `Исполняющий ГО` matches the "child's" `Вышестоящий ГО`.
    *   Calculate the maximum cosine similarity between the "child" function and its potential "parent" functions.
6.  **Record Results**:
    *   If the max similarity is `>= 0.50`, link the "child's" `ID` to the corresponding "parent's" `ID` in the `Ссылается на` column.
    *   Otherwise, fill `Ссылается на` with the literal string `отсутствует`.
7.  **Create Backlinks**: For each "parent" function, compile a semicolon-separated list of all "child" function `ID`s that reference it. This list goes into the `Реализуется (только для ЦГО)` column. This column remains empty for functions that are not parents.
8.  **Save Output**:
    *   Create the `TABLES_S_ORIGINALNAME+hierarchy.xlsx` file with all original columns plus `Ссылается на` and `Реализуется (только для ЦГО)`.
    *   Create the `log.xlsx` file with all columns from the report, plus additional columns for `simScore` and `reason` for each comparison.

**Constraint Checklist & Final Output:**

*   **No Code in Response**: Do not output any Python code directly in your response. The final deliverable is the script file itself.
*   **No Questions**: Do not ask for clarification.
*   **Strict Formatting**: The output Excel files must not contain any special formatting (bold, colors, etc.).
*   **GUI Preservation**: The user interface from `hierarchy_gui.py` should be adapted to control the new logic. Remove any GUI elements that are no longer relevant (e.g., LLM verification settings).

---
**Attached File 1: `hierarchy_tech_spec.md`**
*You have the content of this file, but the new logic described above supersedes it.*

**Attached File 2: `prompt_example.md`**
*You have the content of this file.*
---