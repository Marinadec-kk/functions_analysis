"""
Document Parser - Extracts functions and government hierarchy from documents

This module handles:
1. Reading .docx and .doc files
2. Extracting government body hierarchy using AI
3. Parsing function sections from documents
4. Creating initial Excel matrix with ID, FunctionText, Госорган, Вышестоящий
"""

import os
import re
import json
import time
import logging
from typing import Dict, List, Tuple, Optional

import pandas as pd
from docx import Document
from openai import OpenAI, APIConnectionError, RateLimitError, APITimeoutError
import httpx

from .config import setup_logging
from .utils import load_prompt, prepare_api_base_url

logger = logging.getLogger(__name__)


def run_document_parser(config: Dict) -> str:
    """Parse documents and create initial Excel file."""
    logger = setup_logging(config)

    # Load prompt
    prompt_path = config['prompt_files']['document_parser_extract']
    prompt = load_prompt(prompt_path)

    # Find all documents in input folder
    input_folder = config['input_folder']
    documents = find_documents(input_folder)

    if not documents:
        raise ValueError(f"No documents found in {input_folder}")

    logger.info(f"Found {len(documents)} documents to process")

    # Process documents
    all_results = []
    for doc_path in documents:
        try:
            result = process_document(doc_path, prompt, config)
            if result:
                all_results.extend(result)  # Extend with the list of function dictionaries
        except Exception as e:
            logger.error(f"Failed to process {doc_path}: {e}")
            continue

    if not all_results:
        raise ValueError("No documents were successfully processed")

    # Create Excel file
    df = pd.DataFrame(all_results)
    output_path = config['output_excel']
    df.to_excel(output_path, index=False)

    logger.info(f"Created Excel file with {len(all_results)} functions: {output_path}")
    return output_path


def find_documents(folder_path: str) -> List[str]:
    """Find all .docx and .doc files in folder."""
    documents = []

    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.startswith(("~$", "~", ".")):
                continue

            filepath = os.path.join(root, file)
            lower_name = file.lower()

            if lower_name.endswith((".docx", ".doc")):
                documents.append(filepath)

    return documents


def process_document(doc_path: str, prompt: str, config: Dict) -> Optional[Dict]:
    """Process a single document and extract functions."""
    filename = os.path.basename(doc_path)
    logger.info(f"Processing: {filename}")

    try:
        # Read document paragraphs
        paragraphs = read_document_paragraphs(doc_path)

        # Extract government body name
        full_go_name = extract_government_body_name(paragraphs, filename, prompt, config)
        if not full_go_name:
            logger.warning(f"Could not extract government body name from {filename}")
            return None

        # Parse hierarchy
        go_data = parse_government_hierarchy(full_go_name)

        # Extract functions
        functions, status = extract_functions(paragraphs)
        if status != "OK":
            logger.warning(f"No functions found in {filename}: {status}")
            return None

        # Create abbreviation for ID
        go_abbrev = create_government_abbreviation(go_data['main_go'])

        # Create result entries
        results = []
        for i, func_text in enumerate(functions, 1):
            func_id = f"{go_abbrev}-{i}"

            results.append({
                'ID': func_id,
                'FunctionText': func_text,
                'Госорган': go_data['main_go'],
                'Вышестоящий': go_data.get('parent_go', ''),
            })

        logger.info(f"Extracted {len(results)} functions from {filename}")
        return results

    except Exception as e:
        logger.error(f"Error processing {filename}: {e}")
        return None


def read_document_paragraphs(doc_path: str) -> List[str]:
    """Read paragraphs from .docx or .doc file."""
    if doc_path.lower().endswith('.docx'):
        doc = Document(doc_path)
        return [p.text for p in doc.paragraphs]
    else:
        # For .doc files, we would need win32com, but since we're removing pywin32,
        # we'll skip .doc support for now or raise an appropriate error
        raise ValueError(f"Unsupported document format: {doc_path}. Only .docx files are supported.")


