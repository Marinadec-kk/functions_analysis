import os
import logging
from typing import Dict, Any
from dotenv import load_dotenv

def load_config() -> Dict[str, Any]:
    """Load configuration from environment variables."""
    load_dotenv()

    config = {
        # Core settings
        'input_folder': os.getenv('INPUT_FOLDER', '/data/input_documents'),
        'output_excel': os.getenv('OUTPUT_EXCEL', '/data/analysis_results.xlsx'),
        'output_markdown': os.getenv('OUTPUT_MARKDOWN_FOLDER', '/data/markdown_output'),
        'spheres_file': os.getenv('SPHERES_FILE', '/data/spheres_reference.xlsx'),
        'universal_functions': os.getenv('UNIVERSAL_FUNCTIONS_JSON', '/data/universal_functions.json'),

        # AI Configuration
        'ai_mode': os.getenv('AI_MODE', 'online'),
        'ai_api_key': os.getenv('OPENAI_API_KEY'),
        'ai_model': os.getenv('AI_MODEL', 'gpt-4o-mini'),
        'embedding_server': os.getenv('EMBEDDING_SERVER', 'http://localhost:1234'),
        'embedding_model': os.getenv('EMBEDDING_MODEL', 'Qwen/Qwen3-Embedding-8B-GGUF'),

        # Processing Parameters
        'similarity_threshold': float(os.getenv('SIMILARITY_THRESHOLD', '0.5')),
        'universal_threshold': float(os.getenv('UNIVERSAL_SIMILARITY_THRESHOLD', '0.75')),
        'embedding_workers': int(os.getenv('EMBEDDING_WORKERS', '8')),
        'ai_workers': int(os.getenv('AI_WORKERS', '30')),
        'batch_size': int(os.getenv('BATCH_SIZE', '64')),

        # Prompt files (relative to project root)
        'prompt_files': {
            'document_parser_extract': 'prompts/document_parser/extract_full_name.txt',
            'function_classifier_initial': 'prompts/function_classifier/classify_initial.txt',
            'function_classifier_refinement': 'prompts/function_classifier/classify_refinement.txt',
            'function_classifier_verify': 'prompts/function_classifier/verify_type.txt',
            'function_classifier_arbitrate': 'prompts/function_classifier/arbitrate_dispute.txt',
            'sphere_classifier': 'prompts/sphere_classifier/classify_sphere.txt',
            'collision_detector': 'prompts/collision_detector/verify_collision.txt',
        },

        # Logging
        'log_level': os.getenv('LOG_LEVEL', 'INFO'),
    }

    return config

def validate_config(config: Dict[str, Any]) -> None:
    """Validate configuration and required files."""
    required_files = [
        config['input_folder'],
        config['spheres_file'],
        config['universal_functions']
    ]

    for file_path in required_files:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Required file not found: {file_path}")

    # Validate prompt files exist (relative to project root)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for prompt_key, prompt_path in config['prompt_files'].items():
        full_prompt_path = os.path.join(project_root, prompt_path)
        if not os.path.exists(full_prompt_path):
            raise FileNotFoundError(f"Required prompt file not found: {full_prompt_path}")

def setup_logging(config: Dict[str, Any]) -> logging.Logger:
    """Set up structured logging."""
    logger = logging.getLogger('functions_analysis')
    logger.setLevel(getattr(logging, config['log_level'].upper()))

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
