"""
Collision Detector - Detect and resolve duplicate functions using embeddings and AI

This module handles:
1. Reading Excel file from sphere classifier
2. Finding potentially similar functions using embeddings
3. Filtering out universal functions to reduce false positives
4. Group-based analysis for efficient collision detection
5. Using AI to verify if similarities are actual collisions
6. Updating Excel file with collision detection results
"""

import os
import re
import json
import time
import logging
from typing import Dict, List, Tuple, Optional, Set, Any
from collections import defaultdict

import pandas as pd
import numpy as np
from openai import OpenAI, APIConnectionError, RateLimitError, APITimeoutError
import httpx
from tqdm import tqdm

from .config import setup_logging
from .utils import load_prompt, prepare_api_base_url, get_embeddings

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
DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD = 0.75
DEFAULT_BATCH_SIZE = 64
DEFAULT_GROUPING_COLUMNS = ["TrueType", "Sphere_3"]


def load_universal_functions(json_path: str) -> List[str]:
    """Load universal function texts from JSON file.
    
    Args:
        json_path: Path to universal functions JSON file
        
    Returns:
        List of universal function texts
    """
    if not json_path or not os.path.isfile(json_path):
        logger.warning(f"Universal functions file not found: {json_path}")
        return []
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        universal_texts = set()
        
        def collect_functions(node: Any):
            """Recursively collect function texts from nested structure."""
            if isinstance(node, dict):
                if 'function' in node and isinstance(node['function'], str):
                    text = node['function'].strip()
                    if text:
                        universal_texts.add(text)
                for value in node.values():
                    collect_functions(value)
            elif isinstance(node, list):
                for item in node:
                    collect_functions(item)
        
        collect_functions(data)
        result = sorted(list(universal_texts))
        logger.info(f"Loaded {len(result)} universal functions from {json_path}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to load universal functions from {json_path}: {e}")
        return []


def filter_universal_functions(df: pd.DataFrame, embeddings: List[List[float]], 
                               universal_embeddings: List[List[float]], 
                               threshold: float) -> pd.DataFrame:
    """Filter out functions that match universal functions.
    
    Args:
        df: DataFrame with functions
        embeddings: Embeddings for df functions
        universal_embeddings: Embeddings for universal functions
        threshold: Similarity threshold for universal match
        
    Returns:
        Filtered DataFrame (non-universal functions only)
    """
    if not universal_embeddings or len(embeddings) == 0:
        return df
    
    try:
        # Calculate similarity between each function and all universal functions
        func_embeds = np.array(embeddings)
        univ_embeds = np.array(universal_embeddings)
        
        # Normalize embeddings
        func_norms = np.linalg.norm(func_embeds, axis=1, keepdims=True)
        univ_norms = np.linalg.norm(univ_embeds, axis=1, keepdims=True)
        
        func_embeds_norm = func_embeds / np.where(func_norms == 0, 1e-10, func_norms)
        univ_embeds_norm = univ_embeds / np.where(univ_norms == 0, 1e-10, univ_norms)
        
        # Cosine similarity: func x universal
        similarities = np.dot(func_embeds_norm, univ_embeds_norm.T)
        
        # Find max similarity to any universal function
        max_similarities = np.max(similarities, axis=1)
        
        # Keep only functions below universal threshold
        non_universal_mask = max_similarities < threshold
        
        original_count = len(df)
        filtered_df = df[non_universal_mask].copy()
        filtered_count = len(filtered_df)
        
        logger.info(f"Universal function filter: {original_count - filtered_count} functions filtered out, {filtered_count} remaining")
        
        return filtered_df
        
    except Exception as e:
        logger.error(f"Error filtering universal functions: {e}")
        return df


