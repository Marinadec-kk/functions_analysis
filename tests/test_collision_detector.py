"""
Tests for collision_detector module
"""

import pytest
import json
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List, Set, Tuple

from pipeline.collision_detector import (
    run_collision_detector,
    find_collision_candidates,
    verify_collisions_with_ai,
    create_collision_verification_prompt,
    call_ai_for_collision_verification,
    update_collision_results,
    parse_json_response,
    create_ai_client,
    COL_ID, COL_TEXT, COL_EXECUTOR, COL_TYPE, COL_SPHERE, COL_COLLISION_GROUP, COL_COLLISION_VERDICT
)


class TestRunCollisionDetector:
    """Test the main collision detector function."""

    @patch('pipeline.collision_detector.setup_logging')
    @patch('pipeline.collision_detector.pd.read_excel')
    @patch('pipeline.collision_detector.pd.DataFrame.to_excel')
    @patch('pipeline.collision_detector.find_collision_candidates')
    @patch('pipeline.collision_detector.verify_collisions_with_ai')
    @patch('pipeline.collision_detector.update_collision_results')
    def test_successful_collision_detection(self, mock_update, mock_verify, mock_find, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test successful collision detection."""
        # Setup mocks
        config = {
            'prompt_files': {
                'collision_detector': 'prompts/collision_detector/verify_collision.txt',
            },
            'log_level': 'INFO',
            'similarity_threshold': 0.5
        }

        # Mock Excel data
        mock_df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2', 'GO2-1'],
            COL_TEXT: ['Function 1 text', 'Function 2 text', 'Function 3 text'],
            COL_EXECUTOR: ['Agency 1', 'Agency 1', 'Agency 2']
        })
        mock_read_excel.return_value = mock_df

        # Mock collision pairs
        collision_pairs = {('GO1-1', 'GO2-1')}
        mock_find.return_value = collision_pairs

        # Mock verified collisions
        verified_collisions = {('GO1-1', 'GO2-1'): 'REAL'}
        mock_verify.return_value = verified_collisions

        # Mock updated dataframe
        updated_df = mock_df.copy()
        updated_df[COL_COLLISION_GROUP] = ['COLLISION_1', 'NO_COLLISION', 'COLLISION_1']
        updated_df[COL_COLLISION_VERDICT] = ['REAL', 'FALSE', 'REAL']
        mock_update.return_value = updated_df

        # Run the function
        result = run_collision_detector(config, '/test/input.xlsx')

        # Verify
        assert result == '/test/input.xlsx'
        mock_read_excel.assert_called_once_with('/test/input.xlsx')
        mock_find.assert_called_once()
        mock_verify.assert_called_once()
        mock_update.assert_called_once()
        mock_to_excel.assert_called_once_with('/test/input.xlsx', index=False)

    @patch('pipeline.collision_detector.setup_logging')
    @patch('pipeline.collision_detector.pd.read_excel')
    @patch('pipeline.collision_detector.pd.DataFrame.to_excel')
    def test_no_collision_candidates(self, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test when no collision candidates are found."""
        config = {
            'prompt_files': {
                'collision_detector': 'prompts/collision_detector/verify_collision.txt',
            },
            'log_level': 'INFO'
        }

        # Mock Excel data
        mock_df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Function 1 text', 'Function 2 text'],
            COL_EXECUTOR: ['Agency 1', 'Agency 2']
        })
        mock_read_excel.return_value = mock_df

        # Run the function
        result = run_collision_detector(config, '/test/input.xlsx')

        # Verify
        assert result == '/test/input.xlsx'
        mock_to_excel.assert_called_once_with('/test/input.xlsx', index=False)

    @patch('pipeline.collision_detector.setup_logging')
    @patch('pipeline.collision_detector.pd.read_excel')
    @patch('pipeline.collision_detector.pd.DataFrame.to_excel')
    def test_no_functions_to_process(self, mock_to_excel, mock_read_excel, mock_setup_logging):
        """Test when no functions need processing."""
        config = {
            'prompt_files': {
                'collision_detector': 'prompts/collision_detector/verify_collision.txt',
            },
            'log_level': 'INFO'
        }

        # Mock Excel with existing collision data
        mock_df = pd.DataFrame({
            COL_ID: ['GO1-1'],
            COL_TEXT: ['Function 1 text'],
            COL_COLLISION_GROUP: ['COLLISION_1']
        })
        mock_read_excel.return_value = mock_df

        result = run_collision_detector(config, '/test/input.xlsx')

        assert result == '/test/input.xlsx'
        # Should not try to save if no processing needed
        mock_to_excel.assert_not_called()


