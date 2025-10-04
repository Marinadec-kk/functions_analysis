import asyncio
import httpx
import json
import logging
import pandas as pd
from typing import Dict, Any, Optional, List, Callable

from .utils import (
    prepare_api_base_url,
    call_api_with_backoff,
    log_message,
    update_status,
    update_progress,
    async_get_embedding_batch,  # Import from utils
)
# import config  # No longer needed to import config directly here

logger = logging.getLogger(__name__)


class TypologyProcessor:
    """
    Обрабатывает классификацию функций по типам с использованием моделей ИИ.
    Включает многоступенчатый процесс классификации и получение эмбеддингов.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ai_api_base_url = self.config.get("ai_api_base_url")
        self.ai_api_key = self.config.get("ai_api_key")
        self.ai_model_params = self.config.get("ai_model_params", {})
        self.api_max_retries = self.config.get("api_max_retries", 3)
        self.api_timeout = self.config.get("api_timeout", 60)

        if not self.ai_api_base_url or not self.ai_api_key:
            log_message(
                "AI API base URL or key not configured for TypologyProcessor.",
                level="error",
            )
            raise ValueError("AI API configuration missing for TypologyProcessor.")

        self.ai_endpoint = prepare_api_base_url(
            base_url=self.ai_api_base_url,
            version=self.config.get("ai_api_version", "v1"),
        )
        self.embedding_endpoint = prepare_api_base_url(
            base_url=self.ai_api_base_url,
            version=self.config.get("ai_embeddings_api_version", "v1"),
        )
        self.embedding_model = self.config.get(
            "embedding_model_name", "text-embedding-ada-002"
        )

    def _parse_json_verdict(self, ai_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Парсит JSON-ответ от AI и извлекает вердикт.
        """
        try:
            # Предполагаем, что ответ AI содержит ключ 'verdict' или 'choices'[0]['message']['content']
            if "verdict" in ai_response:
                return ai_response.get("verdict", {})
            elif (
                "choices" in ai_response
                and len(ai_response["choices"]) > 0
                and "message" in ai_response["choices"][0]
            ):
                content = ai_response["choices"][0]["message"]["content"]
                try:
                    # Попытка распарсить content, если это JSON-строка
                    return json.loads(content)
                except json.JSONDecodeError:
                    return {"raw_content": content}  # Возвращаем как есть, если не JSON
            else:
                log_message(
                    f"Неожиданный формат ответа AI: {ai_response}", level="warning"
                )
                return {"error": "Unexpected AI response format"}

        except Exception as e:
            log_message(
                f"Неожиданная ошибка при парсинге вердикта AI: {e}", level="error"
            )
            return {"error": "ParsingError", "details": str(e)}

    async def _call_ai_for_classification(
        self, prompt: str, stage: str, model: str
    ) -> Dict[str, Any]:
        """
        Общая функция для вызова AI на различных стадиях классификации.
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.ai_api_key}",
        }

        # Предполагаем, что API ожидает format: "messages"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            **self.ai_model_params,
        }

        # Примерный URL для классификации: f"{self.ai_endpoint}/chat/completions"
        # Настраивается в зависимости от реального AI API
        classification_url = self.ai_endpoint

        try:
            response = await call_api_with_backoff(
                url=classification_url,
                method="POST",
                headers=headers,
                json_data=payload,
                max_retries=self.api_max_retries,
                timeout=self.api_timeout,
            )
            return response
        except Exception as e:
            log_message(
                f"Ошибка запроса на этапе {stage} AI классификации: {e}", level="error"
            )
            return {"error": "RequestError", "details": str(e)}

    async def classify_single_function(
        self, function_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Выполняет многоступенчатую классификацию одной функции с использованием ИИ.
        """
        function_name = function_data.get(
            "extracted_go_full_name",
            function_data.get("potential_function_name", "Unknown Function"),
        )
        function_description = function_data.get("description", "")

        log_message(
            f"Начало классификации типа для функции: {function_name}", level="info"
        )

        # 0. Получение эмбеддингов
        try:
            embeddings = await async_get_embedding_batch(
                texts=[function_description],
                config_dict=self.config,  # Pass the entire config dictionary
            )
            function_data["embeddings"] = embeddings[0] if embeddings else []
        except Exception as e:
            log_message(
                f"Не удалось получить эмбеддинги для '{function_name}': {e}. Пропускаем классификацию.",
                level="error",
            )
            return {
                **function_data,
                "type_classification": {"verdict": "failed_embeddings"},
            }

        # Используем конкретные модели для каждого этапа, если они указаны в конфиге
        model_1 = self.config.get("classification_model_1", "gpt-3.5-turbo")
        model_2 = self.config.get("classification_model_2", "gpt-3.5-turbo")
        model_3 = self.config.get(
            "classification_model_3", "gpt-4"
        )  # Более мощная модель для финального решения

        # Этап 1: Первичная классификация
        prompt_1 = self.config.get(
            "prompt_template_typology_1",
            "Classify the type of function '{function_name}' based on its description: '{function_description}'. "
            "Consider its context and purpose. Output only the type name.",
        ).format(function_name=function_name, function_description=function_description)

        log_message(
            f"Вызов AI-1 ({model_1}) для классификации функции: {function_name}",
            level="debug",
        )
        ai_response_1 = await self._call_ai_for_classification(
            prompt_1, "classification", model_1
        )
        verdict_1 = self._parse_json_verdict(ai_response_1)
        if verdict_1.get("error"):
            log_message(
                f"Этап 1 классификации провален для {function_name}: {verdict_1}",
                level="error",
            )
            return {
                **function_data,
                "type_classification": {
                    "verdict": "failed_stage_1",
                    "details": verdict_1,
                },
            }

        # Этап 2: Верификация
        prompt_2 = self.config.get(
            "prompt_template_typology_2",
            "Verify the initial classification '{verdict_1_content}' for function '{function_name}'. "
            "Description: '{function_description}'. Confirm or suggest a better type.",
        ).format(
            verdict_1_content=verdict_1.get("raw_content", "N/A"),
            function_name=function_name,
            function_description=function_description,
        )
        log_message(
            f"Вызов AI-2 ({model_2}) для верификации функции: {function_name}",
            level="debug",
        )
        ai_response_2 = await self._call_ai_for_classification(
            prompt_2, "verification", model_2
        )
        verdict_2 = self._parse_json_verdict(ai_response_2)
        if verdict_2.get("error"):
            log_message(
                f"Этап 2 верификации провален для {function_name}: {verdict_2}",
                level="error",
            )
            return {
                **function_data,
                "type_classification": {
                    "verdict": "failed_stage_2",
                    "details": verdict_2,
                },
            }

        # Этап 3: Окончательное решение
        prompt_3 = self.config.get(
            "prompt_template_typology_3",
            "Based on description: '{function_description}' and previous verdicts "
            "'{verdict_1_content}' and '{verdict_2_content}', provide a final and definitive type for function '{function_name}'. "
            "Output only the final type name clearly.",
        ).format(
            function_description=function_description,
            verdict_1_content=verdict_1.get("raw_content", "N/A"),
            verdict_2_content=verdict_2.get("raw_content", "N/A"),
            function_name=function_name,
        )
        log_message(
            f"Вызов AI-3 ({model_3}) для окончательного решения по функции: {function_name}",
            level="debug",
        )
        ai_response_3 = await self._call_ai_for_classification(
            prompt_3, "final_decision", model_3
        )
        final_verdict = self._parse_json_verdict(ai_response_3)
        if final_verdict.get("error"):
            log_message(
                f"Этап 3 окончательного решения провален для {function_name}: {final_verdict}",
                level="error",
            )
            return {
                **function_data,
                "type_classification": {
                    "verdict": "failed_stage_3",
                    "details": final_verdict,
                },
            }

        function_type = final_verdict.get("type") or final_verdict.get(
            "raw_content", "UNKNOWN"
        )
        log_message(
            f"Функция '{function_name}' классифицирована как: {function_type}",
            level="info",
        )
        return {
            **function_data,
            "function_type": function_type,
            "raw_type_verdicts": [verdict_1, verdict_2, final_verdict],
        }

    async def process_typology(
        self,
        input_df: pd.DataFrame,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> pd.DataFrame:
        """
        Основная логика обработки типологии функций для всего набора данных.
        """
        if input_df.empty:
            log_message(
                "Входные данные для типологической обработки пусты.", level="warning"
            )
            return input_df

        results = []
        total_functions = len(input_df)

        update_status("Начало классификации типов функций...")

        # Создаем список задач для асинхронного выполнения
        tasks = []
        for i, row in input_df.iterrows():
            tasks.append(self.classify_single_function(row.to_dict()))

        # Запускаем все задачи параллельно
        classified_functions_list = await asyncio.gather(*tasks)

        # Обновление прогресса после завершения всех задач
        if progress_callback:
            progress_callback(
                total_functions, total_functions, "Классификация типов завершена"
            )

        return pd.DataFrame(classified_functions_list)

    async def run(
        self,
        input_df: pd.DataFrame,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> pd.DataFrame:
        """
        Запускает процесс типологической классификации.
        """
        log_message("Запуск модуля типологической классификации.", level="info")
        classified_df = await self.process_typology(input_df, progress_callback)
        log_message("Модуль типологической классификации завершен.", level="info")
        return classified_df


# Внешняя функция, которую вызывает pipeline (для единообразия с другими модулями)
async def classify_function_types(
    functions_df: pd.DataFrame,
    config: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """
    Основной публичный интерфейс для классификации типов функций.
    """
    if status_callback:
        status_callback("Начало классификации типов функций...")

    typology_processor = TypologyProcessor(config)

    # Обернем progress_callback, чтобы он соответствовал сигнатуре update_progress из utils
    def _progress_wrapper(current: int, total: int, stage_detail: str):
        if progress_callback:
            progress_callback(current, total, f"Классификация типов: {stage_detail}")
        update_progress(current, total, f"Классификация типов: {stage_detail}")

    classified_df = await typology_processor.run(functions_df, _progress_wrapper)

    if status_callback:
        status_callback("Классификация типов функций завершена.")
    return classified_df


if __name__ == "__main__":
    # Пример использования модуля (для автономного тестирования)
    logging.basicConfig(level=logging.INFO)

    # Имитация данных после парсинга
    data = {
        "id": [1, 2, 3],
        "extracted_go_full_name": [
            "pkg.user.Login",
            "pkg.db.Query",
            "pkg.utils.Format",
        ],
        "description": [
            "Handles user authentication and session creation.",
            "Executes a database query and returns results.",
            "Formats text strings according to specified rules.",
        ],
        "original_text": ["func Login() {}", "func Query() {}", "func Format() {}"],
    }
    mock_df = pd.DataFrame(data)

    # Имитация конфигурации (замените на реальные данные при тестировании)
    mock_config = {
        "ai_api_base_url": "http://localhost:8000",  # Пример
        "ai_api_key": "YOUR_OPENAI_API_KEY",  # Замените на ваш API ключ
        "ai_api_version": "v1",
        "ai_embeddings_api_version": "v1",  # Отдельная версия для эмбеддингов
        "embedding_model_name": "text-embedding-ada-002",
        "classification_model_1": "gpt-3.5-turbo",
        "classification_model_2": "gpt-3.5-turbo",
        "classification_model_3": "gpt-4",
        "api_max_retries": 1,  # Уменьшено для быстрого тестирования
        "api_timeout": 30,
        "prompt_template_typology_1": "Classify the primary action or category of function '{function_name}' based on its description: '{function_description}'. Examples: 'Authentication', 'Database', 'Utility', 'Networking'. Output only the most fitting category name.",
        "prompt_template_typology_2": "Review the classification '{verdict_1_content}' for function '{function_name}' with description: '{function_description}'. If it's accurate, state 'CONFIRMED: [Type]'. If not, suggest a refined type. Output clearly.",
        "prompt_template_typology_3": "Considering description '{function_description}' and previous classifications '{verdict_1_content}' and '{verdict_2_content}', what is the single, most precise type for function '{function_name}'? Respond with just the type name.",
    }

    async def run_test():
        print("Запуск автономного теста typology_module...")
        # Для использования utils.log_message и других функций в автономном режиме
        # нужно установить ui_queue_callback
        import queue

        mock_ui_queue = queue.Queue()
        from backend.utils import set_ui_queue_callback

        set_ui_queue_callback(mock_ui_queue.put)

        classified_df = await classify_function_types(
            functions_df=mock_df,
            config=mock_config,
            progress_callback=lambda c, t, s: print(f"Прогресс: {c}/{t} ({s})"),
            status_callback=lambda msg: print(f"Статус: {msg}"),
        )
        print("\n--- Результаты классификации типов ---")
        print(classified_df.head())

        print("\n--- Сообщения из UI очереди (моделирование) ---")
        while not mock_ui_queue.empty():
            print(mock_ui_queue.get())

    # Для запуска асинхронной функции в sync контексте
    asyncio.run(run_test())
