"""
Collision Detector - Detect and resolve duplicate functions using embeddings and AI

This module handles:
1. Reading Excel file from sphere classifier
2. Finding potentially similar functions using embeddings
3. Using AI to verify if similarities are actual collisions
4. Updating Excel file with collision detection results
"""

import os
import re
import json
import time
import logging
from typing import Dict, List, Tuple, Optional, Set, Any

import pandas as pd
import numpy as np
from openai import OpenAI, APIConnectionError, RateLimitError, APITimeoutError
import httpx

from .config import setup_logging
from .utils import load_prompt, prepare_api_base_url, get_embedding_from_server

logger = logging.getLogger(__name__)

# Column names
COL_ID = "ID"
COL_TEXT = "FunctionText"
COL_EXECUTOR = "Госорган"
COL_TYPE = "TrueType"
COL_SPHERE = "Sphere"
COL_COLLISION_GROUP = "Collision_Group"
COL_COLLISION_VERDICT = "Collision_Verdict"

# Configuration constants
DEFAULT_SIMILARITY_THRESHOLD = 0.50
DEFAULT_BATCH_SIZE = 64


def run_collision_detector(config: Dict, excel_file: str) -> str:
    """Detect collisions in functions and update Excel file."""
    logger = setup_logging(config)

    logger.info(f"Starting collision detection for: {excel_file}")

    # Load prompt
    prompt_path = config['prompt_files']['collision_detector']
    prompt = load_prompt(prompt_path)

    # Read input Excel file
    df = pd.read_excel(excel_file)

    # Check if collision columns already exist
    if COL_COLLISION_GROUP in df.columns:
        # Process only rows without existing collision analysis
        df_to_process = df[df[COL_COLLISION_GROUP].isna() | (df[COL_COLLISION_GROUP] == "")]
        df_already_processed = df[~(df[COL_COLLISION_GROUP].isna() | (df[COL_COLLISION_GROUP] == ""))]
        logger.info(f"Found {len(df_already_processed)} already processed functions, {len(df_to_process)} to process")
    else:
        # Process all functions
        df_to_process = df.copy()
        df_already_processed = pd.DataFrame()
        logger.info(f"Processing all {len(df_to_process)} functions for collisions")

    if df_to_process.empty:
        logger.info("No functions need collision detection")
        return excel_file

    # Find potential collision pairs using embeddings
    collision_pairs = find_collision_candidates(df_to_process, config)

    if not collision_pairs:
        logger.info("No collision candidates found")
        # Mark all functions as processed with no collisions
        for idx, row in df_to_process.iterrows():
            df_to_process.at[idx, COL_COLLISION_GROUP] = "NO_COLLISION"
            df_to_process.at[idx, COL_COLLISION_VERDICT] = "FALSE"
        final_df = pd.concat([df_already_processed, df_to_process], ignore_index=True) if not df_already_processed.empty else df_to_process
        final_df.to_excel(excel_file, index=False)
        return excel_file

    # Verify collisions using AI
    verified_collisions = verify_collisions_with_ai(df_to_process, collision_pairs, config, prompt)

    # Update dataframe with collision results
    df_to_process = update_collision_results(df_to_process, verified_collisions)

    # Combine results
    if not df_already_processed.empty:
        final_df = pd.concat([df_already_processed, df_to_process], ignore_index=True)
        # Restore original order
        if COL_ID in df.columns and COL_ID in final_df.columns:
            final_df = final_df.set_index(COL_ID).loc[df[COL_ID]].reset_index()
    else:
        final_df = df_to_process

    # Save updated Excel file
    final_df.to_excel(excel_file, index=False)
    logger.info(f"Updated Excel file with collision detection for {len(df_to_process)} functions: {excel_file}")

    return excel_file


def find_collision_candidates(df: pd.DataFrame, config: Dict) -> Set[Tuple[str, str]]:
    """Find potential collision pairs using embeddings."""
    collision_pairs = set()

    try:
        # Get embeddings for all functions
        texts = df[COL_TEXT].tolist()
        embeddings = get_embedding_from_server(
            create_ai_client(config),
            config['embedding_model'],
            texts
        )

        if embeddings is None or len(embeddings) != len(texts):
            logger.warning("Failed to get embeddings for collision detection")
            return collision_pairs

        # Calculate similarity matrix
        embeddings_array = np.array(embeddings)
        norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
        # Avoid division by zero
        norms = np.where(norms == 0, 1e-10, norms)

        # Cosine similarity matrix
        similarity_matrix = np.dot(embeddings_array, embeddings_array.T) / (norms @ norms.T)

        # Find pairs with high similarity but different executors
        threshold = config.get('similarity_threshold', DEFAULT_SIMILARITY_THRESHOLD)

        for i in range(len(df)):
            for j in range(i + 1, len(df)):
                if (similarity_matrix[i, j] >= threshold and
                    df.iloc[i][COL_EXECUTOR] != df.iloc[j][COL_EXECUTOR]):
                    pair = tuple(sorted([str(df.iloc[i][COL_ID]), str(df.iloc[j][COL_ID])]))
                    collision_pairs.add(pair)

        logger.info(f"Found {len(collision_pairs)} potential collision pairs")

    except Exception as e:
        logger.error(f"Error finding collision candidates: {e}")

    return collision_pairs