class TestFindCollisionCandidates:
    """Test collision candidate detection."""

    @patch('pipeline.collision_detector.get_embedding_from_server')
    @patch('pipeline.collision_detector.create_ai_client')
    def test_successful_candidate_detection(self, mock_create_client, mock_embeddings):
        """Test successful collision candidate detection."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2', 'GO2-1'],
            COL_TEXT: ['Function 1 text', 'Function 2 text', 'Function 3 text'],
            COL_EXECUTOR: ['Agency 1', 'Agency 1', 'Agency 2']
        })

        config = {
            'embedding_model': 'test-model',
            'similarity_threshold': 0.8
        }

        # Mock embeddings with high similarity between GO1-1 and GO2-1
        embeddings = [
            [1.0, 0.0, 0.0],  # GO1-1
            [0.5, 0.5, 0.0],  # GO1-2 (different from GO1-1)
            [0.9, 0.1, 0.0]   # GO2-1 (similar to GO1-1)
        ]
        mock_embeddings.return_value = embeddings

        mock_client = Mock()
        mock_create_client.return_value = mock_client

        result = find_collision_candidates(df, config)

        # Should find collision between GO1-1 and GO2-1 (different executors, high similarity)
        # Should not find collision between GO1-1 and GO1-2 (same executor)
        assert ('GO1-1', 'GO2-1') in result
        assert ('GO1-1', 'GO1-2') not in result

    @patch('pipeline.collision_detector.get_embedding_from_server')
    def test_embeddings_failure(self, mock_embeddings):
        """Test when embeddings fail."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Function 1 text', 'Function 2 text'],
            COL_EXECUTOR: ['Agency 1', 'Agency 2']
        })

        config = {'embedding_model': 'test-model'}

        mock_embeddings.return_value = None

        result = find_collision_candidates(df, config)

        assert result == set()


class TestVerifyCollisionsWithAI:
    """Test AI collision verification."""

    @patch('pipeline.collision_detector.call_ai_for_collision_verification')
    def test_successful_collision_verification(self, mock_ai_call):
        """Test successful collision verification."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO2-1'],
            COL_TEXT: ['Function 1 text', 'Function 2 text'],
            COL_EXECUTOR: ['Agency 1', 'Agency 2'],
            COL_TYPE: ['Регулятивные', 'Регулятивные'],
            COL_SPHERE: ['01', '01']
        })

        collision_pairs = {('GO1-1', 'GO2-1')}
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }
        prompt = 'Test collision verification prompt'

        mock_ai_call.return_value = 'REAL'

        result = verify_collisions_with_ai(df, collision_pairs, config, prompt)

        assert result[('GO1-1', 'GO2-1')] == 'REAL'
        mock_ai_call.assert_called_once()

    @patch('pipeline.collision_detector.call_ai_for_collision_verification')
    def test_collision_verification_error(self, mock_ai_call):
        """Test collision verification error handling."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO2-1'],
            COL_TEXT: ['Function 1 text', 'Function 2 text']
        })

        collision_pairs = {('GO1-1', 'GO2-1')}
        config = {}
        prompt = 'Test prompt'

        mock_ai_call.side_effect = Exception("AI Error")

        result = verify_collisions_with_ai(df, collision_pairs, config, prompt)

        assert result[('GO1-1', 'GO2-1')] == 'ERROR'


class TestCreateCollisionVerificationPrompt:
    """Test collision verification prompt creation."""

    def test_prompt_creation(self):
        """Test creation of collision verification prompt."""
        func1 = pd.Series({
            COL_ID: 'GO1-1',
            COL_TEXT: 'Function 1 text',
            COL_EXECUTOR: 'Agency 1',
            COL_TYPE: 'Регулятивные',
            COL_SPHERE: '01'
        })

        func2 = pd.Series({
            COL_ID: 'GO2-1',
            COL_TEXT: 'Function 2 text',
            COL_EXECUTOR: 'Agency 2',
            COL_TYPE: 'Регулятивные',
            COL_SPHERE: '01'
        })

        base_prompt = 'Base collision prompt'
        result = create_collision_verification_prompt(base_prompt, func1, func2)

        assert 'FUNCTION 1:' in result
        assert 'FUNCTION 2:' in result
        assert 'GO1-1' in result
        assert 'GO2-1' in result
        assert 'Agency 1' in result
        assert 'Agency 2' in result


class TestCallAiForCollisionVerification:
    """Test AI collision verification calls."""

    @patch('pipeline.collision_detector.parse_json_response')
    def test_successful_ai_verification(self, mock_parse_json):
        """Test successful AI collision verification."""
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }

        # Mock AI response
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"verdict": "REAL"}'

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_parse_json.return_value = {"verdict": "REAL"}

        with patch('pipeline.collision_detector.create_ai_client', return_value=mock_client):
            result = call_ai_for_collision_verification('Test prompt', config)

        assert result == 'REAL'

    @patch('pipeline.collision_detector.parse_json_response')
    def test_invalid_ai_response(self, mock_parse_json):
        """Test invalid AI response."""
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini'
        }

        # Mock AI response with invalid verdict
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"verdict": "INVALID"}'

        mock_client = Mock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_parse_json.return_value = {"verdict": "INVALID"}

        with patch('pipeline.collision_detector.create_ai_client', return_value=mock_client):
            result = call_ai_for_collision_verification('Test prompt', config)

        assert result == 'ERROR'


