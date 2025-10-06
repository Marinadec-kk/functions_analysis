"""
Tests for sphere_classifier module
"""

import pytest
import json
import pandas as pd
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List

from pipeline.sphere_classifier import (
    run_sphere_classifier,
    process_functions_for_spheres,
    process_single_function_for_sphere,
    parse_json_response,
    create_ai_client,
    SPHERES_MAP,
    COL_ID, COL_TEXT, COL_SPHERE
)


class TestRunSphereClassifier:
    """Test the main sphere classifier function."""

    @patch('pipeline.sphere_classifier.setup_logging')
    @patch('pipeline.sphere_classifier.pd.read_excel')
    @patch('pipeline.sphere_classifier.pd.DataFrame.to_excel')
    @patch('pipeline.sphere_classifier.process_functions_for_spheres')
    def test_successful_sphere_classification(self, mock_process, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test successful sphere classification."""
        # Setup mocks
        config = {
            'prompt_files': {
                'sphere_classifier': 'prompts/sphere_classifier/classify_sphere.txt',
            },
            'log_level': 'INFO'
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
            COL_SPHERE: ['01', '02']
        })
        mock_process.return_value = processed_df

        # Run the function
        result = run_sphere_classifier(config, '/test/input.xlsx')

        # Verify
        assert result == '/test/input.xlsx'
        mock_read_excel.assert_called_once_with('/test/input.xlsx')
        mock_process.assert_called_once()
        mock_to_excel.assert_called_once_with('/test/input.xlsx', index=False)

    @patch('pipeline.sphere_classifier.setup_logging')
    @patch('pipeline.sphere_classifier.pd.read_excel')
    @patch('pipeline.sphere_classifier.pd.DataFrame.to_excel')
    def test_no_functions_to_process(self, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test when no functions need processing."""
        config = {
            'prompt_files': {
                'sphere_classifier': 'prompts/sphere_classifier/classify_sphere.txt',
            },
            'log_level': 'INFO'
        }

        # Mock Excel with existing sphere classifications
        mock_df = pd.DataFrame({
            COL_ID: ['GO1-1'],
            COL_TEXT: ['Function 1 text'],
            COL_SPHERE: ['01']
        })
        mock_read_excel.return_value = mock_df

        result = run_sphere_classifier(config, '/test/input.xlsx')

        assert result == '/test/input.xlsx'
        # Should not try to save if no processing needed
        mock_to_excel.assert_not_called()

    @patch('pipeline.sphere_classifier.setup_logging')
    @patch('pipeline.sphere_classifier.pd.read_excel')
    @patch('pipeline.sphere_classifier.pd.DataFrame.to_excel')
    def test_empty_dataframe(self, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test with empty dataframe."""
        config = {
            'prompt_files': {
                'sphere_classifier': 'prompts/sphere_classifier/classify_sphere.txt',
            },
            'log_level': 'INFO'
        }

        mock_df = pd.DataFrame()
        mock_read_excel.return_value = mock_df

        result = run_sphere_classifier(config, '/test/input.xlsx')

        assert result == '/test/input.xlsx'
        # Should not try to save if no data
        mock_to_excel.assert_not_called()


class TestProcessFunctionsForSpheres:
    """Test AI processing of functions for spheres."""

    @patch('pipeline.sphere_classifier.create_ai_client')
    @patch('pipeline.sphere_classifier.process_single_function_for_sphere')
    def test_successful_processing(self, mock_single_process, mock_create_client):
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
        prompt = 'Test prompt'

        # Mock client
        mock_client = Mock()
        mock_create_client.return_value = mock_client

        # Mock single function processing
        def mock_process_side_effect(client, row, config, prompt):
            result = row.copy()
            result[COL_SPHERE] = '01'
            return result

        mock_single_process.side_effect = mock_process_side_effect

        result = process_functions_for_spheres(df_to_process, config, prompt)

        assert len(result) == 2
        assert result.iloc[0][COL_SPHERE] == '01'
        assert result.iloc[1][COL_SPHERE] == '01'
        assert mock_single_process.call_count == 2

    @patch('pipeline.sphere_classifier.create_ai_client')
    @patch('pipeline.sphere_classifier.process_single_function_for_sphere')
    def test_processing_failure(self, mock_single_process, mock_create_client):
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
        prompt = 'Test prompt'

        mock_client = Mock()
        mock_create_client.return_value = mock_client
        mock_single_process.return_value = None  # Simulate failure

        result = process_functions_for_spheres(df_to_process, config, prompt)

        # Should still return a dataframe but with error status
        assert len(result) == 1
        assert result.iloc[0][COL_SPHERE] == 'ОШИБКА'


class TestProcessSingleFunctionForSphere:
    """Test single function sphere processing."""

    @patch('pipeline.sphere_classifier.parse_json_response')
    def test_successful_sphere_classification(self, mock_parse_json):
        """Test successful single function sphere classification."""
        row = pd.Series({
            COL_ID: 'GO1-1',
            COL_TEXT: 'Test function text'
        })

        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }
        prompt = 'Test prompt'

        # Mock AI response - should return code format like "01.1"
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"name": "01.1"}'

        # Mock OpenAI client
        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_parse_json.return_value = {"name": "01.1"}

        result = process_single_function_for_sphere(mock_client, row, config, prompt)

        assert result[COL_SPHERE] == '01.1'

    @patch('pipeline.sphere_classifier.parse_json_response')
    def test_no_match_sphere_classification(self, mock_parse_json):
        """Test no match sphere classification."""
        row = pd.Series({
            COL_ID: 'GO1-1',
            COL_TEXT: 'Test function text'
        })

        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }
        prompt = 'Test prompt'

        # Mock AI response for no match
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"name": "NO_MATCH"}'

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_parse_json.return_value = {"name": "NO_MATCH"}

        result = process_single_function_for_sphere(mock_client, row, config, prompt)

        assert result[COL_SPHERE] == 'NO_MATCH'

    @patch('pipeline.sphere_classifier.parse_json_response')
    def test_invalid_sphere_classification(self, mock_parse_json):
        """Test invalid sphere classification."""
        row = pd.Series({
            COL_ID: 'GO1-1',
            COL_TEXT: 'Test function text'
        })

        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }
        prompt = 'Test prompt'

        # Mock AI response with invalid sphere
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"name": "INVALID_SPHERE"}'

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_parse_json.return_value = {"name": "INVALID_SPHERE"}

        result = process_single_function_for_sphere(mock_client, row, config, prompt)

        assert result[COL_SPHERE] == 'ОШИБКА'


class TestParseJsonResponse:
    """Test JSON response parsing."""

    def test_parse_valid_json_response(self):
        """Test parsing valid JSON response."""
        response_text = '{"name": "01.1 ИСПОЛНИТЕЛЬНЫЕ И ЗАКОНОДАТЕЛЬНЫЕ ОРГАНЫ, БЮДЖЕТНО-ФИНАНСОВЫЕ ВОПРОСЫ, МЕЖДУНАРОДНЫЕ ОТНОШЕНИЯ"}'

        result = parse_json_response(response_text)

        assert result['name'] == '01.1 ИСПОЛНИТЕЛЬНЫЕ И ЗАКОНОДАТЕЛЬНЫЕ ОРГАНЫ, БЮДЖЕТНО-ФИНАНСОВЫЕ ВОПРОСЫ, МЕЖДУНАРОДНЫЕ ОТНОШЕНИЯ'

    def test_parse_invalid_json_response(self):
        """Test parsing invalid JSON response."""
        response_text = 'Not JSON content'

        result = parse_json_response(response_text)

        assert result == {}

    def test_parse_json_with_regex(self):
        """Test parsing JSON using regex."""
        response_text = 'Some text before {"name": "02 ОБОРОНА"} and after'

        result = parse_json_response(response_text)

        assert result['name'] == '02 ОБОРОНА'


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
    """Integration tests for sphere classifier."""

    @patch('pipeline.sphere_classifier.load_prompt')
    @patch('pipeline.sphere_classifier.pd.read_excel')
    @patch('pipeline.sphere_classifier.pd.DataFrame.to_excel')
    @patch('pipeline.sphere_classifier.create_ai_client')
    @patch('pipeline.sphere_classifier.process_single_function_for_sphere')
    def test_end_to_end_sphere_classification(self, mock_single_process, mock_create_client, mock_to_excel, mock_read_excel, mock_load_prompt):
        """Test complete sphere classification workflow."""
        # Setup configuration
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini',
            'log_level': 'INFO',
            'prompt_files': {
                'sphere_classifier': 'prompts/sphere_classifier/classify_sphere.txt',
            }
        }

        # Mock prompt loading
        mock_load_prompt.return_value = 'Test sphere classification prompt'

        # Mock Excel data
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Government function 1', 'Government function 2']
        })
        mock_read_excel.return_value = df

        # Mock AI client
        mock_client = Mock()
        mock_create_client.return_value = mock_client

        # Mock single function processing
        def mock_process_side_effect(client, row, config, prompt):
            result = row.copy()
            result[COL_SPHERE] = '01'
            return result

        mock_single_process.side_effect = mock_process_side_effect

        # Run classification
        result_file = run_sphere_classifier(config, '/test/input.xlsx')

        assert result_file == '/test/input.xlsx'
        mock_to_excel.assert_called_once_with('/test/input.xlsx', index=False)

        # Verify Excel was written (we can't easily check the content without more complex mocking)
        # The important thing is that no exceptions were raised
