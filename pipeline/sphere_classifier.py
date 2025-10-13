"""
Sphere Classifier - Classifies government functions into spheres using AI

This module handles:
1. Reading Excel file from function classifier
2. Classifying functions into government spheres using AI
3. Using comprehensive sphere hierarchy mapping
4. Updating Excel file with sphere classification results
"""

import os
import re
import json
import time
import logging
from typing import Dict, List, Tuple, Optional, Any

import pandas as pd
from openai import OpenAI, APIConnectionError, RateLimitError, APITimeoutError
import httpx

from .config import setup_logging
from .utils import load_prompt, prepare_api_base_url

logger = logging.getLogger(__name__)

# Government spheres hierarchy mapping
SPHERES_MAP = {
    "01": "ГОСУДАРСТВЕННЫЕ СЛУЖБЫ ОБЩЕГО НАЗНАЧЕНИЯ",
    "01.1": "ИСПОЛНИТЕЛЬНЫЕ И ЗАКОНОДАТЕЛЬНЫЕ ОРГАНЫ, БЮДЖЕТНО-ФИНАНСОВЫЕ ВОПРОСЫ, МЕЖДУНАРОДНЫЕ ОТНОШЕНИЯ",
    "01.2": "ИНОСТРАННАЯ ЭКОНОМИЧЕСКАЯ ПОМОЩЬ",
    "01.3": "ОБЩИЕ СЛУЖБЫ",
    "01.4": "ФУНДАМЕНТАЛЬНЫЕ ИССЛЕДОВАНИЯ",
    "01.5": "НИОКР, СВЯЗАННЫЕ С ГОСУДАРСТВЕННЫМИ СЛУЖБАМИ ОБЩЕГО НАЗНАЧЕНИЯ",
    "01.6": "ГОСУДАРСТВЕННЫЕ СЛУЖБЫ ОБЩЕГО НАЗНАЧЕНИЯ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "01.7": "ОПЕРАЦИИ, СВЯЗАННЫЕ С ГОСУДАРСТВЕННЫМ ДОЛГОМ",
    "01.8": "ТРАНСФЕРТЫ ОБЩЕГО ХАРАКТЕРА МЕЖДУ ОРГАНАМИ ГОСУДАРСТВЕННОГО УПРАВЛЕНИЯ РАЗЛИЧНОГО УРОВНЯ",
    "02": "ОБОРОНА",
    "02.1": "ВООРУЖЕННЫЕ СИЛЫ",
    "02.2": "ГРАЖДАНСКАЯ ОБОРОНА",
    "02.3": "ИНОСТРАННАЯ ВОЕННАЯ ПОМОЩЬ",
    "02.4": "НИОКР В ОБЛАСТИ ОБОРОНЫ",
    "02.5": "ВОПРОСЫ ОБОРОНЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "03": "ОБЩЕСТВЕННЫЙ ПОРЯДОК И БЕЗОПАСНОСТЬ",
    "03.1": "ПОЛИЦЕЙСКИЕ СЛУЖБЫ",
    "03.2": "ПОЖАРНАЯ ОХРАНА",
    "03.3": "СУДЫ",
    "03.4": "ТЮРЬМЫ",
    "03.5": "НИОКР, СВЯЗАННЫЕ С ВОПРОСАМИ ОБЩЕСТВЕННОГО ПОРЯДКА И БЕЗОПАСНОСТИ",
    "03.6": "ВОПРОСЫ ОБЩЕСТВЕННОГО ПОРЯДКА И БЕЗОПАСНОСТИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "04": "ЭКОНОМИЧЕСКИЕ ВОПРОСЫ",
    "04.1": "ОБЩИЕ ЭКОНОМИЧЕСКИЕ И КОММЕРЧЕСКИЕ ВОПРОСЫ И ВОПРОСЫ, ОТНОСЯЩИЕСЯ К РАБОЧЕЙ СИЛЕ",
    "04.2": "СЕЛЬСКОЕ ХОЗЯЙСТВО, ЛЕСНОЕ ХОЗЯЙСТВО, РЫБОЛОВСТВО И ОХОТА",
    "04.3": "ТОПЛИВО И ЭНЕРГЕТИКА",
    "04.4": "ГОРНОДОБЫВАЮЩАЯ ПРОМЫШЛЕННОСТЬ, ОБРАБАТЫВАЮЩАЯ ПРОМЫШЛЕННОСТЬ И СТРОИТЕЛЬСТВО",
    "04.5": "ТРАНСПОРТ",
    "04.6": "СВЯЗЬ",
    "04.7": "ПРОЧИЕ ОТРАСЛИ",
    "04.8": "НИОКР, СВЯЗАННЫЕ С ЭКОНОМИЧЕСКИМИ ВОПРОСАМИ",
    "04.9": "ЭКОНОМИЧЕСКИЕ ВОПРОСЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "05": "ОХРАНА ОКРУЖАЮЩЕЙ СРЕДЫ",
    "05.1": "СБОР И УДАЛЕНИЕ ОТХОДОВ",
    "05.2": "УДАЛЕНИЕ И ОЧИСТКА СТОЧНЫХ ВОД",
    "05.3": "БОРЬБА С ЗАГРЯЗНЕНИЕМ ОКРУЖАЮЩЕЙ СРЕДЫ",
    "05.4": "ЗАЩИТА БИОРАЗНООБРАЗИЯ И ОХРАНА ЛАНДШАФТА",
    "05.5": "НИОКР В ОБЛАСТИ ОХРАНЫ ОКРУЖАЮЩЕЙ СРЕДЫ",
    "05.6": "ВОПРОСЫ ОХРАНЫ ОКРУЖАЮЩЕЙ СРЕДЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "06": "ЖИЛИЩНЫЕ И КОММУНАЛЬНЫЕ УСЛУГИ",
    "06.1": "ЖИЛИЩНОЕ СТРОИТЕЛЬСТВО",
    "06.2": "КОММУНАЛЬНОЕ РАЗВИТИЕ",
    "06.3": "ВОДОСНАБЖЕНИЕ",
    "06.4": "ОСВЕЩЕНИЕ УЛИЦ",
    "06.5": "НИОКР В ОБЛАСТИ ЖИЛИЩНОГО И КОММУНАЛЬНОГО ХОЗЯЙСТВА",
    "06.6": "ЖИЛИЩНЫЕ И КОММУНАЛЬНЫЕ УСЛУГИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "07": "ЗДРАВООХРАНЕНИЕ",
    "07.1": "МЕДИЦИНСКАЯ ПРОДУКЦИЯ, ОБОРУДОВАНИЕ И ИЗДЕЛИЯ, ИСПОЛЬЗУЕМЫЕ В МЕДИЦИНЕ",
    "07.2": "АМБУЛАТОРНЫЕ УСЛУГИ",
    "07.3": "УСЛУГИ БОЛЬНИЦ",
    "07.4": "УСЛУГИ В ОБЛАСТИ ЗДРАВООХРАНЕНИЯ",
    "07.5": "НИОКР В ОБЛАСТИ ЗДРАВООХРАНЕНИЯ",
    "07.6": "ВОПРОСЫ ЗДРАВООХРАНЕНИЯ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "08": "ОТДЫХ, КУЛЬТУРА И РЕЛИГИЯ",
    "08.1": "УСЛУГИ В ОБЛАСТИ ОРГАНИЗАЦИИ ОТДЫХА И ЗАНЯТИЙ СПОРТОМ",
    "08.2": "УСЛУГИ В ОБЛАСТИ КУЛЬТУРЫ",
    "08.3": "УСЛУГИ В ОБЛАСТИ РАДИО- И ТЕЛЕВЕЩАНИЯ И ИЗДАТЕЛЬСКОГО ДЕЛА",
    "08.4": "РЕЛИГИОЗНЫЕ И ДРУГИЕ ОБЩЕСТВЕННЫЕ УСЛУГИ",
    "08.5": "НИОКР В ОБЛАСТИ ОТДЫХА, КУЛЬТУРЫ И РЕЛИГИИ",
    "08.6": "ВОПРОСЫ ОТДЫХА, КУЛЬТУРЫ И РЕЛИГИИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "09": "ОБРАЗОВАНИЕ",
    "09.1": "ДОШКОЛЬНОЕ И НАЧАЛЬНОЕ ОБРАЗОВАНИЕ",
    "09.2": "СРЕДНЕЕ ОБРАЗОВАНИЕ",
    "09.3": "ПРОДОЛЖЕННОЕ СРЕДНЕЕ ОБРАЗОВАНИЕ",
    "09.4": "ВЫСШЕЕ ОБРАЗОВАНИЕ",
    "09.5": "ОБРАЗОВАНИЕ, НЕ ПОДРАЗДЕЛЕННОЕ ПО СТУПЕНЯМ",
    "09.6": "ВСПОМОГАТЕЛЬНЫЕ УСЛУГИ В СИСТЕМЕ ОБРАЗОВАНИЯ",
    "09.7": "НИОКР В ОБЛАСТИ ОБРАЗОВАНИЯ",
    "09.8": "ВОПРОСЫ ОБРАЗОВАНИЯ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "10": "СОЦИАЛЬНАЯ ЗАЩИТА",
    "10.1": "ЗАБОЛЕВАНИЯ И НЕТРУДОСПОСОБНОСТЬ",
    "10.2": "СТАРОСТЬ",
    "10.3": "ИЖДИВЕНЦЫ, ОСТАВШИЕСЯ БЕЗ КОРМИЛЬЦА",
    "10.4": "СЕМЬЯ И ДЕТИ",
    "10.5": "БЕЗРАБОТИЦА",
    "10.6": "ЖИЛЬЕ",
    "10.7": "ВОПРОСЫ СОЦИАЛЬНОЙ НЕУСТРОЕННОСТИ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
    "10.8": "НИОКР В ОБЛАСТИ СОЦИАЛЬНОЙ ЗАЩИТЫ",
    "10.9": "ВОПРОСЫ СОЦИАЛЬНОЙ ЗАЩИТЫ, НЕ ОТНЕСЕННЫЕ К ДРУГИМ КАТЕГОРИЯМ",
}

