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
import numpy as np
from openai import OpenAI, APIConnectionError, RateLimitError, APITimeoutError
import httpx
from tqdm import tqdm

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

# Spheres file column names
SPHERE_NAME_COL = "Название сферы"
SPHERE_DESC_COL = "Описание сферы"
SPHERE_ACTIVITIES_COL = "Виды деятельности"


def load_spheres_data(spheres_file: str) -> List[Dict[str, str]]:
    """Load spheres data from Excel file.
    
    Args:
        spheres_file: Path to SPHERES.xlsx file
        
    Returns:
        List of dictionaries with sphere information
    """
    logger.info(f"Loading spheres data from: {spheres_file}")
    
    if not os.path.exists(spheres_file):
        raise FileNotFoundError(f"Spheres file not found: {spheres_file}")
    
    df = pd.read_excel(spheres_file, dtype=str)
    df.fillna("", inplace=True)
    
    # Validate required columns
    required_cols = [SPHERE_NAME_COL, SPHERE_DESC_COL, SPHERE_ACTIVITIES_COL]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns in spheres file: {', '.join(missing_cols)}")
    
    spheres_list = df.to_dict("records")
    logger.info(f"Loaded {len(spheres_list)} spheres from file")
    
    return spheres_list


def format_spheres_for_prompt(sphere_rows: List[Dict[str, str]]) -> str:
    """Format spheres list for inclusion in LLM prompt.
    
    Args:
        sphere_rows: List of sphere dictionaries
        
    Returns:
        Formatted string with sphere information
    """
    blocks = []
    for item in sphere_rows:
        name = str(item.get(SPHERE_NAME_COL, ""))
        desc = str(item.get(SPHERE_DESC_COL, ""))
        acts = str(item.get(SPHERE_ACTIVITIES_COL, ""))
        blocks.append(
            f"Название сферы: {name}\nОписание: {desc}\nВиды деятельности: {acts}"
        )
    return "\n---\n".join(blocks)


