"""
Tests for function_classifier module
"""

import pytest
import json
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List

from pipeline.function_classifier import (
    run_function_classifier,
    get_similarity_suggestions,
    process_functions_with_ai,
    process_single_function,
    call_ai_classifier,
    parse_json_response,
    create_ai_client,
    ALL_CATEGORIES,
    DEFAULT_NEEDS_REVIEW_LABEL,
    COL_ID, COL_TEXT, COL_TYPE, COL_FINAL_LABEL, COL_LABELED_BY
)


class TestRunFunctionClassifier:
    """Test the main function classifier function."""

    @patch('pipeline.function_classifier.setup_logging')
    @patch('pipeline.function_classifier.pd.read_excel')
    @patch('pipeline.function_classifier.pd.DataFrame.to_excel')
    @patch('pipeline.function_classifier.process_functions_with_ai')
    @patch('pipeline.function_classifier.get_similarity_suggestions')
    @patch('pipeline.function_classifier.os.path.exists')
    def test_successful_classification(self, mock_path_exists, mock_suggestions, mock_process, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test successful function classification."""
        # Setup mocks
        config = {
            'prompt_files': {
                'function_classifier_initial': 'prompts/function_classifier/classify_initial.txt',
                'function_classifier_refinement': 'prompts/function_classifier/classify_refinement.txt',
                'function_classifier_verify': 'prompts/function_classifier/verify_type.txt',
                'function_classifier_arbitrate': 'prompts/function_classifier/arbitrate_dispute.txt',
            },
            'universal_functions': '/test/universal.json',
            'universal_threshold': 0.75
        }

        # Mock Excel data
        mock_df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Function 1 text', 'Function 2 text']
        })
        mock_read_excel.return_value = mock_df

        # Mock processed results
        processed_df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Function 1 text', 'Function 2 text'],
            COL_TYPE: ['Стратегические', 'Регулятивные'],
            COL_FINAL_LABEL: ['Стратегические', 'Регулятивные']
        })
        mock_process.return_value = processed_df
        mock_suggestions.return_value = {}
        mock_path_exists.return_value = True  # Universal functions file exists

        # Run the function
        result = run_function_classifier(config, '/test/input.xlsx')

        # Verify
        assert result == '/test/input.xlsx'
        mock_read_excel.assert_called_once_with('/test/input.xlsx')
        mock_path_exists.assert_called_once_with('/test/universal.json')
        mock_suggestions.assert_called_once()
        mock_process.assert_called_once()
        mock_to_excel.assert_called_once_with('/test/input.xlsx', index=False)

    @patch('pipeline.function_classifier.setup_logging')
    @patch('pipeline.function_classifier.pd.read_excel')
    @patch('pipeline.function_classifier.pd.DataFrame.to_excel')
    def test_no_functions_to_process(self, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test when no functions need processing."""
        config = {
            'prompt_files': {
                'function_classifier_initial': 'prompts/function_classifier/classify_initial.txt',
                'function_classifier_refinement': 'prompts/function_classifier/classify_refinement.txt',
                'function_classifier_verify': 'prompts/function_classifier/verify_type.txt',
                'function_classifier_arbitrate': 'prompts/function_classifier/arbitrate_dispute.txt',
            },
            'log_level': 'INFO'
        }

        # Mock Excel with existing classifications
        mock_df = pd.DataFrame({
            COL_ID: ['GO1-1'],
            COL_TEXT: ['Function 1 text'],
            COL_TYPE: ['Стратегические']
        })
        mock_read_excel.return_value = mock_df

        result = run_function_classifier(config, '/test/input.xlsx')

        assert result == '/test/input.xlsx'
        # Should not try to save if no processing needed
        mock_to_excel.assert_not_called()

    @patch('pipeline.function_classifier.setup_logging')
    @patch('pipeline.function_classifier.pd.read_excel')
    @patch('pipeline.function_classifier.pd.DataFrame.to_excel')
    def test_empty_dataframe(self, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test with empty dataframe."""
        config = {
            'prompt_files': {
                'function_classifier_initial': 'prompts/function_classifier/classify_initial.txt',
                'function_classifier_refinement': 'prompts/function_classifier/classify_refinement.txt',
                'function_classifier_verify': 'prompts/function_classifier/verify_type.txt',
                'function_classifier_arbitrate': 'prompts/function_classifier/arbitrate_dispute.txt',
            },
            'log_level': 'INFO'
        }

        mock_df = pd.DataFrame()
        mock_read_excel.return_value = mock_df

        result = run_function_classifier(config, '/test/input.xlsx')

        assert result == '/test/input.xlsx'
        # Should not try to save if no data
        mock_to_excel.assert_not_called()


class TestGetSimilaritySuggestions:
    """Test similarity suggestions generation."""

    @patch('pipeline.function_classifier.os.path.exists')
    @patch('pipeline.function_classifier.pd.read_json')
    @patch('pipeline.function_classifier.get_embedding_from_server')
    @patch('pipeline.function_classifier.OpenAI')
    def test_successful_suggestions(self, mock_openai_class, mock_embeddings, mock_read_json, mock_path_exists):
        """Test successful similarity suggestions."""
        df_to_process = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Function 1', 'Function 2']
        })

        config = {
            'ai_mode': 'online',
            'ai_api_key': 'test-key',
            'embedding_server': 'http://localhost:1234',
            'embedding_model': 'test-model',
            'universal_functions': '/test/universal.json',
            'universal_threshold': 0.75
        }

        # Mock file exists
        mock_path_exists.return_value = True

        # Mock universal functions data
        universal_df = pd.DataFrame({
            'FunctionText': ['Similar function 1', 'Similar function 2'],
            'TrueType': ['Стратегические', 'Регулятивные']
        })
        mock_read_json.return_value = universal_df

        # Mock embeddings (2 new + 2 universal functions)
        # Function 1 embedding is very similar to first universal function
        # Function 2 embedding is similar to second universal function
        embeddings = [
            [1.0, 0.0, 0.0],  # Function 1 embedding
            [0.0, 1.0, 0.0],  # Function 2 embedding (orthogonal)
            [0.9, 0.1, 0.0],  # Similar to function 1 (universal 1)
            [0.0, 0.9, 0.1]   # Similar to function 2 (universal 2)
        ]
        mock_embeddings.return_value = embeddings

        # Mock OpenAI client
        mock_client = Mock()
        mock_openai_class.return_value = mock_client

        result = get_similarity_suggestions(df_to_process, config)

        # Should find suggestions for function 1 (similar to first universal)
        assert 'GO1-1' in result
        assert 'Стратегические' in result['GO1-1']
        # Function 2 should also have suggestions (similar to second universal)
        assert 'GO1-2' in result
        assert 'Регулятивные' in result['GO1-2']

    @patch('pipeline.function_classifier.pd.read_json')
    @patch('pipeline.function_classifier.os.path.exists')
    def test_no_universal_file(self, mock_path_exists, mock_read_json):
        """Test when universal functions file doesn't exist."""
        df_to_process = pd.DataFrame({COL_ID: ['GO1-1']})
        config = {'universal_functions': '/nonexistent/file.json'}

        mock_path_exists.return_value = False  # File doesn't exist

        result = get_similarity_suggestions(df_to_process, config)

        assert result == {}
        mock_path_exists.assert_called_once_with('/nonexistent/file.json')
        mock_read_json.assert_not_called()

    @patch('pipeline.function_classifier.pd.read_json')
    @patch('pipeline.function_classifier.get_embedding_from_server')
    def test_embeddings_failure(self, mock_embeddings, mock_read_json):
        """Test when embeddings fail."""
        df_to_process = pd.DataFrame({COL_ID: ['GO1-1']})
        config = {
            'universal_functions': '/test/universal.json',
            'universal_threshold': 0.75
        }

        mock_read_json.return_value = pd.DataFrame({'FunctionText': ['test'], 'TrueType': ['test']})
        mock_embeddings.return_value = None

        result = get_similarity_suggestions(df_to_process, config)

        assert result == {}


class TestProcessFunctionsWithAI:
    """Test AI processing of functions."""

    @patch('pipeline.function_classifier.create_ai_client')
    @patch('pipeline.function_classifier.call_ai_classifier')
    def test_successful_processing(self, mock_ai_classifier, mock_create_client):
        """Test successful processing of multiple functions."""
        df_to_process = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Function 1', 'Function 2']
        })

        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }
        prompts = {'initial': 'test prompt'}
        suggestions_map = {}

        # Mock client
        mock_client = Mock()
        mock_create_client.return_value = mock_client

        # Mock AI classifier to return successful results
        def mock_classifier_side_effect(client, row, prompts, config, suggestions_map):
            return 'Стратегические', ''

        mock_ai_classifier.side_effect = mock_classifier_side_effect

        result = process_functions_with_ai(df_to_process, config, prompts, suggestions_map)

        assert len(result) == 2
        assert result.iloc[0][COL_FINAL_LABEL] == 'Стратегические'
        assert result.iloc[1][COL_FINAL_LABEL] == 'Стратегические'
        assert result.iloc[0][COL_LABELED_BY] == 'AI'
        assert result.iloc[1][COL_LABELED_BY] == 'AI'
        assert mock_ai_classifier.call_count == 2

    @patch('pipeline.function_classifier.create_ai_client')
    @patch('pipeline.function_classifier.call_ai_classifier')
    def test_processing_failure(self, mock_ai_classifier, mock_create_client):
        """Test handling of processing failures."""
        df_to_process = pd.DataFrame({
            COL_ID: ['GO1-1'],
            COL_TEXT: ['Function 1']
        })

        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }
        prompts = {'initial': 'test prompt'}
        suggestions_map = {}

        mock_client = Mock()
        mock_create_client.return_value = mock_client

        # Mock AI classifier to return error
        def mock_classifier_side_effect(client, row, prompts, config, suggestions_map):
            return 'ОШИБКА', ''

        mock_ai_classifier.side_effect = mock_classifier_side_effect

        result = process_functions_with_ai(df_to_process, config, prompts, suggestions_map)

        # Should still return a dataframe but with error status
        assert len(result) == 1
        assert result.iloc[0][COL_FINAL_LABEL] == 'ОШИБКА'