def extract_government_body_name(paragraphs: List[str], filename: str, prompt: str, config: Dict) -> Optional[str]:
    """Extract government body name using AI."""
    context_paragraphs = paragraphs[:150]
    full_text = "\n".join(p.strip() for p in context_paragraphs)

    if not full_text.strip():
        logger.error(f"No text found for analysis in {filename}")
        return None

    # Create AI client
    client = create_ai_client(config)

    max_retries = 3
    delay = 2.0

    for attempt in range(max_retries):
        try:
            api_args = {
                "model": config["ai_model"],
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": full_text},
                ],
                "temperature": 1.0,
                "max_completion_tokens": 4000,
                "top_p": 1,
                "frequency_penalty": 0,
                "presence_penalty": 0,
            }

            if config["ai_mode"] != "Локальный":
                api_args["response_format"] = {"type": "json_object"}

            logger.debug(f"Sending AI request for {filename} (attempt {attempt + 1}/{max_retries})")
            response = client.chat.completions.create(**api_args)

            content = response.choices[0].message.content
            logger.debug(f"AI response for {filename}: {content}")

            # Parse JSON response
            clean_content = sanitize_json_string(content)
            data = json.loads(clean_content)
            full_go_name = data.get("full_go_name")

            if full_go_name and isinstance(full_go_name, str):
                return full_go_name.strip()
            else:
                logger.warning(f"Invalid response format for {filename}")

        except (APIConnectionError, RateLimitError, APITimeoutError) as e:
            logger.warning(f"AI API error for {filename} (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            logger.error(f"Unexpected error extracting GO name from {filename}: {e}")
            break

    return None


def parse_government_hierarchy(full_name: str) -> Dict[str, Optional[str]]:
    """Parse full government name into main and parent organizations."""
    if not full_name:
        return {"main_go": None, "parent_go": None}

    # Keywords that indicate parent organization
    parent_keywords = [
        "Министерства", "Агентства", "Комитета", "Департамента", "Управления"
    ]

    normalized_name = " ".join(full_name.split())

    for keyword in parent_keywords:
        match = re.search(r"\b" + re.escape(keyword) + r"\b", normalized_name, re.IGNORECASE)
        if match:
            split_index = match.start()

            main_go = normalized_name[:split_index].strip()
            parent_part = normalized_name[split_index:].strip()

            # Convert to nominative case
            parent_go = convert_to_nominative(parent_part)

            if not main_go:
                return {"main_go": parent_go.strip(), "parent_go": None}

            return {"main_go": main_go, "parent_go": parent_go.strip()}

    return {"main_go": normalized_name, "parent_go": None}


def convert_to_nominative(word: str) -> str:
    """Convert word to nominative case (simple heuristic)."""
    if word.lower().endswith("а"):
        return word[:-1]
    elif word.lower().endswith("я"):
        return word[:-1] + "е"
    return word


def extract_functions(paragraphs: List[str]) -> Tuple[List[str], str]:
    """Extract function texts from document."""
    in_section = False
    functions = []

    for raw in paragraphs:
        if not raw:
            continue

        text = " ".join(raw.split())
        low = text.lower()

        if not in_section:
            if re.match(r"^\s*(?:\d+\.?\s*)?функции:?\s*$", low):
                in_section = True
            continue

        # Stop at section headers
        if re.match(r"^\s*глава\s+\d+", low) or re.match(r"^\s*раздел\s+\d+", low):
            break

        if text.strip():
            functions.append(text.strip())

    cleaned_functions = [clean_function_text(f) for f in functions if f]
    if not in_section:
        return [], "HEADER_NOT_FOUND"
    if in_section and not cleaned_functions:
        return [], "NO_ITEMS_FOUND"
    return cleaned_functions, "OK"


def clean_function_text(text: str) -> str:
    """Clean and format function text."""
    if not text:
        return ""

    # Remove control characters
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    # Remove unwanted patterns
    patterns_to_remove = [
        r"\s*сноска\s*\..*",
        r"\s*примечание\s*ИЗПИ!.*",
        r"\s*\(?вводится в действие.*?(\(|$)",
        r"\(порядок введения в действие см\. п\. \d+\)",
        r"\s*искл[ю]?че?н[оа]?.*",
        r"\s*действовал[аи]? до \d{2}\.\d{2}\.\d{4}.*",
        r";\s*от\s+\d{2}\.\d{2}\.\d{4}.*",
        r"\s*см\. п\. \d+",
    ]

    for pattern in patterns_to_remove:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # Remove numbered prefixes
    text = re.sub(r"^\s*\d+(?:[-.][\w]+)*\)\s*", "", text).strip()

    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip().rstrip(";,").strip()


def create_government_abbreviation(name: str) -> str:
    """Create abbreviation for government organization."""
    if not isinstance(name, str) or not name.strip():
        return "GO"

    name = " ".join(name.split())
    tokens = re.split(r"[\s\-]+", name)

    if all(tok.isupper() and len(tok) > 1 for tok in tokens):
        return tokens[0]

    # Create abbreviation from first letters, skipping common words
    stop_words = {"и", "по", "о", "в", "на", "об", "с", "при", "для", "над", "под", "из", "во", "со", "республика", "республики", "казахстан", "казахстана", "государственного", "учреждения"}

    letters = [w[0].upper() for w in tokens if w and w.lower() not in stop_words]
    return "".join(letters) or (tokens[0][:3].upper())


def sanitize_json_string(s: str) -> str:
    """Sanitize string to valid JSON."""
    s = s.strip()
    match = re.search(r"```(?:json)?\s*(\{.*})\s*```", s, re.DOTALL)
    if match:
        s = match.group(1)

    try:
        start = s.index("{")
        end = s.rindex("}") + 1
        return s[start:end]
    except ValueError:
        return s


def create_ai_client(config: Dict) -> OpenAI:
    """Create AI client based on configuration."""
    if config["ai_mode"] == "Онлайн":
        return OpenAI(
            api_key=config["ai_api_key"],
            http_client=httpx.Client(timeout=60.0)
        )
    elif config["ai_mode"] == "Локальный":
        return OpenAI(
            base_url=prepare_api_base_url(config["embedding_server"]),
            api_key="not-needed",
            http_client=httpx.Client(timeout=60.0)
        )
    else:  # АП mode
        return OpenAI(
            base_url=prepare_api_base_url(config["embedding_server"]),
            api_key=config["ai_api_key"],
            http_client=httpx.Client(timeout=60.0)
        )
