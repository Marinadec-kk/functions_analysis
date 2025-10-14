"""
Markdown Generator - Generate hierarchical markdown documentation from classified data

This module handles:
1. Reading Excel file from collision detector
2. Generating hierarchical markdown files by government levels
3. Creating collision group documentation
4. Generating ministry/executive portraits with function details
5. Organizing output in structured folder hierarchy
"""

import os
import re
import logging
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict, Counter

import pandas as pd
from tqdm import tqdm

from .config import setup_logging

logger = logging.getLogger(__name__)

# Column names
COL_ID = "ID"
COL_TEXT = "FunctionText"
COL_EXECUTOR = "Госорган"
COL_TYPE = "TrueType"
COL_SPHERE = "Sphere"
COL_SPHERE_2 = "Sphere_2"
COL_SPHERE_3 = "Sphere_3"
COL_COLLISION_GROUP = "Collision_Group"
COL_COLLISION_VERDICT = "Collision_Verdict"

# Level mappings for hierarchical organization
LEVEL_TO_TAG_MAP = {
    '1': '#Центральные_аппараты',
    '2': '#Комитеты_и_департаменты',
    '3': '#Управления',
    '4': '#Руководители_ЦА',
    '5': '#Директоры',
    '6': '#Руководители_отделов',
    '7': '#Главные_специалисты',
    '8': '#Специалисты'
}


def run_markdown_generator(config: Dict, excel_file: str) -> str:
    """Generate markdown documentation from classified data."""
    logger = setup_logging(config)

    logger.info(f"Starting markdown generation for: {excel_file}")

    # Read input Excel file
    df = pd.read_excel(excel_file)

    # Create output folder
    output_folder = config['output_markdown']
    os.makedirs(output_folder, exist_ok=True)

    # Generate hierarchical structure
    generate_hierarchical_structure(df, output_folder, logger)

    # Generate collision group files
    generate_collision_groups(df, output_folder, logger)

    # Generate ministry portraits
    generate_ministry_portraits(df, output_folder, logger)

    logger.info(f"Markdown generation completed. Output folder: {output_folder}")
    return output_folder


def generate_hierarchical_structure(df: pd.DataFrame, output_folder: str, logger: logging.Logger):
    """Generate hierarchical markdown files by government levels."""
    logger.info("Generating hierarchical structure...")

    # Group functions by executor and level
    executor_functions = defaultdict(list)

    for _, row in df.iterrows():
        executor = str(row.get(COL_EXECUTOR, ""))
        if executor:
            executor_functions[executor].append(row)

    # Generate files for each level
    for level in ['1', '2', '3', '4', '5', '6', '7', '8']:
        level_functions = []

        for executor, functions in executor_functions.items():
            executor_level_functions = [f for f in functions if str(f.get('Level', '')) == level]

            if executor_level_functions:
                level_functions.extend(executor_level_functions)

        if level_functions:
            create_level_markdown_file(level, level_functions, output_folder, logger)


def create_level_markdown_file(level: str, functions: List[pd.Series], output_folder: str, logger: logging.Logger):
    """Create markdown file for a specific government level."""
    level_tag = LEVEL_TO_TAG_MAP.get(level, f'#Уровень_{level}')

    # Group functions by executor within this level
    executor_groups = defaultdict(list)
    for func in functions:
        executor = str(func.get(COL_EXECUTOR, ""))
        executor_groups[executor].append(func)

    content_lines = [
        f"# Функции уровня {level}",
        f"{level_tag}",
        "",
        "## Содержание",
        ""
    ]

    # Add table of contents
    for executor in sorted(executor_groups.keys()):
        clean_executor = sanitize_filename(executor)
        content_lines.append(f"- [{executor}](#{clean_executor})")

    content_lines.append("")

    # Generate content for each executor
    for executor in tqdm(sorted(executor_groups.keys()), desc="Generating executor docs", unit="exec"):
        functions_list = executor_groups[executor]

        clean_executor = sanitize_filename(executor)
        content_lines.extend([
            f"## {executor}",
            f"<a name=\"{clean_executor}\"></a>",
            "",
            "### Функции",
            ""
        ])

        for func in functions_list:
            func_id = str(func.get(COL_ID, ""))
            func_text = str(func.get(COL_TEXT, ""))
            func_type = str(func.get(COL_TYPE, ""))
            sphere_2 = str(func.get(COL_SPHERE_2, ""))

            # Create function entry with metadata
            func_line = f"- ⚙️ **{func_id}**"
            details = []

            if func_type:
                details.append(f"Тип: {func_type}")
            if sphere_2:
                cleaned_sphere2 = re.sub(r'^\d+\.?\s*', '', sphere_2)
                details.append(f"Сфера (ур. 2): {cleaned_sphere2}")

            if details:
                func_line += f" ({' | '.join(details)})"

            content_lines.append(func_line)

            # Add function text
            if func_text:
                content_lines.extend([
                    "",
                    "```",
                    func_text.strip(),
                    "```",
                    ""
                ])

    # Write file
    filename = f"level_{level}_functions.md"
    filepath = os.path.join(output_folder, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(content_lines))

    logger.info(f"Created level {level} file: {filename}")


def generate_collision_groups(df: pd.DataFrame, output_folder: str, logger: logging.Logger):
    """Generate collision group documentation."""
    logger.info("Generating collision groups...")

    # Group functions by collision group
    collision_groups = defaultdict(list)

    for _, row in df.iterrows():
        collision_group = str(row.get(COL_COLLISION_GROUP, ""))
        if collision_group and collision_group != "NO_COLLISION":
            collision_groups[collision_group].append(row)

    # Create collision group files
    for group_name, functions in collision_groups.items():
        create_collision_group_file(group_name, functions, output_folder, logger)