class TestProcessSingleFunction:
    """Test single function processing."""

    @patch('pipeline.function_classifier.call_ai_classifier')
    def test_successful_classification(self, mock_ai_classifier):
        """Test successful single function classification."""
        row = pd.Series({
            COL_ID: 'GO1-1',
            COL_TEXT: 'Test function text'
        })

        config = {}
        prompts = {'initial': 'test prompt'}
        suggestions_map = {}

        # Mock AI classifier to return strategic type
        mock_ai_classifier.return_value = ('Стратегические', '')

        result = process_single_function(None, row, config, prompts, suggestions_map)

        assert result[COL_TYPE] == 'Стратегические'
        assert result[COL_FINAL_LABEL] == 'Стратегические'
        assert result[COL_LABELED_BY] == 'AI'

    @patch('pipeline.function_classifier.call_ai_classifier')
    def test_error_classification(self, mock_ai_classifier):
        """Test error in classification."""
        row = pd.Series({
            COL_ID: 'GO1-1',
            COL_TEXT: 'Test function text'
        })

        config = {}
        prompts = {}
        suggestions_map = {}

        mock_ai_classifier.return_value = ('ОШИБКА', '')

        result = process_single_function(None, row, config, prompts, suggestions_map)

        assert result[COL_FINAL_LABEL] == 'ОШИБКА'


