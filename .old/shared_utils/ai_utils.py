# shared_utils/ai_utils.py
import time
import json
import re
from typing import List, Dict, Optional, Callable

from openai import (
    OpenAI,
    APIConnectionError,
    RateLimitError,
    APITimeoutError,
    APIStatusError,
    AuthenticationError,
)
import httpx


def prepare_api_base_url(url: str) -> str:
    """Готовит URL для OpenAI-совместимого API."""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def create_ai_clients(config: dict) -> List[OpenAI]:
    """Создает список AI клиентов на основе конфигурации."""
    clients = []
    mode = config.get("verification_mode") or config.get("ai_mode")

    if mode == "Онлайн":
        if not config.get("openai_api_key"):
            raise ValueError("Не указан OpenAI API Key для онлайн режима.")
        clients.append(OpenAI(api_key=config["openai_api_key"]))
    elif mode == "Локальная":
        if not config.get("local_servers"):
            raise ValueError("Не указаны адреса локальных серверов.")

        timeout_seconds = config.get(
            "api_timeout", 20.0
        )  # Получаем таймаут из конфига или ставим дефолтный
        timeout = httpx.Timeout(timeout_seconds, connect=5.0)

        for url in config["local_servers"]:
            try:
                client = OpenAI(
                    base_url=prepare_api_base_url(url),
                    api_key="not-needed",
                    http_client=httpx.Client(timeout=timeout),
                )
                clients.append(client)
            except Exception as e:
                print(
                    f"[AI Utils] Ошибка создания клиента для {url}: {e}"
                )  # Используем print, т.к. логгер может быть недоступен

        if not clients:
            raise ConnectionError(
                "Не удалось создать ни одного клиента для локальных серверов."
            )
    else:
        raise ValueError(f"Неизвестный режим работы AI: {mode}")

    return clients


def safe_api_call(
    client: OpenAI, model: str, messages: List[Dict], config: dict, log_callback=print
) -> Optional[Dict]:
    """
    Выполняет безопасный вызов к API с обработкой ошибок и повторными попытками.
    Возвращает распарсенный JSON или None в случае неустранимой ошибки.
    """
    request_params = {
        "model": model,
        "messages": messages,
        "temperature": config.get("ai_temperature", 0.5),
    }
    if config.get("use_json_mode", False) and (
        config.get("verification_mode") == "Онлайн" or config.get("ai_mode") == "Онлайн"
    ):
        request_params["response_format"] = {"type": "json_object"}

    max_retries = config.get("api_retries", 3)
    base_delay = config.get("api_delay", 2)

    for attempt in range(max_retries):
        try:
            log_callback(
                f"Отправка запроса к модели {model} (попытка {attempt + 1}/{max_retries})..."
            )
            response = client.chat.completions.create(**request_params)
            content = response.choices[0].message.content
            log_callback(f"Получен сырой ответ от модели:\n---\n{content}\n---")

            # Надежное извлечение JSON
            match = re.search(r"\{{.*\}}", content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            else:
                log_callback("ПРЕДУПРЕЖДЕНИЕ: Не удалось найти JSON в ответе модели.")
                return None  # Возвращаем None, если JSON не найден

        except AuthenticationError as e:
            error_msg = e.body.get("message") if e.body else str(e)
            log_callback(
                f"КРИТИЧЕСКАЯ ОШИБКА: Ошибка аутентификации. Проверьте API ключ. {error_msg}"
            )
            raise  # Прерываем выполнение полностью
        except (
            APIConnectionError,
            RateLimitError,
            APITimeoutError,
            APIStatusError,
            httpx.TimeoutException,
            httpx.ConnectError,
        ) as e:
            log_callback(
                f"Сетевая ошибка или ошибка API ({type(e).__name__}). Повтор через {base_delay} сек..."
            )
            time.sleep(base_delay)
        except Exception as e:
            log_callback(
                f"Непредвиденная ошибка при вызове API: {e}. Попытка {attempt + 1}/{max_retries}."
            )
            time.sleep(base_delay)

    log_callback(f"Не удалось получить ответ от API после {max_retries} попыток.")
    return None


def _get_one_batch_embeddings(
    client: OpenAI, texts: List[str], model_name: str, retries: int, delay: float
) -> Optional[List[List[float]]]:
    """Получает эмбеддинги для одного пакета с повторными попытками."""
    for attempt in range(retries):
        try:
            # Заменяем пустые строки пробелом, чтобы избежать ошибки API
            valid_texts = [t if t and t.strip() else " " for t in texts]
            resp = client.embeddings.create(model=model_name, input=valid_texts)
            return [item.embedding for item in resp.data]
        except Exception as e:
            print(
                f"[Embeddings] Ошибка в попытке {attempt + 1}/{retries}: {e}. Повтор через {delay} сек."
            )
            time.sleep(delay)
    return None


def get_text_embeddings(
    texts: List[str],
    config: dict,
    log_callback: Callable = print,
    progress_callback: Optional[Callable] = None,
    stop_event: Optional[threading.Event] = None,
) -> List[Optional[List[float]]]:
    """
    Высокоуровневая функция для получения эмбеддингов для списка текстов.
    Управляет созданием клиента, пакетированием, прогрессом и обработкой ошибок.
    """
    log_callback("Запуск процесса получения эмбеддингов...")

    embed_server_url = config.get("embed_server_url") or config.get("embed_server")
    embed_model = config.get("embed_model")
    batch_size = config.get("batch_size", 32)
    api_retries = config.get("api_retries", 3)
    api_delay = config.get("api_delay", 2.0)
    api_timeout = config.get("api_timeout", 60.0)

    if not embed_server_url or not embed_model:
        raise ValueError(
            "Не указан URL сервера или имя модели для эмбеддингов в конфигурации."
        )

    try:
        timeout = httpx.Timeout(api_timeout, connect=5.0)
        client = OpenAI(
            base_url=prepare_api_base_url(embed_server_url),
            api_key="not-needed",
            http_client=httpx.Client(timeout=timeout),
        )
    except Exception as e:
        log_callback(
            f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось создать клиент для сервера эмбеддингов: {e}"
        )
        return [None] * len(texts)

    all_embeddings = []

    if progress_callback:
        progress_callback("reset", len(texts))

    batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]

    for i, batch in enumerate(batches):
        if stop_event and stop_event.is_set():
            log_callback("Процесс получения эмбеддингов прерван.")
            break

        log_callback(
            f"Обработка пакета {i + 1}/{len(batches)} (размер: {len(batch)})..."
        )

        batch_embeddings = _get_one_batch_embeddings(
            client, batch, embed_model, api_retries, api_delay
        )

        if batch_embeddings:
            all_embeddings.extend(batch_embeddings)
        else:
            # Если пакет не удалось обработать, добавляем None для сохранения соответствия индексов
            log_callback(
                f"ПРЕДУПРЕЖДЕНИЕ: Не удалось получить эмбеддинги для пакета {i + 1}."
            )
            all_embeddings.extend([None] * len(batch))

        if progress_callback:
            progress_callback("step", len(batch))

    log_callback("Процесс получения эмбеддингов завершен.")
    return all_embeddings