class TestUpdateCollisionResults:
    """Test collision results updating."""

    def test_update_with_real_collisions(self):
        """Test updating dataframe with real collisions."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2', 'GO2-1'],
            COL_TEXT: ['Function 1', 'Function 2', 'Function 3']
        })

        verified_collisions = {
            ('GO1-1', 'GO2-1'): 'REAL',
            ('GO1-2', 'GO1-1'): 'FALSE'  # This should not create a collision
        }

        result = update_collision_results(df, verified_collisions)

        # GO1-1 and GO2-1 should be in the same collision group
        assert result.loc[result[COL_ID] == 'GO1-1', COL_COLLISION_GROUP].iloc[0] == 'COLLISION_1'
        assert result.loc[result[COL_ID] == 'GO2-1', COL_COLLISION_GROUP].iloc[0] == 'COLLISION_1'
        assert result.loc[result[COL_ID] == 'GO1-2', COL_COLLISION_GROUP].iloc[0] == 'NO_COLLISION'

        # Verdicts should be set correctly
        assert result.loc[result[COL_ID] == 'GO1-1', COL_COLLISION_VERDICT].iloc[0] == 'REAL'
        assert result.loc[result[COL_ID] == 'GO2-1', COL_COLLISION_VERDICT].iloc[0] == 'REAL'
        assert result.loc[result[COL_ID] == 'GO1-2', COL_COLLISION_VERDICT].iloc[0] == 'FALSE'


class TestParseJsonResponse:
    """Test JSON response parsing."""

    def test_parse_valid_json_response(self):
        """Test parsing valid JSON response."""
        response_text = '{"verdict": "REAL", "reasoning": "test"}'

        result = parse_json_response(response_text)

        assert result['verdict'] == 'REAL'

    def test_parse_invalid_json_response(self):
        """Test parsing invalid JSON response."""
        response_text = 'Not JSON content'

        result = parse_json_response(response_text)

        assert result == {}

    def test_parse_json_with_regex(self):
        """Test parsing JSON using regex."""
        response_text = 'Some text before {"verdict": "FALSE"} and after'

        result = parse_json_response(response_text)

        assert result['verdict'] == 'FALSE'


class TestCreateAiClient:
    """Test AI client creation."""

    def test_online_mode(self):
        """Test creating client for online mode."""
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key'
        }

        client = create_ai_client(config)

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
    """Integration tests for collision detector."""

    @patch('pipeline.collision_detector.load_prompt')
    @patch('pipeline.collision_detector.pd.read_excel')
    @patch('pipeline.collision_detector.pd.DataFrame.to_excel')
    @patch('pipeline.collision_detector.find_collision_candidates')
    @patch('pipeline.collision_detector.verify_collisions_with_ai')
    @patch('pipeline.collision_detector.update_collision_results')
    def test_end_to_end_collision_detection(self, mock_update, mock_verify, mock_find, mock_to_excel, mock_read_excel, mock_load_prompt):
        """Test complete collision detection workflow."""
        # Setup configuration
        config = {
            'ai_mode': 'Онлайн',
            'ai_api_key': 'test-key',
            'ai_model': 'gpt-4o-mini',
            'log_level': 'INFO',
            'similarity_threshold': 0.8,
            'prompt_files': {
                'collision_detector': 'prompts/collision_detector/verify_collision.txt',
            }
        }

        # Mock prompt loading
        mock_load_prompt.return_value = 'Test collision detection prompt'

        # Mock Excel data
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2', 'GO2-1'],
            COL_TEXT: ['Similar function 1', 'Different function', 'Similar function 2'],
            COL_EXECUTOR: ['Agency 1', 'Agency 1', 'Agency 2']
        })
        mock_read_excel.return_value = df

        # Mock collision detection
        collision_pairs = {('GO1-1', 'GO2-1')}
        mock_find.return_value = collision_pairs

        # Mock AI verification
        verified_collisions = {('GO1-1', 'GO2-1'): 'REAL'}
        mock_verify.return_value = verified_collisions

        # Mock updated dataframe
        updated_df = df.copy()
        updated_df[COL_COLLISION_GROUP] = ['COLLISION_1', 'NO_COLLISION', 'COLLISION_1']
        updated_df[COL_COLLISION_VERDICT] = ['REAL', 'FALSE', 'REAL']
        mock_update.return_value = updated_df

        # Run collision detection
        result_file = run_collision_detector(config, '/test/input.xlsx')

        assert result_file == '/test/input.xlsx'
        mock_to_excel.assert_called_once_with('/test/input.xlsx', index=False)

        # Verify all steps were called
        mock_find.assert_called_once()
        mock_verify.assert_called_once()
        mock_update.assert_called_once()
