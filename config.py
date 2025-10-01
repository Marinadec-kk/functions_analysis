# config.py

import os
import configparser
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

# Base directory for the project (adjust if needed)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Path to the configuration file
CONFIG_INI_PATH = os.path.join(BASE_DIR, "config.ini")

# Load settings from config.ini
config = configparser.ConfigParser()
config.read(CONFIG_INI_PATH)


# Helper function to get config values with priority: Env -> INI -> Hardcoded Default
def get_setting(
    env_var: str, ini_section: str, ini_key: str, default_value, type_cast=None
):
    """
    Retrieves a configuration setting with priority:
    1. Environment variable
    2. config.ini file
    3. Hardcoded default value

    Args:
        env_var (str): The name of the environment variable.
        ini_section (str): The section name in config.ini.
        ini_key (str): The key name within the section in config.ini.
        default_value: The hardcoded default value.
        type_cast (callable, optional): A function to cast the value to the desired type (e.g., int, float, bool). Defaults to None.

    Returns:
        The configuration value, cast to the specified type if type_cast is provided.
    """
    # 1. Try environment variable
    value = os.getenv(env_var)
    if value is not None:
        try:
            return type_cast(value) if type_cast else value
        except ValueError:
            # If type casting fails for env var, log and proceed to INI/default
            print(
                f"Warning: Could not cast environment variable '{env_var}' (value: '{value}') to type {type_cast.__name__}. Falling back to config.ini or default."
            )
            pass  # Fall through to INI

    # 2. Try config.ini
    if config.has_section(ini_section) and config.has_option(ini_section, ini_key):
        value = config[ini_section][ini_key]
        try:
            return type_cast(value) if type_cast else value
        except ValueError:
            # If type casting fails for INI, log and proceed to hardcoded default
            print(
                f"Warning: Could not cast config.ini setting '{ini_section}.{ini_key}' (value: '{value}') to type {type_cast.__name__}. Falling back to default."
            )
            pass  # Fall through to hardcoded default

    # 3. Use hardcoded default
    return type_cast(default_value) if type_cast else default_value


# --- API Configuration ---
AI_API_BASE_URL = get_setting(
    env_var="AI_API_BASE_URL",
    ini_section="API",
    ini_key="AI_API_BASE_URL",
    default_value="http://localhost:8000/api",
)

AI_API_KEY = get_setting(
    env_var="AI_API_KEY",
    ini_section="API",  # Not typically stored in INI, but here for completeness of priority
    ini_key="AI_API_KEY",
    default_value="your_ai_api_key_here",
)

AI_API_VERSION = get_setting(
    env_var="AI_API_VERSION",
    ini_section="API",
    ini_key="AI_API_VERSION",
    default_value="v1",
)

AI_CHAT_COMPLETION_ENDPOINT = get_setting(
    env_var="AI_CHAT_COMPLETION_ENDPOINT",
    ini_section="API",
    ini_key="AI_CHAT_COMPLETION_ENDPOINT",
    default_value="/chat/completions",
)

AI_EMBEDDING_ENDPOINT = get_setting(
    env_var="AI_EMBEDDING_ENDPOINT",
    ini_section="API",
    ini_key="AI_EMBEDDING_ENDPOINT",
    default_value="/embeddings",
)


# --- File Paths Configuration ---
DEFAULT_INPUT_DOCS_DIR = os.path.join(
    BASE_DIR,
    get_setting(
        env_var="DEFAULT_INPUT_DOCS_DIR",
        ini_section="Paths",
        ini_key="DEFAULT_INPUT_DOCS_DIR",
        default_value="input_documents",
    ),
)
DEFAULT_OUTPUT_DIR = os.path.join(
    BASE_DIR,
    get_setting(
        env_var="DEFAULT_OUTPUT_DIR",
        ini_section="Paths",
        ini_key="DEFAULT_OUTPUT_DIR",
        default_value="output_results",
    ),
)

# --- AI Model Parameters (example) ---
CLASSIFICATION_MODEL_NAME = get_setting(
    env_var="CLASSIFICATION_MODEL_NAME",
    ini_section="Models",
    ini_key="CLASSIFICATION_MODEL_NAME",
    default_value="gpt-3.5-turbo",
)
VERIFICATION_MODEL_NAME = get_setting(
    env_var="VERIFICATION_MODEL_NAME",
    ini_section="Models",
    ini_key="VERIFICATION_MODEL_NAME",
    default_value="gpt-4",
)
EMBEDDING_MODEL_NAME = get_setting(
    env_var="EMBEDDING_MODEL_NAME",
    ini_section="Models",
    ini_key="EMBEDDING_MODEL_NAME",
    default_value="text-embedding-ada-002",
)

# --- Other Application Settings ---
LOG_FILE_PATH = os.path.join(
    BASE_DIR,
    get_setting(
        env_var="LOG_FILE_PATH",
        ini_section="Paths",
        ini_key="LOG_FILE_PATH",
        default_value="app_logs.log",
    ),
)
MAX_RETRIES_API = get_setting(
    env_var="MAX_RETRIES_API",
    ini_section="Application",  # Corrected section
    ini_key="MAX_RETRIES_API",
    default_value=5,
    type_cast=int,
)
BACKOFF_FACTOR_API = get_setting(
    env_var="BACKOFF_FACTOR_API",
    ini_section="Application",  # Corrected section
    ini_key="BACKOFF_FACTOR_API",
    default_value=0.5,
    type_cast=float,
)


def get_full_config():
    """Gathers all configuration settings into a single dictionary."""
    return {
        # API settings
        "ai_api_base_url": AI_API_BASE_URL,
        "ai_api_key": AI_API_KEY,
        "ai_api_version": AI_API_VERSION,
        "ai_chat_completion_endpoint": AI_CHAT_COMPLETION_ENDPOINT,
        "ai_embedding_endpoint": AI_EMBEDDING_ENDPOINT,
        # Path settings
        "default_input_docs_dir": DEFAULT_INPUT_DOCS_DIR,
        "default_output_dir": DEFAULT_OUTPUT_DIR,
        "log_file_path": LOG_FILE_PATH,
        # Model settings
        "classification_model_name": CLASSIFICATION_MODEL_NAME,
        "verification_model_name": VERIFICATION_MODEL_NAME,
        "embedding_model_name": EMBEDDING_MODEL_NAME,
        # Application settings
        "max_retries_api": MAX_RETRIES_API,
        "backoff_factor_api": BACKOFF_FACTOR_API,
    }
