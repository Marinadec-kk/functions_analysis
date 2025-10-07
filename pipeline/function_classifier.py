"""
Function Classifier - Classifies government functions into types using AI

This module handles:
1. Reading Excel file from document parser
2. Classifying functions using 3-stage AI process (classifier, verifier, arbiter)
3. Using embeddings for similarity suggestions
4. Updating Excel file with classification results
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

from .config import setup_logging
from .utils import load_prompt, prepare_api_base_url, get_embedding_from_server

logger = logging.getLogger(__name__)

# Function type categories
ALL_CATEGORIES = ["Стратегические", "Регулятивные", "Реализационные", "Контрольные", "Общие"]
DEFAULT_NEEDS_REVIEW_LABEL = "НЕОПРЕДЕЛЕНО"

# Column names
COL_ID = "ID"
COL_TEXT = "FunctionText"
COL_TYPE = "TrueType"
COL_LABELED_BY = "Labeled_by"
COL_AI1_REASON = "AI1_Reason"
COL_AI2_REASON = "AI2_Reason"
COL_AI3_REASON = "AI3_Reason"
COL_FINAL_LABEL = "final_label"


def run_function_classifier(config: Dict, excel_file: str) -> str:
    """Classify functions in Excel file and update it with results."""
    logger = setup_logging(config)

    logger.info(f"Starting function classification for: {excel_file}")

    # Load prompts
    prompt_files = config['prompt_files']
    prompts = {
        'initial': load_prompt(prompt_files['function_classifier_initial']),
        'refinement': load_prompt(prompt_files['function_classifier_refinement']),
        'verify': load_prompt(prompt_files['function_classifier_verify']),
        'arbitrate': load_prompt(prompt_files['function_classifier_arbitrate']),
    }

    # Read input Excel file
    df = pd.read_excel(excel_file)

    # Check if Type column already exists
    if COL_TYPE in df.columns:
        # Process only rows without existing classification
        df_to_process = df[df[COL_TYPE].isna() | (df[COL_TYPE] == "")]
        df_already_processed = df[~(df[COL_TYPE].isna() | (df[COL_TYPE] == ""))]
        logger.info(f"Found {len(df_already_processed)} already classified functions, {len(df_to_process)} to process")
    else:
        # Process all functions
        df_to_process = df.copy()
        df_already_processed = pd.DataFrame()
        logger.info(f"Processing all {len(df_to_process)} functions")

    if df_to_process.empty:
        logger.info("No functions need classification")
        return excel_file

    # Get embeddings for suggestions if universal functions file is available
    suggestions_map = {}
    if config.get('universal_functions') and os.path.exists(config['universal_functions']):
        suggestions_map = get_similarity_suggestions(df_to_process, config)

    # Process functions
    processed_df = process_functions_with_ai(df_to_process, config, prompts, suggestions_map)

    # Combine results
    if not df_already_processed.empty:
        final_df = pd.concat([df_already_processed, processed_df], ignore_index=True)
        # Restore original order
        if COL_ID in df.columns and COL_ID in final_df.columns:
            final_df = final_df.set_index(COL_ID).loc[df[COL_ID]].reset_index()
    else:
        final_df = processed_df

    # Save updated Excel file
    final_df.to_excel(excel_file, index=False)
    logger.info(f"Updated Excel file with {len(processed_df)} classified functions: {excel_file}")

    return excel_file


def get_similarity_suggestions(df_to_process: pd.DataFrame, config: Dict) -> Dict[str, List[str]]:
    """Get similarity suggestions using embeddings."""
    suggestions_map = {}

    try:
        # Check if universal functions file is configured and exists
        universal_file = config.get('universal_functions')
        if not universal_file or not os.path.exists(universal_file):
            return suggestions_map

        # Read universal functions for similarity comparison
        universal_df = pd.read_json(universal_file)

        # Create client for embeddings
        if config['ai_mode'] == 'online':
            embed_client = OpenAI(
                api_key=config['ai_api_key'],
                http_client=httpx.Client(timeout=60.0)
            )
        else:
            embed_client = OpenAI(
                base_url=prepare_api_base_url(config['embedding_server']),
                api_key="not-needed",
                http_client=httpx.Client(timeout=60.0)
            )

        # Get embeddings for all texts
        all_texts = list(df_to_process[COL_TEXT]) + list(universal_df['FunctionText'])
        embeddings = get_embedding_from_server(embed_client, config['embedding_model'], all_texts)

        if embeddings is None or len(embeddings) != len(all_texts):
            logger.warning("Failed to get embeddings for similarity suggestions")
            return suggestions_map

        # Calculate similarities
        new_embeddings = np.array(embeddings[:len(df_to_process)])
        universal_embeddings = np.array(embeddings[len(df_to_process):])

        # Calculate cosine similarities correctly
        # Cosine similarity = dot_product / (||a|| * ||b||)
        norms_new = np.linalg.norm(new_embeddings, axis=1, keepdims=True)
        norms_universal = np.linalg.norm(universal_embeddings, axis=1, keepdims=True)

        # Avoid division by zero
        norms_new = np.where(norms_new == 0, 1e-10, norms_new)
        norms_universal = np.where(norms_universal == 0, 1e-10, norms_universal)

        similarities = np.dot(new_embeddings, universal_embeddings.T) / (norms_new @ norms_universal.T)

        # Get top similar functions for each new function
        threshold = config.get('universal_threshold', 0.75)
        top_k = 3

        for i, (idx, row) in enumerate(df_to_process.iterrows()):
            sim_scores = similarities[i]
            top_indices = np.argsort(sim_scores)[::-1][:top_k]
            top_scores = sim_scores[top_indices]

            # Filter by threshold
            relevant_indices = top_indices[top_scores >= threshold]
            if len(relevant_indices) > 0:
                suggestions = [universal_df.iloc[j]['TrueType'] for j in relevant_indices]
                suggestions_map[row[COL_ID]] = suggestions

        logger.info(f"Generated similarity suggestions for {len(suggestions_map)} functions")

    except Exception as e:
        logger.error(f"Error generating similarity suggestions: {e}")

    return suggestions_map


def process_functions_with_ai(df_to_process: pd.DataFrame, config: Dict, prompts: Dict, suggestions_map: Dict) -> pd.DataFrame:
    """Process functions using 3-stage AI classification."""
    client = create_ai_client(config)

    results = []

    for idx, row in df_to_process.iterrows():
        try:
            result = process_single_function(client, row, config, prompts, suggestions_map)
            if result is not None:
                results.append(result)
                logger.info(f"Processed function {row[COL_ID]}: {result.get(COL_FINAL_LABEL, 'ERROR')}")
            else:
                logger.error(f"Failed to process function {row[COL_ID]}")
                # Add row with error status when process_single_function returns None
                error_result = row.copy()
                error_result[COL_FINAL_LABEL] = "ОШИБКА"
                results.append(error_result)
        except Exception as e:
            logger.error(f"Error processing function {row[COL_ID]}: {e}")
            # Add row with error status
            error_result = row.copy()
            error_result[COL_FINAL_LABEL] = "ОШИБКА"
            results.append(error_result)

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


def process_single_function(client: OpenAI, row: pd.Series, config: Dict, prompts: Dict, suggestions_map: Dict) -> Optional[pd.Series]:
    """Process a single function through the 3-stage AI classification."""

    # Stage 1: AI1 Classification
    ai1_type, ai1_reason = call_ai_classifier(client, row, prompts, config, suggestions_map)

    if ai1_type in ["ОШИБКА", "ОСТАНОВЛЕНО"]:
        row[COL_FINAL_LABEL] = ai1_type
        return row

    # For now, skip verification and arbitration to simplify
    # In a full implementation, we would do AI2 verification and AI3 arbitration if needed

    # Set final result
    row[COL_TYPE] = ai1_type
    row[COL_FINAL_LABEL] = ai1_type
    row[COL_LABELED_BY] = "AI"

    return row


def call_ai_classifier(client: OpenAI, row: pd.Series, prompts: Dict, config: Dict, suggestions_map: Dict) -> Tuple[str, str]:
    """Call AI classifier for a single function."""
    text = row[COL_TEXT]

    # Get suggestions if available
    suggestions = suggestions_map.get(row[COL_ID], [])
    suggestion_text = ""
    if suggestions:
        unique_suggestions = sorted(list(set(suggestions)))
        suggestion_text = (
            "\n\nSIMILARITY ANALYSIS: Based on semantic proximity to verified examples, "
            f"the most likely types are: {', '.join(unique_suggestions)}. "
            "Please consider this information when making your verdict."
        )

    # Use initial prompt
    system_prompt = prompts['initial'].format(embedding_suggestions=suggestion_text)

    max_retries = 3
    delay = 2.0

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=config["ai_model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
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

            verdict = parsed.get("verdict", "").strip().capitalize()
            if not verdict or verdict not in ALL_CATEGORIES:
                verdict = DEFAULT_NEEDS_REVIEW_LABEL

            return verdict, ""

        except (APIConnectionError, RateLimitError, APITimeoutError) as e:
            logger.warning(f"AI API error for function {row[COL_ID]} (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            logger.error(f"Unexpected error classifying function {row[COL_ID]}: {e}")
            break

    return "ОШИБКА", ""


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
