import pytest
import os
from pipeline.config import load_config
from pipeline.utils import load_prompt


def test_all_prompts_exist_and_load():
    """Test that all prompt files exist and can be loaded."""
    config = load_config()

    # Get project root for relative path resolution
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for prompt_key, prompt_path in config['prompt_files'].items():
        full_prompt_path = os.path.join(project_root, prompt_path)

        # Check file exists
        assert os.path.exists(full_prompt_path), f"Prompt file not found: {full_prompt_path}"

        # Check file can be loaded
        prompt_content = load_prompt(full_prompt_path)
        assert prompt_content, f"Prompt file is empty: {full_prompt_path}"
        assert len(prompt_content.strip()) > 0, f"Prompt file has no content: {full_prompt_path}"

        # Check for JSON format in collision detector (should have "verdict" key example)
        if prompt_key == 'collision_detector':
            assert '"verdict"' in prompt_content, f"Collision detector prompt missing verdict format: {full_prompt_path}"


def test_prompt_loading_function():
    """Test the load_prompt utility function."""
    # Create a temporary test prompt file
    test_content = "Test prompt content"
    with open('/tmp/test_prompt.txt', 'w') as f:
        f.write(test_content)

    # Test loading
    loaded_content = load_prompt('/tmp/test_prompt.txt')
    assert loaded_content == test_content

    # Clean up
    os.remove('/tmp/test_prompt.txt')


def test_config_prompt_paths():
    """Test that config contains all expected prompt paths."""
    config = load_config()

    expected_prompts = [
        'document_parser_extract',
        'function_classifier_initial',
        'function_classifier_refinement',
        'function_classifier_verify',
        'function_classifier_arbitrate',
        'sphere_classifier',
        'collision_detector'
    ]

    for prompt_key in expected_prompts:
        assert prompt_key in config['prompt_files'], f"Missing prompt key: {prompt_key}"
        prompt_path = config['prompt_files'][prompt_key]
        assert prompt_path.endswith('.txt'), f"Prompt file should end with .txt: {prompt_path}"