def run_collision_detector(config: Dict, excel_file: str) -> str:
    """Detect collisions in functions and update Excel file."""
    logger = setup_logging(config)

    logger.info(f"Starting collision detection for: {excel_file}")

    # Load universal functions
    universal_texts = load_universal_functions(config.get('universal_functions', ''))
    universal_embeddings = []
    if universal_texts:
        logger.info(f"Generating embeddings for {len(universal_texts)} universal functions...")
        universal_embeddings = get_embeddings(config, universal_texts, logger)
        if universal_embeddings:
            logger.info(f"Generated {len(universal_embeddings)} universal function embeddings")
    
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

    # Find potential collision pairs using embeddings (with grouping and universal filtering)
    collision_pairs = find_collision_candidates(df_to_process, config, universal_embeddings)

    if not collision_pairs:
        logger.info("No collision candidates found")
        # Mark all functions as processed with no collisions
        for idx, row in df_to_process.iterrows():
            df_to_process.at[idx, COL_COLLISION_GROUP] = "NO_COLLISION"
            df_to_process.at[idx, COL_COLLISION_VERDICT] = "FALSE"
        final_df = pd.concat([df_already_processed, df_to_process], ignore_index=True) if not df_already_processed.empty else df_to_process
        final_df = final_df.fillna("")
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
    # Replace NaN values with empty strings to avoid JSON serialization errors
    final_df = final_df.fillna("")
    final_df.to_excel(excel_file, index=False)
    logger.info(f"Updated Excel file with collision detection for {len(df_to_process)} functions: {excel_file}")

    return excel_file


def find_collision_candidates(df: pd.DataFrame, config: Dict, universal_embeddings: List[List[float]] = None) -> Set[Tuple[str, str]]:
    """Find potential collision pairs using embeddings with grouping and universal filtering.
    
    Args:
        df: DataFrame with functions to analyze
        config: Configuration dictionary
        universal_embeddings: Optional list of universal function embeddings for filtering
        
    Returns:
        Set of collision pairs (tuples of sorted IDs)
    """
    all_collision_pairs = set()
    
    # Get grouping columns from config
    grouping_cols = config.get('grouping_columns', DEFAULT_GROUPING_COLUMNS)
    
    # Filter grouping columns to only those that exist in DataFrame
    grouping_cols = [col for col in grouping_cols if col in df.columns]
    
    if not grouping_cols:
        logger.warning("No valid grouping columns found, analyzing all functions together")
        # Analyze all functions as one group
        return _find_candidates_in_group(df, config, universal_embeddings, "All Functions")
    
    # Group data and analyze each group separately
    logger.info(f"Grouping functions by: {', '.join(grouping_cols)}")
    grouped = df.groupby(grouping_cols, dropna=False)
    total_groups = grouped.ngroups
    logger.info(f"Found {total_groups} groups to analyze")
    
    for group_idx, (group_name, group_df) in enumerate(tqdm(grouped, desc="Analyzing groups", unit="group"), 1):
        if isinstance(group_name, tuple):
            group_display = ", ".join([f"{col}={val}" for col, val in zip(grouping_cols, group_name)])
        else:
            group_display = str(group_name)
        
        # Filter out "Общие функции" type within each group
        if COL_TYPE in group_df.columns:
            group_df_filtered = group_df[group_df[COL_TYPE] != "Общие функции"].copy()
            if len(group_df_filtered) < len(group_df):
                logger.debug(f"Group '{group_display}': Filtered out {len(group_df) - len(group_df_filtered)} 'Общие функции'")
        else:
            group_df_filtered = group_df.copy()
        
        # Skip groups with less than 2 functions
        if len(group_df_filtered) < 2:
            logger.debug(f"Group '{group_display}': Skipped (only {len(group_df_filtered)} functions)")
            continue
        
        # Find candidates in this group
        group_pairs = _find_candidates_in_group(group_df_filtered, config, universal_embeddings, group_display)
        
        if group_pairs:
            logger.info(f"Group '{group_display}': Found {len(group_pairs)} collision candidates")
            all_collision_pairs.update(group_pairs)
    
    logger.info(f"Total collision candidates found across all groups: {len(all_collision_pairs)}")
    return all_collision_pairs