class TestCallAiClassifier:
    """Test AI classifier calls."""

    def test_parse_valid_json_response(self):
        """Test parsing valid JSON response."""
        response_text = '{"verdict": "Стратегические", "reasoning": "test"}'

        result = parse_json_response(response_text)

        assert result['verdict'] == 'Стратегические'

    def test_parse_invalid_json_response(self):
        """Test parsing invalid JSON response."""
        response_text = 'Not JSON content'

        result = parse_json_response(response_text)

        assert result == {}

    def test_parse_json_with_regex(self):
        """Test parsing JSON using regex."""
        response_text = 'Some text before {"verdict": "Регулятивные"} and after'

        result = parse_json_response(response_text)

        assert result['verdict'] == 'Регулятивные'


class TestCreateAiClient:
    """Test AI client creation."""

    def test_online_mode(self):
        """Test creating client for online mode."""
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key'
        }

        client = create_ai_client(config)

        # Should be OpenAI instance (can't easily test exact configuration without mocking)
        assert client is not None

    def test_local_mode(self):
        """Test creating client for local mode."""
        config = {
            'ai_mode': 'Локальный',
            'embedding_server': 'http://localhost:1234'
        }

        client = create_ai_client(config)

        assert client is not None

    def test_ap_mode(self):
        """Test creating client for AP mode."""
        config = {
            'ai_mode': 'АП',
            'embedding_server': 'http://localhost:1234',
            'ai_api_key': 'test-key'
        }

        client = create_ai_client(config)

        assert client is not None


class TestIntegration:
    """Integration tests for function classifier."""

    @patch('pipeline.function_classifier.load_prompt')
    @patch('pipeline.function_classifier.pd.read_excel')
    @patch('pipeline.function_classifier.pd.DataFrame.to_excel')
    @patch('pipeline.function_classifier.create_ai_client')
    @patch('pipeline.function_classifier.call_ai_classifier')
    def test_end_to_end_classification(self, mock_ai_classifier, mock_create_client, mock_to_excel, mock_read_excel, mock_load_prompt):
        """Test complete function classification workflow."""
        # Setup configuration
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini',
            'universal_functions': '/test/universal.json',
            'log_level': 'INFO',
            'prompt_files': {
                'function_classifier_initial': 'prompts/function_classifier/classify_initial.txt',
                'function_classifier_refinement': 'prompts/function_classifier/classify_refinement.txt',
                'function_classifier_verify': 'prompts/function_classifier/verify_type.txt',
                'function_classifier_arbitrate': 'prompts/function_classifier/arbitrate_dispute.txt',
            }
        }

        # Mock prompt loading
        mock_load_prompt.return_value = 'Test prompt with {embedding_suggestions}'

        # Mock Excel data
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Strategic function', 'Regulatory function']
        })
        mock_read_excel.return_value = df

        # Mock AI client
        mock_client = Mock()
        mock_create_client.return_value = mock_client

        # Mock AI classifier responses
        def mock_classifier_side_effect(client, row, prompts, config, suggestions_map):
            text = row[COL_TEXT]
            if 'Strategic' in text:
                return 'Стратегические', ''
            elif 'Regulatory' in text:
                return 'Регулятивные', ''
            else:
                return DEFAULT_NEEDS_REVIEW_LABEL, ''

        mock_ai_classifier.side_effect = mock_classifier_side_effect

        # Run classification
        result_file = run_function_classifier(config, '/test/input.xlsx')

        assert result_file == '/test/input.xlsx'
        mock_to_excel.assert_called_once_with('/test/input.xlsx', index=False)

        # Verify Excel was written (we can't easily check the content without more complex mocking)
        # The important thing is that no exceptions were raised