def get_embeddings_batch(client: OpenAI, model: str, texts: List[str]) -> Optional[List[List[float]]]:
    """Get embeddings for a batch of texts.
    
    Args:
        client: OpenAI client
        model: Embedding model name
        texts: List of texts to embed
        
    Returns:
        List of embedding vectors or None on error
    """
    try:
        # Replace empty texts with space to avoid API errors
        valid_texts = [t if t and t.strip() else " " for t in texts]
        response = client.embeddings.create(
            model=model,
            input=valid_texts
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        logger.error(f"Error getting embeddings: {e}")
        return None


def generate_sphere_embeddings(client: OpenAI, spheres_data: List[Dict[str, str]], config: Dict) -> np.ndarray:
    """Generate embeddings for all spheres.
    
    Args:
        client: OpenAI client for embeddings
        spheres_data: List of sphere dictionaries
        config: Configuration dictionary
        
    Returns:
        NumPy array of sphere embeddings
    """
    logger.info(f"Generating embeddings for {len(spheres_data)} spheres...")
    
    # Create corpus: combine name, description, and activities
    sphere_corpus = [
        f"Сфера: {s.get(SPHERE_NAME_COL, '')}. Описание: {s.get(SPHERE_DESC_COL, '')}. Деятельность: {s.get(SPHERE_ACTIVITIES_COL, '')}"
        for s in spheres_data
    ]
    
    embeddings_list = []
    batch_size = config.get('batch_size', 64)
    
    for i in tqdm(range(0, len(sphere_corpus), batch_size), desc="Embedding spheres", unit="batch"):
        batch_texts = sphere_corpus[i:i + batch_size]
        batch_embeds = get_embeddings_batch(client, config['embedding_model'], batch_texts)
        if batch_embeds:
            embeddings_list.extend(batch_embeds)
        else:
            logger.error(f"Failed to get embeddings for batch {i//batch_size + 1}")
            # Fill with zeros to maintain alignment
            embeddings_list.extend([[0.0] * 768] * len(batch_texts))
    
    return np.array(embeddings_list)


def generate_function_embedding(client: OpenAI, function_text: str, config: Dict) -> Optional[np.ndarray]:
    """Generate embedding for a single function.
    
    Args:
        client: OpenAI client for embeddings
        function_text: Function text to embed
        config: Configuration dictionary
        
    Returns:
        NumPy array of function embedding or None on error
    """
    embeds = get_embeddings_batch(client, config['embedding_model'], [function_text])
    if embeds and len(embeds) > 0:
        return np.array(embeds[0])
    return None


def find_top_k_spheres(function_embedding: np.ndarray, sphere_embeddings: np.ndarray, 
                       spheres_data: List[Dict[str, str]], k: int) -> List[Dict[str, str]]:
    """Find top-K most similar spheres using cosine similarity.
    
    Args:
        function_embedding: Embedding vector for the function
        sphere_embeddings: Matrix of sphere embeddings
        spheres_data: List of sphere dictionaries
        k: Number of top spheres to return
        
    Returns:
        List of top-K sphere dictionaries
    """
    # Calculate cosine similarity
    similarities = np.dot(sphere_embeddings, function_embedding) / (
        np.linalg.norm(sphere_embeddings, axis=1) * np.linalg.norm(function_embedding)
    )
    
    # Get indices of top-K most similar
    top_k_indices = np.argsort(similarities)[::-1][:k]
    
    # Return corresponding sphere data
    return [spheres_data[i] for i in top_k_indices]


def add_hierarchy_spheres(df: pd.DataFrame) -> pd.DataFrame:
    """Add Sphere_2 and Sphere_3 columns based on hierarchy.
    
    Creates a 3-level hierarchy from sphere codes:
    - Sphere: Most specific (e.g., "01.1.1 Исполнительные и законодательные органы (CS)")
    - Sphere_2: Intermediate (e.g., "01.1 Исполнительные и законодательные органы...")
    - Sphere_3: Broadest (e.g., "01 Государственные службы общего назначения")
    
    Handles both 3-level codes (01.1.1) and 2-level codes (01.1).
    """
    if COL_SPHERE not in df.columns:
        df[COL_SPHERE] = ""
    
    df_copy = df.copy()
    
    # Extract sphere code from Sphere column (assumes format "CODE NAME" or just "CODE")
    df_copy["temp_code"] = df_copy[COL_SPHERE].astype(str).str.split(" ").str[0]
    
    # Derive parent codes by extracting parts before last dot
    # For "01.1.1" -> Sphere_2_code="01.1", Sphere_3_code="01"
    # For "01.1" -> Sphere_2_code="01.1", Sphere_3_code="01"
    def get_parent_code(code: str, level: int) -> str:
        """Extract parent code at specified level.
        level=2: intermediate (01.1 from 01.1.1)
        level=3: broadest (01 from 01.1.1 or 01.1)
        """
        if not code or code in ['', 'nan', 'None', 'NO_MATCH', 'ОШИБКА', 'ERROR']:
            return ""
        
        parts = code.split('.')
        if level == 3:  # Broadest - just first part
            return parts[0] if parts else ""
        elif level == 2:  # Intermediate - first two parts
            return '.'.join(parts[:2]) if len(parts) >= 2 else code
        return code
    
    df_copy["Sphere_2_code"] = df_copy["temp_code"].apply(lambda x: get_parent_code(x, 2))
    df_copy["Sphere_3_code"] = df_copy["temp_code"].apply(lambda x: get_parent_code(x, 3))
    
    # Look up names from SPHERES_MAP and capitalize
    df_copy[COL_SPHERE_2] = (
        df_copy["Sphere_2_code"]
        .map(SPHERES_MAP)
        .fillna("")
        .astype(str)
        .apply(lambda x: x.capitalize() if x else "")
    )
    df_copy[COL_SPHERE_3] = (
        df_copy["Sphere_3_code"]
        .map(SPHERES_MAP)
        .fillna("")
        .astype(str)
        .apply(lambda x: x.capitalize() if x else "")
    )
    
    # Combine code + capitalized name
    df_copy[COL_SPHERE_2] = df_copy.apply(
        lambda row: f"{row['Sphere_2_code']} {row[COL_SPHERE_2]}"
        if row[COL_SPHERE_2]
        else "",
        axis=1,
    )
    df_copy[COL_SPHERE_3] = df_copy.apply(
        lambda row: f"{row['Sphere_3_code']} {row[COL_SPHERE_3]}"
        if row[COL_SPHERE_3]
        else "",
        axis=1,
    )
    
    return df_copy.drop(columns=["temp_code", "Sphere_2_code", "Sphere_3_code"])


def run_sphere_classifier(config: Dict, excel_file: str) -> str:
    """Classify functions into government spheres and update Excel file."""
    logger = setup_logging(config)

    logger.info(f"Starting sphere classification for: {excel_file}")

    # Load spheres data from file
    spheres_data = load_spheres_data(config['spheres_file'])
    
    # Generate embeddings for spheres (for top-K filtering)
    # Create embedding client
    if config.get('use_local_embeddings', True):
        embed_client = OpenAI(
            base_url=prepare_api_base_url(config.get('embedding_server', '')),
            api_key="not-needed",
            http_client=httpx.Client(timeout=60.0)
        )
    else:
        embed_client = OpenAI(
            api_key=config["ai_api_key"],
            http_client=httpx.Client(timeout=60.0)
        )
    
    sphere_embeddings = generate_sphere_embeddings(embed_client, spheres_data, config)
    logger.info(f"Generated embeddings for {len(spheres_data)} spheres")
    
    # Load prompt
    prompt_path = config['prompt_files']['sphere_classifier']
    prompt = load_prompt(prompt_path)

    # Read input Excel file
    df = pd.read_excel(excel_file)

    # Check if sphere columns already exist - use Sphere_3 as indicator of complete processing
    # This allows re-running to complete partial classifications
    if COL_SPHERE_3 in df.columns:
        # Normalize Sphere_3 column
        df[COL_SPHERE_3] = df[COL_SPHERE_3].astype(str).str.strip()
        df[COL_SPHERE_3].replace(["", "nan", "None"], pd.NA, inplace=True)
        
        # Process only rows without complete hierarchy (Sphere_3 is empty)
        df_to_process = df[df[COL_SPHERE_3].isna()].copy()
        df_already_processed = df[df[COL_SPHERE_3].notna()].copy()
        logger.info(f"Found {len(df_already_processed)} fully classified functions (with Sphere_3), {len(df_to_process)} to process")
    else:
        # Process all functions
        df_to_process = df.copy()
        df_already_processed = pd.DataFrame()
        logger.info(f"Processing all {len(df_to_process)} functions (Sphere_3 column not found)")

    if df_to_process.empty:
        logger.info("No functions need sphere classification")
        return excel_file

    # Process functions
    processed_df = process_functions_for_spheres(
        df_to_process, config, prompt, spheres_data, sphere_embeddings, embed_client
    )

    # Combine results
    logger.info("Combining previously processed and newly processed results...")
    if not df_already_processed.empty:
        combined_df = pd.concat([df_already_processed, processed_df], ignore_index=True)
    else:
        combined_df = processed_df

    # Add hierarchy columns (Sphere_2, Sphere_3) for all rows
    logger.info("Adding hierarchical sphere columns (Sphere_2, Sphere_3)...")
    final_df = add_hierarchy_spheres(combined_df)

    # Restore original order
    if COL_ID in df.columns and COL_ID in final_df.columns:
        try:
            # Remove duplicates if any, keeping last (most recent) entry
            final_df.drop_duplicates(subset=[COL_ID], keep="last", inplace=True)
            final_df = final_df.set_index(COL_ID).loc[df[COL_ID]].reset_index()
            logger.info("Restored original row order")
        except KeyError:
            logger.warning("Could not fully restore original row order")

    # Save updated Excel file
    # Replace NaN values with empty strings to avoid JSON serialization errors
    final_df = final_df.fillna("")
    final_df.to_excel(excel_file, index=False)
    logger.info(f"Updated Excel file with {len(processed_df)} sphere-classified functions: {excel_file}")
    logger.info(f"Total functions in output: {len(final_df)} (with hierarchy columns)")

    return excel_file


def process_functions_for_spheres(df_to_process: pd.DataFrame, config: Dict, prompt: str, 
                                  spheres_data: List[Dict[str, str]], sphere_embeddings: np.ndarray,
                                  embed_client: OpenAI) -> pd.DataFrame:
    """Process functions using AI sphere classification with top-K filtering."""
    client = create_ai_client(config)

    results = []

    for idx, row in tqdm(df_to_process.iterrows(), total=len(df_to_process), desc="Classifying spheres", unit="func"):
        try:
            result = process_single_function_for_sphere(
                client, row, config, prompt, spheres_data, sphere_embeddings, embed_client
            )
            if result is not None:
                results.append(result)
                logger.debug(f"Processed sphere for function {row[COL_ID]}: {result.get(COL_SPHERE, 'ERROR')}")
            else:
                logger.error(f"Failed to process sphere for function {row[COL_ID]}")
                error_result = row.copy()
                error_result[COL_SPHERE] = "ОШИБКА"
                results.append(error_result)
        except Exception as e:
            logger.error(f"Error processing sphere for function {row[COL_ID]}: {e}")
            error_result = row.copy()
            error_result[COL_SPHERE] = "ОШИБКА"
            results.append(error_result)

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


def process_single_function_for_sphere(client: OpenAI, row: pd.Series, config: Dict, prompt: str, 
                                       spheres_data: List[Dict[str, str]], sphere_embeddings: np.ndarray,
                                       embed_client: OpenAI) -> Optional[pd.Series]:
    """Process a single function for sphere classification with top-K filtering."""

    # Generate embedding for the function
    function_text = row[COL_TEXT]
    function_embedding = generate_function_embedding(embed_client, function_text, config)
    
    if function_embedding is None:
        logger.error(f"Failed to generate embedding for function {row[COL_ID]}")
        row[COL_SPHERE] = "ОШИБКА"
        return row
    
    # Find top-K most similar spheres
    top_k = config.get('top_k_spheres')
    top_k_spheres = find_top_k_spheres(function_embedding, sphere_embeddings, spheres_data, top_k)
    
    logger.debug(f"Function {row[COL_ID]}: Selected top-{top_k} candidate spheres")
    
    # Format only top-K spheres for the prompt
    spheres_formatted = format_spheres_for_prompt(top_k_spheres)

    # Create the full prompt with filtered sphere information
    full_prompt = prompt + "\n\nСПИСОК ДОСТУПНЫХ СФЕР:\n---\n" + spheres_formatted + "\n---"

    # Build lookup maps from ALL spheres_data (not just top-K) for validation
    sphere_names_map = {}  # Full name -> Full name (for exact match)
    sphere_code_map = {}   # Code -> Full name
    
    for sphere in spheres_data:
        full_name = sphere.get(SPHERE_NAME_COL, "").strip()
        if full_name:
            sphere_names_map[full_name.lower()] = full_name
            # Extract code (first token before space)
            code = full_name.split()[0] if ' ' in full_name else full_name
            sphere_code_map[code] = full_name

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
                temperature=config['ai_temperature'],
                max_completion_tokens=config['ai_max_tokens'],
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                response_format={"type": "json_object"} if config["ai_mode"] != "Локальный" else None
            )

            content = response.choices[0].message.content
            parsed = parse_json_response(content)

            sphere_name = parsed.get("name", "").strip()
            
            # Check for NO_MATCH
            if sphere_name == "NO_MATCH":
                row[COL_SPHERE] = "NO_MATCH"
                return row
            
            # Try exact match (case-insensitive)
            if sphere_name.lower() in sphere_names_map:
                row[COL_SPHERE] = sphere_names_map[sphere_name.lower()]
                return row
            
            # Try extracting code from response and matching
            if " " in sphere_name:
                potential_code = sphere_name.split()[0].strip()
                if potential_code in sphere_code_map:
                    row[COL_SPHERE] = sphere_code_map[potential_code]
                    return row
            
            # Try matching just the code
            if sphere_name in sphere_code_map:
                row[COL_SPHERE] = sphere_code_map[sphere_name]
                return row
            
            # Fuzzy match: check if sphere_name is contained in any full name
            for full_name_lower, full_name in sphere_names_map.items():
                if sphere_name.lower() in full_name_lower or full_name_lower in sphere_name.lower():
                    row[COL_SPHERE] = full_name
                    logger.info(f"Fuzzy matched '{sphere_name}' to '{full_name}'")
                    return row
            
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
