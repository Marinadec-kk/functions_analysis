import pytest
import os
import tempfile
from unittest.mock import patch
from pipeline.config import load_config, validate_config


def test_config_loading():
    """Test that configuration loads correctly."""
    # Test with minimal environment
    config = load_config()

    # Check that all required keys are present
    required_keys = [
        'input_folder', 'output_excel', 'output_markdown',
        'spheres_file', 'universal_functions', 'ai_mode',
        'ai_api_key', 'ai_model', 'embedding_server',
        'embedding_model', 'similarity_threshold',
        'universal_threshold', 'embedding_workers',
        'ai_workers', 'batch_size', 'prompt_files', 'log_level'
    ]

    for key in required_keys:
        assert key in config, f"Missing required config key: {key}"

    # Check types
    assert isinstance(config['similarity_threshold'], float)
    assert isinstance(config['embedding_workers'], int)
    assert isinstance(config['prompt_files'], dict)


def test_config_validation():
    """Test configuration validation."""
    config = load_config()

    # Mock os.path.exists to return True for all required files
    with patch('os.path.exists', return_value=True):
        # Should pass with valid config
        validate_config(config)


def test_config_validation_missing_files():
    """Test validation fails with missing files."""
    config = load_config()
    # Mock the file paths to point to nonexistent locations
    config['input_folder'] = '/nonexistent/input'
    config['spheres_file'] = '/nonexistent/spheres.xlsx'
    config['universal_functions'] = '/nonexistent/universal.json'

    # Mock os.path.exists to return False for these paths and True for prompt files
    def mock_exists(path):
        if path in ['/nonexistent/input', '/nonexistent/spheres.xlsx', '/nonexistent/universal.json']:
            return False
        return True  # Return True for prompt files that exist

    with patch('os.path.exists', side_effect=mock_exists):
        with pytest.raises(FileNotFoundError):
            validate_config(config)
