import os
import logging
from typing import Dict, Any
from dotenv import load_dotenv

def get_project_root() -> str:
    """Get absolute path to project root directory."""
    # The project root is the parent directory of the pipeline package
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def normalize_path(path: str, project_root: str) -> str:
    """
    Convert path to absolute path within project root.
    
    - If path is already an absolute path within project root, return as-is
    - If path is already an absolute path outside project root, return as-is (user knows what they're doing)
    - If path is relative, make it absolute relative to project root
    """
    if not path:
        raise ValueError("Path cannot be empty")
    
    # Check if path is already absolute AND exists outside project root
    # (e.g., /home/user/some/other/location)
    if os.path.isabs(path):
        abs_project_root = os.path.abspath(project_root)
        # If the absolute path starts with project root, it's inside - keep it
        # If not, it's outside but user explicitly specified it - keep it
        return os.path.abspath(path)
    
    # Path is relative - make it absolute relative to project root
    abs_path = os.path.abspath(os.path.join(project_root, path))
    
    return abs_path

def load_config() -> Dict[str, Any]:
    """Load configuration from environment variables."""
    load_dotenv()

    # Get project root
    project_root = get_project_root()

    # Required environment variables
    required_vars = [
        'INPUT_FOLDER',
        'OUTPUT_EXCEL',
        'OUTPUT_MARKDOWN_FOLDER',
        'SPHERES_FILE',
        'UNIVERSAL_FUNCTIONS_JSON',
        'AI_MODE',
        'AI_MODEL',
        'SIMILARITY_THRESHOLD',
        'UNIVERSAL_SIMILARITY_THRESHOLD',
        'EMBEDDING_WORKERS',
        'AI_WORKERS',
        'BATCH_SIZE',
        'LOG_LEVEL',
    ]
    
    # Check for missing required variables
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    
    # Check for API key if in online mode
    ai_mode = os.getenv('AI_MODE')
    if ai_mode == 'online' and not os.getenv('OPENAI_API_KEY'):
        raise ValueError("OPENAI_API_KEY is required when AI_MODE='online'")
    
    # Check for embedding server if in local or alternative mode (unless using local embeddings)
    use_local_embeddings = os.getenv('USE_LOCAL_EMBEDDINGS', 'true').lower() == 'true'
    if ai_mode in ('local', 'alternative'):
        if not use_local_embeddings and not os.getenv('EMBEDDING_SERVER'):
            raise ValueError(f"EMBEDDING_SERVER is required when AI_MODE='{ai_mode}' and USE_LOCAL_EMBEDDINGS=false")
        if not os.getenv('EMBEDDING_MODEL'):
            raise ValueError(f"EMBEDDING_MODEL is required when AI_MODE='{ai_mode}'")

    # Normalize all file paths to absolute paths
    config = {
        # Core settings (all normalized to absolute paths)
        'project_root': project_root,
        'input_folder': normalize_path(os.getenv('INPUT_FOLDER'), project_root),
        'output_excel': normalize_path(os.getenv('OUTPUT_EXCEL'), project_root),
        'output_markdown': normalize_path(os.getenv('OUTPUT_MARKDOWN_FOLDER'), project_root),
        'spheres_file': normalize_path(os.getenv('SPHERES_FILE'), project_root),
        'universal_functions': normalize_path(os.getenv('UNIVERSAL_FUNCTIONS_JSON'), project_root),

        # AI Configuration
        'ai_mode': os.getenv('AI_MODE'),
        'ai_api_key': os.getenv('OPENAI_API_KEY'),
        'ai_model': os.getenv('AI_MODEL'),
        'embedding_server': os.getenv('EMBEDDING_SERVER', ''),
        'embedding_model': os.getenv('EMBEDDING_MODEL'),
        'use_local_embeddings': os.getenv('USE_LOCAL_EMBEDDINGS', 'true').lower() == 'true',

        # Model Parameters
        'ai_temperature': float(os.getenv('AI_TEMPERATURE', '1.0')),
        'ai_max_tokens': int(os.getenv('AI_MAX_TOKENS', '4000')),

        # Processing Parameters
        'similarity_threshold': float(os.getenv('SIMILARITY_THRESHOLD')),
        'universal_threshold': float(os.getenv('UNIVERSAL_SIMILARITY_THRESHOLD')),
        'embedding_workers': int(os.getenv('EMBEDDING_WORKERS')),
        'ai_workers': int(os.getenv('AI_WORKERS')),
        'batch_size': int(os.getenv('BATCH_SIZE')),

        # Prompt files (converted to absolute paths)
        'prompt_files': {
            'document_parser_extract': os.path.join(project_root, 'prompts/document_parser/extract_full_name.txt'),
            'function_classifier_initial': os.path.join(project_root, 'prompts/function_classifier/classify_initial.txt'),
            'function_classifier_refinement': os.path.join(project_root, 'prompts/function_classifier/classify_refinement.txt'),
            'function_classifier_verify': os.path.join(project_root, 'prompts/function_classifier/verify_type.txt'),
            'function_classifier_arbitrate': os.path.join(project_root, 'prompts/function_classifier/arbitrate_dispute.txt'),
            'sphere_classifier': os.path.join(project_root, 'prompts/sphere_classifier/classify_sphere.txt'),
            'collision_detector': os.path.join(project_root, 'prompts/collision_detector/verify_collision.txt'),
        },

        # Logging
        'log_level': os.getenv('LOG_LEVEL'),
    }

    return config

def validate_config(config: Dict[str, Any]) -> None:
    """Validate configuration and required files."""
    # Validate input folder exists
    if not os.path.exists(config['input_folder']):
        raise FileNotFoundError(f"Input folder not found: {config['input_folder']}")
    
    # Validate reference files exist
    required_files = {
        'spheres_file': config['spheres_file'],
        'universal_functions': config['universal_functions']
    }
    
    for file_key, file_path in required_files.items():
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Required file '{file_key}' not found: {file_path}")

    # Validate prompt files exist (now stored as absolute paths)
    for prompt_key, prompt_path in config['prompt_files'].items():
        if not os.path.exists(prompt_path):
            raise FileNotFoundError(f"Required prompt file '{prompt_key}' not found: {prompt_path}")
    
    # Create output directories if they don't exist
    output_dirs = [
        os.path.dirname(config['output_excel']),
        config['output_markdown']
    ]
    
    for output_dir in output_dirs:
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

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
