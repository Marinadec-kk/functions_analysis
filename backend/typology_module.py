import asyncio
import httpx
import json
import logging
import pandas as pd
import numpy as np
import os
from typing import Dict, Any, Optional, List, Callable

from .utils import (
    prepare_api_base_url,
    call_api_with_backoff,
    log_message,
    update_status,
    update_progress,
    async_get_embedding_batch,
    read_table_auto,
)

logger = logging.getLogger(__name__)


class TypologyProcessor:
    """
    Обрабатывает классификацию функций по типам с использованием моделей ИИ.
    Включает многоступенчатый процесс классификации и использование "золотого стандарта" для few-shot learning.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ai_api_base_url = self.config.get("ai_api_base_url")
        self.ai_api_key = self.config.get("ai_api_key")
        self.ai_model_params = self.config.get("ai_model_params", {})
        self.api_max_retries = self.config.get("api_max_retries", 3)
        self.api_timeout = self.config.get("api_timeout", 60)

        if not self.ai_api_base_url or not self.ai_api_key:
            raise ValueError("AI API configuration missing for TypologyProcessor.")

        self.ai_endpoint = prepare_api_base_url(
            base_url=self.ai_api_base_url,
            version=self.config.get("ai_api_version", "v1"),
        )
        self.golden_standard_df = None
        self.golden_standard_embeddings = None

    async def load_and_prepare_golden_standard(self):
        """Загружает золотой стандарт и предварительно вычисляет его эмбеддинги."""
        golden_standard_path = self.config.get("gold_standard_file")
        if not golden_standard_path or not os.path.exists(golden_standard_path):
            log_message(
                "Файл золотого стандарта не найден или не указан. Классификация без few-shot примеров.",
                level="info",
            )
            return

        try:
            log_message(
                f"Загрузка золотого стандарта из {golden_standard_path}", level="info"
            )
            self.golden_standard_df = read_table_auto(golden_standard_path)
            # Проверка на необходимые колонки
            if (
                "text" not in self.golden_standard_df.columns
                or "type" not in self.golden_standard_df.columns
            ):
                log_message(
                    "Файл золотого стандарта должен содержать колонки 'text' и 'type'.",
                    level="error",
                )
                self.golden_standard_df = None
                return

            log_message(
                "Вычисление эмбеддингов для золотого стандарта...", level="info"
            )
            standard_texts = self.golden_standard_df["text"].tolist()
            embeddings_list = await async_get_embedding_batch(
                standard_texts, self.config
            )
            self.golden_standard_embeddings = np.array(embeddings_list)
            log_message(
                f"Эмбеддинги для {len(self.golden_standard_embeddings)} примеров золотого стандарта вычислены.",
                level="info",
            )

        except Exception as e:
            log_message(f"Ошибка при обработке золотого стандарта: {e}", level="error")
            self.golden_standard_df = None
            self.golden_standard_embeddings = None

    def _get_few_shot_examples(self, target_embedding: np.ndarray) -> str:
        """Находит наиболее похожие примеры из золотого стандарта."""
        if self.golden_standard_embeddings is None or self.golden_standard_df is None:
            return ""

        try:
            # Косинусное сходство: 1 - косинусное расстояние
            similarities = 1 - np.array(
                [
                    np.dot(target_embedding, emb.T)
                    / (np.linalg.norm(target_embedding) * np.linalg.norm(emb))
                    for emb in self.golden_standard_embeddings
                ]
            )

            num_examples = self.config.get("num_few_shot_examples", 3)
            # Получаем индексы N самых похожих (наименьшее расстояние)
            closest_indices = np.argsort(similarities)[:num_examples]

            examples = []
            for idx in closest_indices:
                example_row = self.golden_standard_df.iloc[idx]
                examples.append(
                    f"Пример: Текст: '{example_row['text']}' -> Тип: '{example_row['type']}'"
                )

            return "\n".join(examples)
        except Exception as e:
            log_message(f"Ошибка при поиске few-shot примеров: {e}", level="warning")
            return ""

    def _parse_json_verdict(self, ai_response: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if "choices" in ai_response and len(ai_response["choices"]) > 0:
                content = ai_response["choices"][0]["message"]["content"]
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return {"raw_content": content}
            else:
                return {"error": "Unexpected AI response format"}
        except Exception as e:
            return {"error": "ParsingError", "details": str(e)}

    async def _call_ai_for_classification(
        self, prompt: str, stage: str, model: str
    ) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.ai_api_key}",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            **self.ai_model_params,
        }
        try:
            return await call_api_with_backoff(
                url=self.ai_endpoint,
                method="POST",
                headers=headers,
                json_data=payload,
                max_retries=self.api_max_retries,
                timeout=self.api_timeout,
            )
        except Exception as e:
            log_message(f"Ошибка запроса на этапе {stage}: {e}", level="error")
            return {"error": "RequestError", "details": str(e)}

    async def classify_single_function(
        self, function_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        function_name = function_data.get("extracted_go_full_name", "Unknown Function")
        function_description = function_data.get("description", "")

        # 1. Получение эмбеддинга для текущей функции
        try:
            embedding_list = await async_get_embedding_batch(
                [function_description], self.config
            )
            if not embedding_list or not embedding_list[0]:
                raise ValueError("Embedding not returned")
            current_embedding = np.array(embedding_list[0])
            function_data["embeddings"] = embedding_list[0]
        except Exception as e:
            log_message(
                f"Не удалось получить эмбеддинг для '{function_name}': {e}. Классификация пропущена.",
                level="error",
            )
            return {**function_data, "function_type": "ERROR_EMBEDDING"}

        # 2. Получение few-shot примеров
        few_shot_examples = self._get_few_shot_examples(current_embedding)

        # 3. Многоступенчатая классификация
        model_1 = self.config.get("classification_model_1", "gpt-3.5-turbo")
        model_2 = self.config.get("classification_model_2", "gpt-3.5-turbo")
        model_3 = self.config.get("classification_model_3", "gpt-4")

        # Этап 1: Первичная классификация с примерами
        prompt_1_template = self.config.get("prompt_template_typology_1", "...")
        prompt_1 = prompt_1_template.format(
            function_name=function_name,
            function_description=function_description,
            few_shot_examples=few_shot_examples,
        )
        ai_response_1 = await self._call_ai_for_classification(
            prompt_1, "classification", model_1
        )
        verdict_1 = self._parse_json_verdict(ai_response_1)

        # Этап 2: Верификация
        prompt_2_template = self.config.get("prompt_template_typology_2", "...")
        prompt_2 = prompt_2_template.format(
            verdict_1_content=verdict_1.get("raw_content", "N/A"),
            function_name=function_name,
            function_description=function_description,
        )
        ai_response_2 = await self._call_ai_for_classification(
            prompt_2, "verification", model_2
        )
        verdict_2 = self._parse_json_verdict(ai_response_2)

        # Этап 3: Окончательное решение
        prompt_3_template = self.config.get("prompt_template_typology_3", "...")
        prompt_3 = prompt_3_template.format(
            function_description=function_description,
            verdict_1_content=verdict_1.get("raw_content", "N/A"),
            verdict_2_content=verdict_2.get("raw_content", "N/A"),
            function_name=function_name,
        )
        ai_response_3 = await self._call_ai_for_classification(
            prompt_3, "final_decision", model_3
        )
        final_verdict = self._parse_json_verdict(ai_response_3)

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

    async def run(
        self,
        input_df: pd.DataFrame,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> pd.DataFrame:
        if input_df.empty:
            return input_df

        await self.load_and_prepare_golden_standard()

        tasks = [
            self.classify_single_function(row.to_dict())
            for i, row in input_df.iterrows()
        ]
        results = []
        total_tasks = len(tasks)

        for i, task in enumerate(asyncio.as_completed(tasks)):
            result = await task
            results.append(result)
            if progress_callback:
                progress_callback(i + 1, total_tasks, "Классификация типов")

        return pd.DataFrame(results)


async def classify_function_types(
    functions_df: pd.DataFrame,
    config: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    if status_callback:
        status_callback("Начало классификации типов функций...")

    typology_processor = TypologyProcessor(config)

    classified_df = await typology_processor.run(functions_df, progress_callback)

    if status_callback:
        status_callback("Классификация типов функций завершена.")
    return classified_df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # ... (тестовый код остается без изменений, но может потребовать обновления мок-конфига)
