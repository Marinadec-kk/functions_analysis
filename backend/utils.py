import os
import json
import asyncio
import httpx
import pandas as pd
from typing import List, Dict, Any, Optional, Callable
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    AsyncRetrying,
    before_sleep_log,
)
import logging
from logging.handlers import RotatingFileHandler
import re  # Import for improved filename sanitization

# Import configuration settings using relative import
# Assuming 'backend' is a subpackage of the root package containing 'config.py'
import config

# --- Logging Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Clear existing handlers to prevent duplicate logs if the script is reloaded in interactive environments
# This is important for ensuring logging setup is idempotent.
if logger.handlers:
    for handler in logger.handlers:
        logger.removeHandler(handler)

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(console_handler)

# File Handler with rotation
# Ensure the log directory exists before creating the file handler
log_dir = os.path.dirname(config.LOG_FILE_PATH)
if not os.path.exists(log_dir):
    os.makedirs(
        log_dir, exist_ok=True
    )  # Use exist_ok=True to avoid error if dir already exists

file_handler = RotatingFileHandler(
    config.LOG_FILE_PATH,
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=5,  # Keep up to 5 backup log files
    encoding="utf-8",  # Specify encoding for robustness across different OS
)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logger.addHandler(file_handler)


# This will be set by the main application to allow logging messages to the UI queue.
ui_queue_callback: Optional[Callable[[Dict[str, Any]], None]] = None


def set_ui_queue_callback(callback: Callable[[Dict[str, Any]], None]):
    """Sets a global callback function to send messages to the UI queue."""
    global ui_queue_callback
    ui_queue_callback = callback


