`"""
Document Parser - Extracts functions and government hierarchy from documents

This module handles:
1. Reading .docx and .doc files
2. Extracting government body hierarchy using AI
3. Parsing function sections from documents
4. Creating initial Excel matrix with ID, FunctionText, Worker, Parent, Level
"""

import os
import re
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set

import pandas as pd
from docx import Document
from openai import OpenAI, APIConnectionError, RateLimitError, APITimeoutError
import httpx
from tqdm import tqdm

from .config import setup_logging
from .utils import load_prompt, prepare_api_base_url

logger = logging.getLogger(__name__)

SECTION_ANCHOR_PATTERN = re.compile(r"\bположение\s+о\b", re.IGNORECASE)
GLAVA_ANCHOR_PATTERN = re.compile(r"\bглава\s*1\b", re.IGNORECASE)
FUNCTION_HEADER_PATTERN = re.compile(
    r"^\s*(?:\d+\.?\s*)?функции(?:\s+(?:комитета|департамента))?\s*:?\s*$",
    re.IGNORECASE,
)
SECTION_TERMINATOR_PATTERN = re.compile(r"^\s*(?:глава|раздел)\b", re.IGNORECASE)

LEVEL_MAPPING = {
    "министерство": "1",
    "агентство": "1.1",
    "комитет": "2.1",
    "департамент": "2.2",
    "управление": "2.3",
    "служба": "2.4",
    "центр": "3.1",
    "институт": "3.2",
}

ROOT_PARENT_NAME = "Республика Казахстан"


@dataclass
class SectionResult:
    full_go_name: str
    main_go: str
    parent_go: Optional[str]
    functions: List[str]
    source: str


@dataclass
class GovernmentNode:
    name: str
    full_name: str
    parent_name: Optional[str]
    abbreviation: str
    functions: List[str] = field(default_factory=list)
    children: Set[str] = field(default_factory=set)
    level: Optional[str] = None
    worker: Optional[str] = None
    parent_worker: Optional[str] = None
    id_prefix: Optional[str] = None


def split_document_sections(paragraphs: List[str]) -> List[List[str]]:
    """Split document into sections starting from 'Положение о ... Глава 1' anchors."""
    anchors: List[int] = []
    for idx, raw in enumerate(paragraphs):
        text = (raw or "").strip()
        if not text:
            continue

        normalized = " ".join(text.split())
        if SECTION_ANCHOR_PATTERN.search(normalized) and _has_glava_anchor(
            paragraphs, idx
        ):
            anchors.append(idx)

    if not anchors:
        return []

    sections: List[List[str]] = []
    for pos, start in enumerate(anchors):
        end = anchors[pos + 1] if pos + 1 < len(anchors) else len(paragraphs)
        sections.append(paragraphs[start:end])
    return sections


def _has_glava_anchor(
    paragraphs: List[str], start_index: int, lookahead: int = 12
) -> bool:
    """Validate that 'Глава 1' appears shortly after the 'Положение' header."""
    limit = min(len(paragraphs), start_index + lookahead + 1)
    for idx in range(start_index, limit):
        text = (paragraphs[idx] or "").strip()
        if not text:
            continue
        normalized = " ".join(text.split())
        if GLAVA_ANCHOR_PATTERN.search(normalized):
            return True
    return False


def build_government_hierarchy(
    section_results: List[SectionResult],
) -> Dict[str, GovernmentNode]:
    """Aggregate section data into government hierarchy nodes."""
    nodes: Dict[str, GovernmentNode] = {}

    for section in section_results:
        main_name = section.main_go
        parent_name = section.parent_go.strip() if section.parent_go else None

        node = nodes.get(main_name)
        if not node:
            node = GovernmentNode(
                name=main_name,
                full_name=section.full_go_name or main_name,
                parent_name=parent_name,
                abbreviation=create_government_abbreviation(main_name),
            )
            nodes[main_name] = node
        else:
            if parent_name:
                if node.parent_name and node.parent_name != parent_name:
                    logger.warning(
                        "Inconsistent parent detected for %s: %s -> %s",
                        main_name,
                        node.parent_name,
                        parent_name,
                    )
                elif not node.parent_name:
                    node.parent_name = parent_name

            if section.full_go_name and len(section.full_go_name) > len(node.full_name):
                node.full_name = section.full_go_name

        node.functions.extend(section.functions)

        if parent_name:
            parent_node = nodes.get(parent_name)
            if not parent_node:
                parent_node = GovernmentNode(
                    name=parent_name,
                    full_name=parent_name,
                    parent_name=None,
                    abbreviation=create_government_abbreviation(parent_name),
                )
                nodes[parent_name] = parent_node
            parent_node.children.add(main_name)

    return nodes


def compute_hierarchy_metadata(nodes: Dict[str, GovernmentNode]) -> None:
    """Populate worker, parent, level, and ID prefix values across the hierarchy."""
    root_abbreviation = create_government_abbreviation(ROOT_PARENT_NAME)
    visited: Set[str] = set()
    visiting: Set[str] = set()

    def dfs(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            logger.warning("Cycle detected in hierarchy for %s", name)
            visiting.remove(name)
            return

        visiting.add(name)
        node = nodes[name]
        parent_name = node.parent_name.strip() if node.parent_name else None
        parent_node = nodes.get(parent_name) if parent_name else None

        if parent_node:
            dfs(parent_name)
            parent_abbrev = parent_node.abbreviation
            parent_worker = parent_node.worker or ROOT_PARENT_NAME
            parent_prefix = parent_node.id_prefix or parent_node.abbreviation
        else:
            parent_abbrev = root_abbreviation
            parent_worker = ROOT_PARENT_NAME
            parent_prefix = ""

        node.level = determine_level(node.name)
        suffix = parent_abbrev if parent_abbrev else ""
        node.worker = f"{node.full_name} {suffix}".strip()
        node.parent_worker = parent_worker
        node.id_prefix = (
            f"{parent_prefix}-{node.abbreviation}"
            if parent_prefix
            else node.abbreviation
        )

        visited.add(name)
        visiting.remove(name)

        for child_name in sorted(node.children):
            dfs(child_name)

    for name in list(nodes.keys()):
        if name not in visited:
            dfs(name)


def determine_level(name: str) -> str:
    """Map the first word of a government body name to its classification level."""
    if not isinstance(name, str):
        return ""

    tokens = re.findall(r"[A-Za-zА-Яа-яЁё]+", name)
    if not tokens:
        return ""

    first_word = tokens[0].lower()
    return LEVEL_MAPPING.get(first_word, "")


def generate_output_rows(nodes: Dict[str, GovernmentNode]) -> List[Dict[str, str]]:
    """Generate final flattened rows for the Excel output."""
    records: List[Dict[str, str]] = []

    for node in nodes.values():
        if not node.functions:
            continue

        id_prefix = node.id_prefix
        if not id_prefix:
            logger.warning(
                "ID prefix missing for %s; falling back to abbreviation", node.name
            )
            id_prefix = node.abbreviation

        parent_value = node.parent_worker or ROOT_PARENT_NAME
        worker_value = node.worker or node.full_name

        for idx, func_text in enumerate(node.functions, start=1):
            records.append(
                {
                    "ID": f"{id_prefix}-{idx}",
                    "FunctionText": func_text,
                    "Worker": worker_value,
                    "Parent": parent_value,
                    "Level": node.level or "",
                }
            )

    return records


def run_document_parser(config: Dict) -> str:
    """Parse documents and create initial Excel file."""
    logger = setup_logging(config)

    prompt_path = config["prompt_files"]["document_parser_extract"]
    prompt = load_prompt(prompt_path)

    input_folder = config["input_folder"]
    documents = find_documents(input_folder)

    if not documents:
        raise ValueError(f"No documents found in {input_folder}")

    logger.info(f"Found {len(documents)} documents to process")

    collected_sections: List[SectionResult] = []
    for doc_path in tqdm(documents, desc="Processing documents", unit="doc"):
        try:
            section_payloads = process_document(doc_path, prompt, config)
            if section_payloads:
                collected_sections.extend(section_payloads)
        except Exception as e:
            logger.error(f"Failed to process {doc_path}: {e}")
            continue

    if not collected_sections:
        raise ValueError("No documents were successfully processed")

    hierarchy = build_government_hierarchy(collected_sections)
    compute_hierarchy_metadata(hierarchy)
    records = generate_output_rows(hierarchy)

    if not records:
        raise ValueError("No function records generated from processed documents")

    df = pd.DataFrame(
        records, columns=["ID", "FunctionText", "Worker", "Parent", "Level"]
    )
    output_path = config["output_excel"]
    df.to_excel(output_path, index=False)

    logger.info(f"Created Excel file with {len(records)} functions: {output_path}")
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


def process_document(doc_path: str, prompt: str, config: Dict) -> List[SectionResult]:
    """Process a single document and extract section data."""
    filename = os.path.basename(doc_path)
    logger.info(f"Processing: {filename}")

    try:
        paragraphs = read_document_paragraphs(doc_path)

        sections = split_document_sections(paragraphs)
        if not sections:
            logger.warning(
                f"No matching 'Положение' sections found in {filename}; document skipped"
            )
            return []

        logger.info(f"Detected {len(sections)} section(s) in {filename}")

        processed_sections: List[SectionResult] = []
        for index, section_paragraphs in enumerate(sections, start=1):
            section_label = f"{filename} [section {index}]"

            full_go_name = extract_government_body_name(
                section_paragraphs, section_label, prompt, config
            )
            if not full_go_name:
                logger.warning(
                    f"Could not extract government body name from {section_label}"
                )
                continue

            go_data = parse_government_hierarchy(full_go_name)
            main_go = (go_data.get("main_go") or "").strip()
            if not main_go:
                logger.warning(
                    f"Main government body not identified for {section_label}"
                )
                continue

            parent_go = go_data.get("parent_go")
            if isinstance(parent_go, str):
                parent_go = parent_go.strip() or None
            else:
                parent_go = None

            functions, status = extract_functions(section_paragraphs)
            if status != "OK":
                logger.warning(f"No functions found in {section_label}: {status}")
                continue

            filtered_functions = [
                func.strip() for func in functions if func and func.strip()
            ]
            if not filtered_functions:
                logger.warning(f"Filtered out empty functions in {section_label}")
                continue

            processed_sections.append(
                SectionResult(
                    full_go_name=full_go_name.strip(),
                    main_go=main_go,
                    parent_go=parent_go,
                    functions=filtered_functions,
                    source=section_label,
                )
            )

        if not processed_sections:
            logger.warning(f"No usable sections extracted from {filename}")
            return []

        total_functions = sum(len(section.functions) for section in processed_sections)
        logger.info(
            f"Extracted {total_functions} functions from {filename} across "
            f"{len(processed_sections)} section(s)"
        )

        return processed_sections

    except Exception as e:
        logger.error(f"Error processing {filename}: {e}")
        return []


def read_document_paragraphs(doc_path: str) -> List[str]:
    """Read paragraphs from .docx or .doc file."""
    if doc_path.lower().endswith(".docx"):
        doc = Document(doc_path)
        return [p.text for p in doc.paragraphs]
    elif doc_path.lower().endswith(".doc"):
        # Convert .doc to .docx using LibreOffice, then read with python-docx
        import tempfile
        import subprocess
        import shutil

        try:
            # Create a temporary directory for conversion
            temp_dir = tempfile.mkdtemp()

            # Copy the .doc file to temp directory (LibreOffice needs write access to the directory)
            temp_doc = os.path.join(temp_dir, os.path.basename(doc_path))
            shutil.copy2(doc_path, temp_doc)

            # Convert using LibreOffice headless mode
            logger.info(
                f"Converting {os.path.basename(doc_path)} to .docx using LibreOffice..."
            )

            # Try multiple possible LibreOffice commands
            libreoffice_commands = [
                "libreoffice",
                "soffice",
                "/usr/bin/libreoffice",
                "/usr/bin/soffice",
            ]

            conversion_successful = False
            for cmd in libreoffice_commands:
                try:
                    result = subprocess.run(
                        [
                            cmd,
                            "--headless",
                            "--convert-to",
                            "docx",
                            "--outdir",
                            temp_dir,
                            temp_doc,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                    if result.returncode == 0:
                        conversion_successful = True
                        logger.debug(f"Conversion successful using {cmd}")
                        break
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue

            if not conversion_successful:
                raise RuntimeError(
                    "LibreOffice not found or conversion failed. Install LibreOffice: sudo apt-get install libreoffice"
                )

            # Find the converted .docx file
            base_name = os.path.splitext(os.path.basename(doc_path))[0]
            converted_docx = os.path.join(temp_dir, base_name + ".docx")

            if not os.path.exists(converted_docx):
                raise FileNotFoundError(f"Converted file not found: {converted_docx}")

            # Read the converted .docx file
            doc = Document(converted_docx)
            paragraphs = [p.text for p in doc.paragraphs]

            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass

            logger.info(
                f"Successfully converted and read {len(paragraphs)} paragraphs from .doc file: {os.path.basename(doc_path)}"
            )
            return paragraphs

        except Exception as e:
            # Clean up on error
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
            logger.error(f"Failed to convert/read .doc file {doc_path}: {e}")
            raise ValueError(f"Failed to read .doc file: {doc_path}. Error: {e}")
    else:
        raise ValueError(
            f"Unsupported document format: {doc_path}. Only .docx and .doc files are supported."
        )


def extract_government_body_name(
    paragraphs: List[str], filename: str, prompt: str, config: Dict
) -> Optional[str]:
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
                "temperature": config["ai_temperature"],
                "max_completion_tokens": config["ai_max_tokens"],
                "top_p": 1,
                "frequency_penalty": 0,
                "presence_penalty": 0,
            }

            if config["ai_mode"] != "Локальный":
                api_args["response_format"] = {"type": "json_object"}

            logger.debug(
                f"Sending AI request for {filename} (attempt {attempt + 1}/{max_retries})"
            )
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
            logger.warning(
                f"AI API error for {filename} (attempt {attempt + 1}/{max_retries}): {type(e).__name__}"
            )
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
        "Министерства",
        "Агентства",
        "Комитета",
        "Департамента",
        "Управления",
    ]

    normalized_name = " ".join(full_name.split())

    for keyword in parent_keywords:
        match = re.search(
            r"\b" + re.escape(keyword) + r"\b", normalized_name, re.IGNORECASE
        )
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
    collected_raw: List[str] = []

    for raw in paragraphs:
        if raw is None:
            continue

        text = " ".join(raw.split())
        if not text:
            continue

        if not in_section:
            if FUNCTION_HEADER_PATTERN.match(text):
                in_section = True
                logger.debug(f"Found functions section header: {text[:100]}")
            continue

        if SECTION_TERMINATOR_PATTERN.match(text):
            break

        if FUNCTION_HEADER_PATTERN.match(text):
            logger.debug(
                "Encountered nested functions header inside section; skipping line"
            )
            continue

        collected_raw.append(text.strip())

    cleaned_functions = []
    for item in collected_raw:
        cleaned = clean_function_text(item)
        if cleaned and cleaned.strip():
            cleaned_functions.append(cleaned)

    if not in_section:
        return [], "HEADER_NOT_FOUND"
    if not cleaned_functions:
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
    stop_words = {
        "и",
        "по",
        "о",
        "в",
        "на",
        "об",
        "с",
        "при",
        "для",
        "над",
        "под",
        "из",
        "во",
        "со",
        "республика",
        "республики",
        "казахстан",
        "казахстана",
        "государственного",
        "учреждения",
    }

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
    if config["ai_mode"] == "online":
        return OpenAI(
            api_key=config["ai_api_key"], http_client=httpx.Client(timeout=60.0)
        )
    elif config["ai_mode"] == "local":
        return OpenAI(
            base_url=prepare_api_base_url(config["embedding_server"]),
            api_key="not-needed",
            http_client=httpx.Client(timeout=60.0),
        )
    else:  # АП mode
        return OpenAI(
            base_url=prepare_api_base_url(config["embedding_server"]),
            api_key=config["ai_api_key"],
            http_client=httpx.Client(timeout=60.0),
        )