def verify_collisions_with_ai(df: pd.DataFrame, collision_pairs: Set[Tuple[str, str]], config: Dict, prompt: str) -> Dict[Tuple[str, str], str]:
    """Verify collision pairs using AI."""
    verified_collisions = {}

    # Create ID to row mapping
    id_to_row = {row[COL_ID]: row for _, row in df.iterrows()}

    for pair in collision_pairs:
        try:
            id1, id2 = pair
            func1 = id_to_row[id1]
            func2 = id_to_row[id2]

            # Create verification prompt
            verification_prompt = create_collision_verification_prompt(prompt, func1, func2)

            # Call AI for verification
            verdict = call_ai_for_collision_verification(verification_prompt, config)

            verified_collisions[pair] = verdict

            logger.info(f"Verified collision pair {pair}: {verdict}")

        except Exception as e:
            logger.error(f"Error verifying collision pair {pair}: {e}")
            verified_collisions[pair] = "ERROR"

    return verified_collisions


def create_collision_verification_prompt(base_prompt: str, func1: pd.Series, func2: pd.Series) -> str:
    """Create AI prompt for collision verification."""
    return f"""{base_prompt}

FUNCTION 1:
ID: {func1[COL_ID]}
Executor: {func1[COL_EXECUTOR]}
Type: {func1.get(COL_TYPE, 'N/A')}
Sphere: {func1.get(COL_SPHERE, 'N/A')}
Text: {func1[COL_TEXT]}

FUNCTION 2:
ID: {func2[COL_ID]}
Executor: {func2[COL_EXECUTOR]}
Type: {func2.get(COL_TYPE, 'N/A')}
Sphere: {func2.get(COL_SPHERE, 'N/A')}
Text: {func2[COL_TEXT]}

Analyze if these functions represent a REAL collision or FALSE alarm."""


def call_ai_for_collision_verification(prompt: str, config: Dict) -> str:
    """Call AI to verify if a collision is real."""
    client = create_ai_client(config)

    max_retries = 3
    delay = 2.0

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=config["ai_model"],
                messages=[
                    {"role": "system", "content": "You are an expert in detecting administrative collisions in government functions."},
                    {"role": "user", "content": prompt}
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

            verdict = parsed.get("verdict", "ERROR").upper()
            if verdict in ["REAL", "FALSE"]:
                return verdict

        except (APIConnectionError, RateLimitError, APITimeoutError) as e:
            logger.warning(f"AI API error for collision verification (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            logger.error(f"Unexpected error in collision verification: {e}")
            break

    return "ERROR"


def update_collision_results(df: pd.DataFrame, verified_collisions: Dict[Tuple[str, str], str]) -> pd.DataFrame:
    """Update dataframe with collision verification results."""
    # Group functions by collision status
    collision_groups = {}
    next_group_id = 1

    # Initialize collision columns
    df[COL_COLLISION_GROUP] = "NO_COLLISION"
    df[COL_COLLISION_VERDICT] = "FALSE"

    for pair, verdict in verified_collisions.items():
        id1, id2 = pair

        if verdict == "REAL":
            # Create collision group
            if id1 not in collision_groups and id2 not in collision_groups:
                # New collision group
                group_id = f"COLLISION_{next_group_id}"
                next_group_id += 1
                collision_groups[id1] = group_id
                collision_groups[id2] = group_id
            elif id1 in collision_groups:
                # Extend existing group
                group_id = collision_groups[id1]
                collision_groups[id2] = group_id
            elif id2 in collision_groups:
                # Extend existing group
                group_id = collision_groups[id2]
                collision_groups[id1] = group_id

            # Update dataframe
            df.loc[df[COL_ID] == id1, COL_COLLISION_GROUP] = collision_groups[id1]
            df.loc[df[COL_ID] == id2, COL_COLLISION_GROUP] = collision_groups[id2]
            df.loc[df[COL_ID].isin([id1, id2]), COL_COLLISION_VERDICT] = verdict

    return df


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