# Column names
COL_ID = "ID"
COL_TEXT = "FunctionText"
COL_TYPE = "TrueType"
COL_SPHERE = "Sphere"
COL_SPHERE_2 = "Sphere_2"
COL_SPHERE_3 = "Sphere_3"


def run_sphere_classifier(config: Dict, excel_file: str) -> str:
    """Classify functions into government spheres and update Excel file."""
    logger = setup_logging(config)

    logger.info(f"Starting sphere classification for: {excel_file}")

    # Load prompt
    prompt_path = config['prompt_files']['sphere_classifier']
    prompt = load_prompt(prompt_path)

    # Read input Excel file
    df = pd.read_excel(excel_file)

    # Check if sphere columns already exist
    if COL_SPHERE in df.columns:
        # Process only rows without existing sphere classification
        df_to_process = df[df[COL_SPHERE].isna() | (df[COL_SPHERE] == "")]
        df_already_processed = df[~(df[COL_SPHERE].isna() | (df[COL_SPHERE] == ""))]
        logger.info(f"Found {len(df_already_processed)} already classified functions, {len(df_to_process)} to process")
    else:
        # Process all functions
        df_to_process = df.copy()
        df_already_processed = pd.DataFrame()
        logger.info(f"Processing all {len(df_to_process)} functions")

    if df_to_process.empty:
        logger.info("No functions need sphere classification")
        return excel_file

    # Process functions
    processed_df = process_functions_for_spheres(df_to_process, config, prompt)

    # Combine results
    if not df_already_processed.empty:
        final_df = pd.concat([df_already_processed, processed_df], ignore_index=True)
        # Restore original order
        if COL_ID in df.columns and COL_ID in final_df.columns:
            final_df = final_df.set_index(COL_ID).loc[df[COL_ID]].reset_index()
    else:
        final_df = processed_df

    # Save updated Excel file
    # Replace NaN values with empty strings to avoid JSON serialization errors
    final_df = final_df.fillna("")
    final_df.to_excel(excel_file, index=False)
    logger.info(f"Updated Excel file with {len(processed_df)} sphere-classified functions: {excel_file}")

    return excel_file