def log_message(message: str, level: str = "info", to_ui: bool = True):
    """
    Logs a message to the console, file, and, if configured, to the UI queue.

    Args:
        message (str): The message to log.
        level (str): The logging level ('info', 'warning', 'error', 'debug').
        to_ui (bool): If True, sends the log to the UI queue.
    """
    full_message = f"[{level.upper()}] {message}"
    if level == "info":
        logger.info(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:  # Default to debug for other levels
        logger.debug(message)

    if to_ui and ui_queue_callback:
        # Send a dictionary with type, level, and message to the UI queue
        ui_queue_callback({"type": "log", "level": level, "message": full_message})


def update_status(message: str):
    """
    Updates the status message on the UI, if configured.
    Also logs the status message at debug level.

    Args:
        message (str): The status message to display.
    """
    log_message(
        f"Status: {message}", level="debug"
    )  # Log status as debug to avoid excessive INFO logs
    if ui_queue_callback:
        ui_queue_callback({"type": "status", "message": message})


def update_progress(current: int, total: int, stage: str = ""):
    """
    Updates the progress bar on the UI, if configured.

    Args:
        current (int): The current progress value.
        total (int): The total progress value.
        stage (str): An optional string describing the current stage of progress.
    """
    # Avoid spamming logs with progress updates, only send to UI
    # log_message(f"Progress: {stage} {current}/{total}", level="debug")
    if ui_queue_callback:
        ui_queue_callback(
            {"type": "progress", "current": current, "total": total, "stage": stage}
        )


# --- API Utilities ---
async def call_api_with_backoff(
    url: str,
    method: str = "POST",
    headers: Optional[Dict[str, str]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    data: Optional[Any] = None,
    max_retries: int = config.MAX_RETRIES_API,  # Use from config
    timeout: int = 600,
    is_json_content: bool = False,  # Flag if the payload itself is a JSON string
    backoff_factor: float = config.BACKOFF_FACTOR_API,  # Use from config
) -> Dict[str, Any]:
    """
    Helper function to call API with exponential backoff for retries using httpx.

    Args:
        url (str): The URL to call.
        method (str): HTTP method (e.g., "POST", "GET").
        headers (Optional[Dict[str, str]]): HTTP headers.
        json_data (Optional[Dict[str, Any]]): JSON payload for POST requests.
        data (Optional[Any]): Raw data payload for POST requests.
        max_retries (int): Maximum number of retries for the API call.
        timeout (int): Timeout for the API request in seconds.
        is_json_content (bool): If True, treats `json_data` as an already JSON-encoded string.
        backoff_factor (float): Multiplier for exponential backoff duration.

    Returns:
        Dict[str, Any]: The JSON response from the API.

    Raises:
        httpx.ConnectError: If there's a connection issue.
        httpx.HTTPStatusError: If the API returns a non-2xx status code after retries.
        ValueError: If an unsupported HTTP method is provided.
        Exception: For other unexpected errors.
    """
    async for attempt in AsyncRetrying(
        wait=wait_exponential(multiplier=backoff_factor, min=4, max=10),
        stop=stop_after_attempt(max_retries),
        before_sleep=before_sleep_log(logger, logging.WARNING, exc_info=True),
        reraise=True,  # Re-raise the last exception if all retries fail
    ):
        with attempt:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method.upper() == "POST":
                        if is_json_content and isinstance(json_data, str):
                            # If payload is already a JSON string, send it as content
                            response = await client.post(
                                url, content=json_data, headers=headers
                            )
                        else:
                            # Otherwise, send it as JSON or form data
                            response = await client.post(
                                url, json=json_data, data=data, headers=headers
                            )
                    elif method.upper() == "GET":
                        response = await client.get(url, headers=headers)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")

                    response.raise_for_status()  # Raise an exception for 4xx or 5xx responses
                    return response.json()
            except httpx.ConnectError as e:
                log_message(f"Connection error to API at {url}: {e}", level="error")
                raise  # Re-raise to trigger tenacity retry
            except httpx.HTTPStatusError as e:
                if 400 <= e.response.status_code < 500:
                    log_message(
                        f"Client error from API at {url} (Status: {e.response.status_code}): {e.response.text}",
                        level="error",
                    )
                elif 500 <= e.response.status_code < 600:
                    log_message(
                        f"Server error from API at {url} (Status: {e.response.status_code}): {e.response.text}. Retrying...",
                        level="warning",
                    )
                raise  # Re-raise to trigger tenacity retry
            except Exception as e:
                log_message(
                    f"An unexpected error occurred during API call to {url}: {e}",
                    level="error",
                )
                raise  # Re-raise to trigger tenacity retry


async def call_llm_for_verdict(
    text1: str,
    text2: str,
    prompt_template: str,
    llm_api_base_url: str = config.AI_API_BASE_URL,
    llm_api_key: str = config.AI_API_KEY,
    llm_model: str = config.VERIFICATION_MODEL_NAME,
    api_version: str = config.AI_API_VERSION,
    chat_endpoint: str = config.AI_CHAT_COMPLETION_ENDPOINT,
    max_tokens: int = 100,
    temperature: float = 0.7,
    max_retries: int = config.MAX_RETRIES_API,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Получает вердикт от LLM по двум текстам.
    """
    if not llm_api_key or llm_api_key == "your_ai_api_key_here":
        log_message("API key for LLM is not provided or is a placeholder.", level="error")
        return {"error": "API key not provided or invalid"}

    # Construct the full URL
    full_url = f"{llm_api_base_url.rstrip('/')}/{api_version.strip('/')}{chat_endpoint.strip('/')}"

    full_prompt = prompt_template.format(text1=text1, text2=text2)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {llm_api_key}",
    }
    payload = {
        "model": llm_model,
        "messages": [{"role": "user", "content": full_prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        response_data = await call_api_with_backoff(
            url=full_url,
            method="POST",
            headers=headers,
            json_data=payload,
            max_retries=max_retries,
            timeout=timeout,
        )
        if (
            response_data
            and "choices" in response_data
            and len(response_data["choices"]) > 0
        ):
            return {
                "verdict": response_data["choices"][0]["message"]["content"].strip()
            }
        else:
            log_message(
                f"LLM API call failed or returned unexpected format: {response_data}",
                level="warning",
            )
            return {
                "error": "LLM API call failed or returned unexpected format",
                "details": response_data,
            }
    except Exception as e:
        log_message(f"Error calling LLM API for verification: {e}", level="error")
        return {"error": str(e)}


async def async_get_embedding_batch(
    api_base_url: str,
    texts: List[str],
    api_key: str,
    model: str,
    api_version: str = config.AI_API_VERSION,
    embedding_endpoint: str = config.AI_EMBEDDING_ENDPOINT,
    max_retries: int = config.MAX_RETRIES_API,
    timeout: int = 120,
) -> List[List[float]]:
    """
    Асинхронно получает эмбеддинги для пакета текстов с использованием API.
    """
    if not api_key or api_key == "your_ai_api_key_here":
        log_message(
            "API key for embeddings is not provided or is a placeholder.", level="error"
        )
        return [[] for _ in texts]

    # Construct the full URL for embeddings
    embedding_url = f"{api_base_url.rstrip('/')}/{api_version.strip('/')}{embedding_endpoint.strip('/')}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": texts,
        "model": model,
    }

    try:
        response_data = await call_api_with_backoff(
            url=embedding_url,
            method="POST",
            headers=headers,
            json_data=payload,
            max_retries=max_retries,
            timeout=timeout,
        )

        if (
            response_data
            and "data" in response_data
            and isinstance(response_data["data"], list)
        ):
            # Sort embeddings by index to match the original order of texts
            embeddings_map = {
                item["index"]: item["embedding"] for item in response_data["data"]
            }
            # Create the final list of embeddings in the correct order
            return [embeddings_map.get(i, []) for i in range(len(texts))]
        else:
            log_message(
                f"Embedding API call returned unexpected format: {response_data}",
                level="warning",
            )
            return [[] for _ in texts]

    except Exception as e:
        log_message(f"Error getting embeddings: {e}", level="error")
        return [[] for _ in texts]


# --- Data Reading Utilities ---
def read_table_auto(file_path: str) -> pd.DataFrame:
    """
    Reads a table from a given file path, supporting CSV and Excel.
    Automatically detects file type based on extension.

    Args:
        file_path (str): The path to the file.

    Returns:
        pd.DataFrame: A Pandas DataFrame containing the data.

    Raises:
        FileNotFoundError: If the specified file does not exist.
        ValueError: If the file format is not supported.
        Exception: For other errors during file reading.
    """
    if not os.path.exists(file_path):
        log_message(f"File not found: {file_path}", level="error")
        raise FileNotFoundError(f"File not found: {file_path}")

    _, ext = os.path.splitext(file_path)
    ext = ext.lower()

    try:
        if ext == ".csv":
            return pd.read_csv(file_path)
        elif ext in [".xls", ".xlsx"]:
            return pd.read_excel(file_path)
        else:
            raise ValueError(
                f"Unsupported file format: {ext}. Only CSV and Excel are supported."
            )
    except Exception as e:
        log_message(f"Error reading file {file_path}: {e}", level="error")
        raise


# --- Text Processing Utilities ---
def fast_clean_and_format_text(text: str) -> str:
    """
    A fast function to clean and format text by removing multiple spaces
    and stripping leading/trailing whitespace.

    Args:
        text (str): The input text.

    Returns:
        str: The cleaned and formatted text.
    """
    if not isinstance(text, str):
        return ""
    # Replace multiple spaces with a single space, then strip
    cleaned_text = " ".join(text.split())
    return cleaned_text.strip()


def create_clean_tag(tag: str) -> str:
    """
    Cleans a string to be suitable for use as a tag in markdown.
    Removes special characters, converts to lowercase, replaces spaces with hyphens.

    Args:
        tag (str): The input string to clean.

    Returns:
        str: A cleaned string suitable for a markdown tag.
    """
    if not isinstance(tag, str):
        return ""
    # Convert to lowercase
    cleaned_tag = tag.lower()
    # Replace spaces with hyphens
    cleaned_tag = cleaned_tag.replace(" ", "-")
    # Remove any character that is not alphanumeric or a hyphen
    # This might remove Cyrillic characters if present. Assuming tags are ASCII-like.
    cleaned_tag = "".join(c for c in cleaned_tag if c.isalnum() or c == "-")
    # Remove multiple hyphens (e.g., "--" -> "-")
    cleaned_tag = "-".join(filter(None, cleaned_tag.split("-")))
    return cleaned_tag


def sanitize_filename(filename: str) -> str:
    """
    Sanitizes a string to be a valid filename, removing/replacing invalid characters
    and limiting length.

    Args:
        filename (str): The input string for the filename.

    Returns:
        str: A sanitized string suitable for use as a filename.
    """
    if not isinstance(filename, str):
        return (
            "invalid-filename"  # Return a default safe filename for non-string inputs
        )

    # Define a set of characters that are typically invalid in filenames across common OS
    # Removed space from the regex as it's handled by replace() separately.
    invalid_chars_pattern = r'[<>:"/\\|?*\']'
    # Use re.sub to replace invalid characters with an empty string
    cleaned_filename = re.sub(invalid_chars_pattern, "", filename)

    # Replace spaces with underscores
    cleaned_filename = cleaned_filename.replace(" ", "_")

    # Limit length to prevent issues with file system limits (e.g., 255 chars on Windows)
    # 100 is a safe arbitrary limit.
    if len(cleaned_filename) > 100:
        cleaned_filename = cleaned_filename[:100]

    # Ensure it's not empty after sanitization, provide a fallback
    if not cleaned_filename:
        return "default-filename"

    return cleaned_filename


def _sanitize_json_string(s: Any) -> str:
    """
    Sanitizes a value (string, number, boolean, etc.) to be a valid JSON string literal.
    This means it will correctly escape all necessary characters and enclose the
    result in double quotes if it's a string, or represent as a literal for
    numbers, booleans, None.

    Args:
        s (Any): The value to sanitize into a JSON string literal.

    Returns:
        str: The JSON-encoded string representation of the input value.
    """
    # json.dumps handles all necessary escaping and formatting for any Python object
    # to be a valid JSON string literal.
    return json.dumps(s)