def create_collision_group_file(group_name: str, functions: List[pd.Series], output_folder: str, logger: logging.Logger):
    """Create markdown file for a collision group."""
    content_lines = [
        f"# Группа коллизий: {group_name}",
        "",
        "## Функции в группе",
        ""
    ]

    for func in functions:
        func_id = str(func.get(COL_ID, ""))
        executor = str(func.get(COL_EXECUTOR, ""))
        func_text = str(func.get(COL_TEXT, ""))
        verdict = str(func.get(COL_COLLISION_VERDICT, ""))

        content_lines.extend([
            f"### {func_id}",
            f"**Исполнитель:** {executor}",
            f"**Вердикт:** {verdict}",
            "",
            "```",
            func_text.strip() if func_text else "Текст функции отсутствует",
            "```",
            ""
        ])

    # Write file
    safe_group_name = sanitize_filename(group_name)
    filename = f"collision_{safe_group_name}.md"
    filepath = os.path.join(output_folder, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(content_lines))

    logger.info(f"Created collision group file: {filename}")


def generate_ministry_portraits(df: pd.DataFrame, output_folder: str, logger: logging.Logger):
    """Generate ministry/executive portraits with function details."""
    logger.info("Generating ministry portraits...")

    # Group functions by executor (ministry)
    ministry_functions = defaultdict(list)

    for _, row in df.iterrows():
        executor = str(row.get(COL_EXECUTOR, ""))
        if executor:
            ministry_functions[executor].append(row)

    # Create portrait files for each ministry
    for ministry, functions in ministry_functions.items():
        create_ministry_portrait(ministry, functions, output_folder, logger)


def create_ministry_portrait(ministry: str, functions: List[pd.Series], output_folder: str, logger: logging.Logger):
    """Create detailed portrait for a ministry."""
    safe_ministry = sanitize_filename(ministry)

    content_lines = [
        f"# Портрет исполнителя: {ministry}",
        "",
        "## Общая статистика",
        ""
    ]

    # Calculate statistics
    total_functions = len(functions)

    # Function type distribution
    type_counts = Counter(str(f.get(COL_TYPE, "")) for f in functions)
    sphere_counts = Counter(str(f.get(COL_SPHERE, "")) for f in functions)

    # Collision statistics
    collision_count = sum(1 for f in functions if str(f.get(COL_COLLISION_GROUP, "")) != "NO_COLLISION")

    content_lines.extend([
        f"**Общее количество функций:** {total_functions}",
        f"**Функций с коллизиями:** {collision_count}",
        "",
        "### Распределение по типам функций",
        ""
    ])

    # Create type distribution table
    if type_counts:
        content_lines.append("| Тип | Количество | Доля |")
        content_lines.append("|-----|------------|------|")

        for func_type, count in sorted(type_counts.items()):
            percentage = (count / total_functions) * 100
            content_lines.append(f"| {func_type} | {count} | {percentage:.1f}% |")

    content_lines.extend([
        "",
        "### Распределение по сферам",
        ""
    ])

    # Create sphere distribution table
    if sphere_counts:
        content_lines.append("| Сфера | Количество | Доля |")
        content_lines.append("|-------|------------|------|")

        for sphere, count in sorted(sphere_counts.items()):
            percentage = (count / total_functions) * 100
            content_lines.append(f"| {sphere} | {count} | {percentage:.1f}% |")

    content_lines.extend([
        "",
        "## Список функций",
        ""
    ])

    # List all functions with details
    for func in functions:
        func_id = str(func.get(COL_ID, ""))
        func_text = str(func.get(COL_TEXT, ""))
        func_type = str(func.get(COL_TYPE, ""))
        sphere_2 = str(func.get(COL_SPHERE_2, ""))
        collision_group = str(func.get(COL_COLLISION_GROUP, ""))
        collision_verdict = str(func.get(COL_COLLISION_VERDICT, ""))

        details = []
        if func_type:
            details.append(f"Тип: {func_type}")
        if sphere_2:
            cleaned_sphere2 = re.sub(r'^\d+\.?\s*', '', sphere_2)
            details.append(f"Сфера (ур. 2): {cleaned_sphere2}")
        if collision_group != "NO_COLLISION":
            details.append(f"Коллизия: {collision_group}")

        details_str = " | ".join(details) if details else ""

        content_lines.extend([
            f"### ⚙️ {func_id}",
            f"**Детали:** {details_str}" if details_str else "",
            "",
            "```",
            func_text.strip() if func_text else "Текст функции отсутствует",
            "```",
            ""
        ])

    # Write file
    filename = f"portrait_{safe_ministry}.md"
    filepath = os.path.join(output_folder, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(content_lines))

    logger.info(f"Created ministry portrait: {filename}")


def sanitize_filename(name: str) -> str:
    """Clean string to be a safe filename or folder name."""
    if not isinstance(name, str) or not name.strip():
        return "unnamed"

    invalid_chars = r'<>:"/\\|?*'
    for ch in invalid_chars:
        name = name.replace(ch, '')

    name = re.sub(r'[()\.,;\-\s/]+', '_', name)
    name = re.sub(r'__+', '_', name)
    name = name.strip('_')

    return name or "unnamed"