def process_functions_for_spheres(df_to_process: pd.DataFrame, config: Dict, prompt: str) -> pd.DataFrame:
    """Process functions using AI sphere classification."""
    client = create_ai_client(config)

    results = []

    for idx, row in df_to_process.iterrows():
        try:
            result = process_single_function_for_sphere(client, row, config, prompt)
            if result is not None:
                results.append(result)
                logger.info(f"Processed sphere for function {row[COL_ID]}: {result.get(COL_SPHERE, 'ERROR')}")
            else:
                logger.error(f"Failed to process sphere for function {row[COL_ID]}")
                # Add row with error status when process_single_function_for_sphere returns None
                error_result = row.copy()
                error_result[COL_SPHERE] = "ОШИБКА"
                results.append(error_result)
        except Exception as e:
            logger.error(f"Error processing sphere for function {row[COL_ID]}: {e}")
            # Add row with error status
            error_result = row.copy()
            error_result[COL_SPHERE] = "ОШИБКА"
            results.append(error_result)

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


def process_single_function_for_sphere(client: OpenAI, row: pd.Series, config: Dict, prompt: str) -> Optional[pd.Series]:
    """Process a single function for sphere classification."""

    # Create the spheres list for the prompt
    spheres_list = "\n".join([f"- {code}: {name}" for code, name in SPHERES_MAP.items()])

    # Create the full prompt
    full_prompt = prompt + "\n\nAVAILABLE SPHERES:\n" + spheres_list

    max_retries = 3
    delay = 2.0

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=config["ai_model"],
                messages=[
                    {"role": "system", "content": full_prompt},
                    {"role": "user", "content": row[COL_TEXT]}
                ],
                temperature=1.0,
                max_completion_tokens=4000,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                response_format={"type": "json_object"} if config["ai_mode"] != "Локальный" else None
            )

            content = response.choices[0].message.content
            parsed = parse_json_response(content)

            sphere_name = parsed.get("name", "").strip()
            
            # Helper function to normalize sphere codes (add leading zero if needed)
            def normalize_code(code: str) -> str:
                """Normalize sphere code: '1.1' -> '01.1', '1' -> '01'"""
                if '.' in code:
                    # Handle hierarchical codes like "1.1" or "01.1"
                    # Only normalize the first part (main category)
                    parts = code.split('.')
                    parts[0] = parts[0].zfill(2)  # Only pad first part
                    return '.'.join(parts)
                else:
                    # Handle single-level codes like "1" or "01"
                    return code.zfill(2)
            
            # Extract code if response is in "code: name" or "code name" format
            # First, try splitting by colon
            if ":" in sphere_name:
                potential_code = sphere_name.split(":")[0].strip()
                normalized_code = normalize_code(potential_code)
                if normalized_code in SPHERES_MAP:
                    row[COL_SPHERE] = normalized_code
                    return row
            
            # Try splitting by space and checking first token
            if " " in sphere_name:
                potential_code = sphere_name.split()[0].strip()
                normalized_code = normalize_code(potential_code)
                if normalized_code in SPHERES_MAP:
                    row[COL_SPHERE] = normalized_code
                    return row

            # Try normalizing the sphere_name itself (might be just a code)
            normalized_sphere = normalize_code(sphere_name)
            if normalized_sphere in SPHERES_MAP:
                row[COL_SPHERE] = normalized_sphere
                return row
            
            # Validate sphere code or name exists in our mapping
            if sphere_name and sphere_name in SPHERES_MAP:
                # It's a valid code
                row[COL_SPHERE] = sphere_name
                return row
            elif sphere_name and sphere_name in SPHERES_MAP.values():
                # It's a valid name, convert to code
                for code, name in SPHERES_MAP.items():
                    if name == sphere_name:
                        row[COL_SPHERE] = code
                        break
                return row
            elif sphere_name == "NO_MATCH":
                row[COL_SPHERE] = "NO_MATCH"
                return row
            else:
                logger.warning(f"Invalid sphere name for function {row[COL_ID]}: {sphere_name}")

        except (APIConnectionError, RateLimitError, APITimeoutError) as e:
            logger.warning(f"AI API error for function {row[COL_ID]} (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            logger.error(f"Unexpected error classifying sphere for function {row[COL_ID]}: {e}")
            break

    # If all retries failed, mark as error
    row[COL_SPHERE] = "ОШИБКА"
    return row


def parse_json_response(response_text: str) -> Dict:
    """Parse JSON response from AI."""
    try:
        # Find JSON object in text
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return {}
    except (json.JSONDecodeError, AttributeError):
        return {}


def create_ai_client(config: Dict) -> OpenAI:
    """Create AI client based on configuration."""
    if config["ai_mode"] == "online":
        return OpenAI(
            api_key=config["ai_api_key"],
            http_client=httpx.Client(timeout=60.0)
        )
    elif config["ai_mode"] == "local":
        return OpenAI(
            base_url=prepare_api_base_url(config["embedding_server"]),
            api_key="not-needed",
            http_client=httpx.Client(timeout=60.0)
        )
    else:  # alternative provider mode
        return OpenAI(
            base_url=prepare_api_base_url(config["embedding_server"]),
            api_key=config["ai_api_key"],
            http_client=httpx.Client(timeout=60.0)
        )
