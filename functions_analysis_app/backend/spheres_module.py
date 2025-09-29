import pandas as pd
import json
import asyncio
import httpx
from typing import Dict, Any, List, Tuple, Callable, Optional

from .utils import (
    prepare_api_base_url,
    call_api_with_backoff,
    read_table_auto,
    log_message,
    update_status,
    update_progress,
)


# --- Helper Functions (adapted from original 3_spheres.py, removing UI dependencies) ---


def require_cols(df: pd.DataFrame, required_columns: List[str]):
    """
    Ensures that the DataFrame contains all required columns.
    Raises ValueError if any column is missing.
    """
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        log_message(
            f"Отсутствуют необходимые столбцы в DataFrame: {', '.join(missing_cols)}",
            level="error",
        )
        raise ValueError(
            f"Missing required columns in DataFrame: {', '.join(missing_cols)}"
        )


def normalize_name_key(name: str) -> str:
    """Normalizes a name string for use as a key."""
    return name.lower().strip()


def build_sphere_indexes(
    spheres_df: pd.DataFrame,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Builds direct and inverted indexes for spheres based on a DataFrame.

    Args:
        spheres_df (pd.DataFrame): DataFrame containing sphere data with 'id' and 'name' columns.

    Returns:
        Tuple[Dict[str, Any], Dict[str, Any]]: A tuple containing
            - direct_sphere_index (dict): Maps sphere IDs to their full data.
            - inverted_sphere_index (dict): Maps normalized sphere names to their IDs.
    """
    require_cols(spheres_df, ["id", "name"])
    direct_sphere_index = {row["id"]: row for _, row in spheres_df.iterrows()}
    inverted_sphere_index = {
        normalize_name_key(row["name"]): row["id"] for _, row in spheres_df.iterrows()
    }
    return direct_sphere_index, inverted_sphere_index


def format_spheres_for_prompt(direct_sphere_index: Dict[str, Any]) -> str:
    """
    Formats sphere data into a string suitable for an AI prompt.
    """
    formatted_spheres = []
    for sphere_id, sphere_data in direct_sphere_index.items():
        formatted_spheres.append(f"- ID: {sphere_id}, Name: {sphere_data['name']}")
        if "description" in sphere_data:
            formatted_spheres.append(f"  Description: {sphere_data['description']}")
    return "\n".join(formatted_spheres)


def build_prompt(
    function_name: str,
    function_description: str,
    formatted_spheres: str,
    config: Dict[str, Any],
) -> str:
    """
    Constructs the prompt for the AI model to classify a function into a sphere.
    """
    system_prompt_template = config.get("sphere_classification_system_prompt", "")
    user_prompt_template = config.get("sphere_classification_user_prompt", "")

    user_prompt = user_prompt_template.format(
        function_name=function_name,
        function_description=function_description,
        spheres_list=formatted_spheres,
    )

    messages = [{"role": "system", "content": system_prompt_template}]
    messages.append({"role": "user", "content": user_prompt})

    return json.dumps(messages)


def parse_sphere_from_model_output(model_output: str) -> str:
    """
    Parses the sphere name from the AI model's output.
    Assumes the output is a JSON string containing a 'sphere_name' field,
    or a plain string with the sphere name.
    """
    try:
        parsed = json.loads(model_output)
        return str(parsed.get("sphere_name", "")).strip()
    except json.JSONDecodeError:
        # If not JSON, assume it's a plain string with the sphere name
        return model_output.strip()
    except Exception as e:
        log_message(
            f"Error parsing AI model output for sphere: {e}. Output: {model_output[:100]}...",
            level="warning",
        )
        return ""


async def classify_one_async(
    function_data: Dict[str, Any],
    direct_sphere_index: Dict[str, Any],
    inverted_sphere_index: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Classifies a single function into a sphere using the AI model.
    Returns the function_data with added sphere_id, sphere_name, and other relevant info.
    """
    function_name = function_data.get(
        "extracted_go_full_name",
        function_data.get("potential_function_name", "Unknown Function"),
    )
    function_description = function_data.get("description", "")

    if not function_name or not function_description:
        log_message(
            f"Пропускаем классификацию сферы из-за отсутствия имени или описания: {function_data.get('id', 'N/A')}",
            level="warning",
        )
        function_data["sphere_id"] = "N/A"
        function_data["sphere"] = "N/A"
        function_data["sphere_verdict_raw"] = ""
        return function_data

    formatted_spheres = format_spheres_for_prompt(direct_sphere_index)
    prompt_payload_str = build_prompt(
        function_name, function_description, formatted_spheres, config
    )
    prompt_payload = json.loads(prompt_payload_str)  # This should be a list of messages

    ai_api_base = config["ai_api_base_url"]
    api_key = config["ai_api_key"]
    model_name = config.get("sphere_classification_model_name", "gpt-3.5-turbo")
    api_endpoint = prepare_api_base_url(ai_api_base, config.get("ai_api_version", "v1"))

    # Assuming the API endpoint for chat completions is directly the base_url + /chat/completions
    full_api_url = f"{api_endpoint}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    request_payload = {
        "model": model_name,
        "messages": prompt_payload,  # Use the messages list directly
        "max_tokens": config.get("ai_max_tokens", 150),
        "temperature": config.get("ai_temperature", 0.7),
    }

    try:
        response_data = await call_api_with_backoff(
            url=full_api_url,
            method="POST",
            headers=headers,
            json_data=request_payload,  # Send the constructed payload
            max_retries=config.get("api_max_retries", 3),
            timeout=config.get("api_timeout", 60),
        )

        model_output = ""
        if response_data and "choices" in response_data and response_data["choices"]:
            model_output = (
                response_data["choices"][0].get("message", {}).get("content", "")
            )
            parsed_sphere_name = parse_sphere_from_model_output(model_output)
            normalized_parsed_name = normalize_name_key(parsed_sphere_name)

            sphere_id = inverted_sphere_index.get(normalized_parsed_name)
            sphere_name = (
                direct_sphere_index.get(sphere_id, {}).get("name")
                if sphere_id
                else None
            )

            function_data["sphere_id"] = sphere_id if sphere_id else "UNKNOWN_ID"
            function_data["sphere"] = (
                sphere_name if sphere_name else parsed_sphere_name
            )  # Keep parsed name if ID not found
            function_data["sphere_verdict_raw"] = model_output
            if not sphere_id:
                log_message(
                    f"Warning: AI suggested sphere '{parsed_sphere_name}' for '{function_name}' not found in provided sphere list. Assigned 'UNKNOWN_ID'.",
                    level="warning",
                )
        else:
            log_message(
                f"Failed to get AI verdict for function: {function_name}. No AI response or invalid format.",
                level="warning",
            )
            function_data["sphere_id"] = "NO_AI_RESPONSE"
            function_data["sphere"] = "NO_AI_RESPONSE"
            function_data["sphere_verdict_raw"] = ""

    except Exception as e:
        log_message(
            f"Error during AI sphere classification for {function_name}: {e}",
            level="error",
        )
        function_data["sphere_id"] = "ERROR_CLASSIFYING"
        function_data["sphere"] = "ERROR_CLASSIFYING"
        function_data["sphere_verdict_raw"] = str(e)

    return function_data