def _find_candidates_in_group(group_df: pd.DataFrame, config: Dict, 
                               universal_embeddings: List[List[float]], 
                               group_name: str) -> Set[Tuple[str, str]]:
    """Find collision candidates within a single group.
    
    Args:
        group_df: DataFrame with functions in this group
        config: Configuration dictionary
        universal_embeddings: Optional list of universal function embeddings
        group_name: Display name for this group (for logging)
        
    Returns:
        Set of collision pairs for this group
    """
    collision_pairs = set()
    
    try:
        # Get embeddings for functions in this group
        texts = group_df[COL_TEXT].fillna("").tolist()
        embeddings = get_embeddings(config, texts, logger)
        
        if embeddings is None or len(embeddings) != len(texts):
            logger.warning(f"Group '{group_name}': Failed to get embeddings")
            return collision_pairs
        
        # Filter out universal functions if threshold is set
        universal_threshold = config.get('universal_threshold', DEFAULT_UNIVERSAL_SIMILARITY_THRESHOLD)
        if universal_embeddings and universal_threshold:
            original_len = len(group_df)
            group_df_filtered = filter_universal_functions(
                group_df.reset_index(drop=True), 
                embeddings, 
                universal_embeddings, 
                universal_threshold
            )
            
            if len(group_df_filtered) < original_len:
                logger.debug(f"Group '{group_name}': {original_len - len(group_df_filtered)} universal functions filtered")
                
                # Get embeddings only for non-universal functions
                # Re-index to align with filtered dataframe
                filtered_indices = group_df_filtered.index.tolist()
                embeddings = [embeddings[i] for i in filtered_indices]
                group_df = group_df_filtered
        
        if len(group_df) < 2:
            logger.debug(f"Group '{group_name}': < 2 functions remain after filtering")
            return collision_pairs
        
        # Calculate similarity matrix
        embeddings_array = np.array(embeddings)
        norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-10, norms)
        
        # Cosine similarity matrix
        similarity_matrix = np.dot(embeddings_array, embeddings_array.T) / (norms @ norms.T)
        
        # Find pairs with high similarity but different executors
        threshold = config.get('similarity_threshold', DEFAULT_SIMILARITY_THRESHOLD)
        
        group_df_reset = group_df.reset_index(drop=True)
        for i in range(len(group_df_reset)):
            for j in range(i + 1, len(group_df_reset)):
                exec_i = group_df_reset.iloc[i][COL_EXECUTOR]
                exec_j = group_df_reset.iloc[j][COL_EXECUTOR]
                
                # Only consider pairs from different executors
                if (similarity_matrix[i, j] >= threshold and exec_i != exec_j):
                    id_i = str(group_df_reset.iloc[i][COL_ID])
                    id_j = str(group_df_reset.iloc[j][COL_ID])
                    pair = tuple(sorted([id_i, id_j]))
                    collision_pairs.add(pair)
        
    except Exception as e:
        logger.error(f"Error finding collision candidates in group '{group_name}': {e}")
    
    return collision_pairs


def verify_collisions_with_ai(df: pd.DataFrame, collision_pairs: Set[Tuple[str, str]], config: Dict, prompt: str) -> Dict[Tuple[str, str], str]:
    """Verify collision pairs using AI."""
    verified_collisions = {}

    # Create ID to row mapping
    id_to_row = {row[COL_ID]: row for _, row in df.iterrows()}

    for pair in tqdm(collision_pairs, desc="Verifying collisions", unit="pair"):
        try:
            id1, id2 = pair
            func1 = id_to_row[id1]
            func2 = id_to_row[id2]

            # Create verification prompt
            verification_prompt = create_collision_verification_prompt(prompt, func1, func2)

            # Call AI for verification
            verdict = call_ai_for_collision_verification(verification_prompt, config)

            verified_collisions[pair] = verdict

            logger.debug(f"Verified collision pair {pair}: {verdict}")

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
                temperature=config['ai_temperature'],
                max_completion_tokens=config['ai_max_tokens'],
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