def add_hierarchy_spheres(
    df: pd.DataFrame, direct_sphere_index: Dict[str, Any]
) -> pd.DataFrame:
    """
    Adds parent sphere IDs and names to the DataFrame based on the Sphere_ID.
    Assumes that the direct_sphere_index contains 'parent_id' for hierarchical spheres.
    """
    if not direct_sphere_index:
        log_message("Индекс сфер пуст, невозможно добавить иерархию.", level="warning")
        df["sphere_parent_id"] = ""
        df["sphere_parent"] = ""
        return df

    # Check if 'parent_id' exists in at least one sphere entry
    if not any(
        "parent_id" in sphere_data for sphere_data in direct_sphere_index.values()
    ):
        log_message(
            "Warning: 'parent_id' not found in sphere index data. Skipping hierarchy addition.",
            level="warning",
        )
        df["sphere_parent_id"] = ""
        df["sphere_parent"] = ""
        return df

    def get_parent_info(sphere_id):
        if sphere_id in direct_sphere_index:
            parent_id = direct_sphere_index[sphere_id].get("parent_id")
            if parent_id and parent_id in direct_sphere_index:
                parent_name = direct_sphere_index[parent_id]["name"]
                return parent_id, parent_name
        return "", ""

    # Ensure 'sphere_id' column exists before applying
    if "sphere_id" not in df.columns:
        log_message(
            "Column 'sphere_id' not found in DataFrame for hierarchy addition.",
            level="error",
        )
        df["sphere_parent_id"] = ""
        df["sphere_parent"] = ""
        return df

    df[["sphere_parent_id", "sphere_parent"]] = df["sphere_id"].apply(
        lambda x: pd.Series(get_parent_info(x))
    )
    return df


# --- Main entry point for the module ---


async def classify_function_spheres(
    functions_df: pd.DataFrame,
    spheres_df: pd.DataFrame,
    config: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """
    Основная функция для запуска конвейера классификации сфер.
    """
    if status_callback:
        status_callback("Начало классификации функций по сферам...")
    log_message("Starting sphere classification...", level="info")

    # Инициализация индексов сфер
    try:
        require_cols(functions_df, ["id", "extracted_go_full_name", "description"])
        require_cols(spheres_df, ["id", "name"])
    except ValueError as e:
        if status_callback:
            status_callback(f"Ошибка в данных для классификации сфер: {e}")
        log_message(f"Ошибка в данных для классификации сфер: {e}", level="error")
        return functions_df  # Возвращаем исходный DF в случае ошибки

    direct_sphere_index, inverted_sphere_index = build_sphere_indexes(spheres_df)
    log_message("Sphere indexes built.", level="info")

    if functions_df.empty:
        log_message("Входной DataFrame для классификации сфер пуст.", level="warning")
        if status_callback:
            status_callback("Классификация сфер: Нет данных для обработки.")
        return functions_df

    total_functions = len(functions_df)
    log_message(f"Классификация {total_functions} функций по сферам.", level="info")

    tasks = []
    for i, row in functions_df.iterrows():
        tasks.append(
            classify_one_async(
                row.to_dict(), direct_sphere_index, inverted_sphere_index, config
            )
        )

    classified_results = []
    for i, task in enumerate(asyncio.as_completed(tasks)):
        result = await task
        classified_results.append(result)
        if progress_callback:
            progress_callback(
                i + 1,
                total_functions,
                f"Классификация сфер: {result.get('extracted_go_full_name', 'N/A')}",
            )
        if (i + 1) % 10 == 0:  # Обновляем статус каждые 10 функций
            if status_callback:
                status_callback(
                    f"Классификация сфер: обработано {i + 1}/{total_functions} функций."
                )

    classified_df = pd.DataFrame(classified_results)
    log_message("Asynchronous sphere classification completed.", level="info")

    # Добавляем информацию об иерархии
    log_message("Adding hierarchical sphere information...", level="info")
    final_df = add_hierarchy_spheres(classified_df, direct_sphere_index)
    log_message("Hierarchical information added.", level="info")

    if status_callback:
        status_callback("Классификация сфер функций завершена.")
    log_message("Sphere classification pipeline finished successfully.", level="info")
    return final_df


if __name__ == "__main__":
    # Пример использования (для автономного тестирования)
    import sys
    import os
    import queue
    import logging

    logging.basicConfig(level=logging.INFO)

    sys.path.append(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    from backend.utils import set_ui_queue_callback

    # Имитация UI очереди для логирования
    mock_ui_queue = queue.Queue()
    set_ui_queue_callback(mock_ui_queue.put)

    # Фиктивные данные для тестирования
    dummy_functions_data = {
        "id": [1, 2, 3, 4],
        "extracted_go_full_name": [
            "pkg.auth.Login",
            "pkg.data.Process",
            "pkg.report.Generate",
            "pkg.util.Helper",
        ],
        "description": [
            "Authenticates user credentials and creates a session.",
            "Processes raw data from sensors and stores it in the database.",
            "Generates monthly financial reports for management.",
            "Provides utility functions for string manipulation.",
        ],
        "function_type": ["Authentication", "Data Processing", "Reporting", "Utility"],
    }
    dummy_functions_df = pd.DataFrame(dummy_functions_data)

    dummy_spheres_data = {
        "id": ["S1", "S2", "S3", "S4", "S5"],
        "name": [
            "User Management",
            "Data Processing",
            "Reporting & Analytics",
            "Core Utilities",
            "Security",
        ],
        "description": [
            "Handles all aspects of user accounts and access.",
            "Involves transformation and storage of raw data.",
            "Focuses on creating and distributing business insights.",
            "General-purpose tools and helper functions.",
            "Measures and protects information systems.",
        ],
        "parent_id": ["", "", "S1", "", "S1"],  # Пример иерархии
    }
    dummy_spheres_df = pd.DataFrame(dummy_spheres_data)

    # Фиктивная конфигурация
    mock_config = {
        "ai_api_base_url": os.getenv(
            "AI_API_BASE_URL", "http://localhost:8000/api"
        ),  # Замените на реальный URL
        "ai_api_key": os.getenv(
            "AI_API_KEY", "YOUR_ACTUAL_AI_API_KEY"
        ),  # Замените на реальный ключ
        "ai_api_version": "v1",
        "sphere_classification_model_name": "gpt-3.5-turbo",
        "ai_max_tokens": 150,
        "ai_temperature": 0.5,
        "api_max_retries": 1,
        "api_timeout": 30,
        "sphere_classification_system_prompt": "You are an expert in classifying software functions into predefined business spheres. Your goal is to identify the most relevant sphere for a given function.",
        "sphere_classification_user_prompt": "Classify the function '{function_name}' with description '{function_description}' into one of the following spheres. Provide only the name of the most fitting sphere.\nAvailable Spheres:\n{spheres_list}",
    }

    async def run_test():
        print("Запуск тестовой функции classify_function_spheres...")
        classified_df = await classify_function_spheres(
            functions_df=dummy_functions_df.copy(),  # Передаем копию, чтобы не изменять исходный
            spheres_df=dummy_spheres_df.copy(),
            config=mock_config,
            progress_callback=lambda c, t, s: print(f"Прогресс: {s} - {c}/{t}"),
            status_callback=lambda msg: print(f"Статус: {msg}"),
        )
        print("\nРезультаты классификации сфер:")
        print(classified_df.head())
        print("\n--- Сообщения из UI очереди (моделирование) ---")
        while not mock_ui_queue.empty():
            print(mock_ui_queue.get())

    asyncio.run(run_test())
    print("Демонстрация spheres_module.py завершена.")
