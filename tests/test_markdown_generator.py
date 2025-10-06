"""
Tests for markdown_generator module
"""

import pytest
import json
import pandas as pd
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List
from collections import namedtuple

from pipeline.markdown_generator import (
    run_markdown_generator,
    generate_hierarchical_structure,
    create_level_markdown_file,
    generate_collision_groups,
    create_collision_group_file,
    generate_ministry_portraits,
    create_ministry_portrait,
    sanitize_filename,
    COL_ID, COL_TEXT, COL_EXECUTOR, COL_TYPE, COL_SPHERE, COL_SPHERE_2,
    COL_COLLISION_GROUP, COL_COLLISION_VERDICT
)


class TestRunMarkdownGenerator:
    """Test the main markdown generator function."""

    @patch('pipeline.markdown_generator.setup_logging')
    @patch('pipeline.markdown_generator.pd.read_excel')
    @patch('pipeline.markdown_generator.os.makedirs')
    @patch('pipeline.markdown_generator.generate_hierarchical_structure')
    @patch('pipeline.markdown_generator.generate_collision_groups')
    @patch('pipeline.markdown_generator.generate_ministry_portraits')
    def test_successful_markdown_generation(self, mock_portraits, mock_collision, mock_hierarchy, mock_makedirs, mock_read_excel, mock_setup_logging):
        """Test successful markdown generation."""
        config = {
            'output_markdown': '/test/output',
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
        result = run_markdown_generator(config, '/test/input.xlsx')

        # Verify
        assert result == '/test/output'
        mock_read_excel.assert_called_once_with('/test/input.xlsx')
        mock_makedirs.assert_called_once_with('/test/output', exist_ok=True)
        mock_hierarchy.assert_called_once()
        mock_collision.assert_called_once()
        mock_portraits.assert_called_once()

    @patch('pipeline.markdown_generator.setup_logging')
    @patch('pipeline.markdown_generator.pd.read_excel')
    @patch('pipeline.markdown_generator.os.makedirs')
    def test_empty_dataframe(self, mock_makedirs, mock_read_excel, mock_setup_logging):
        """Test with empty dataframe."""
        config = {'output_markdown': '/test/output'}

        mock_df = pd.DataFrame()
        mock_read_excel.return_value = mock_df

        result = run_markdown_generator(config, '/test/input.xlsx')

        assert result == '/test/output'


class TestGenerateHierarchicalStructure:
    """Test hierarchical structure generation."""

    @patch('pipeline.markdown_generator.create_level_markdown_file')
    def test_hierarchical_structure_generation(self, mock_create_file):
        """Test hierarchical structure generation."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2', 'GO2-1'],
            COL_TEXT: ['Function 1', 'Function 2', 'Function 3'],
            COL_EXECUTOR: ['Agency 1', 'Agency 1', 'Agency 2'],
            'Level': ['1', '1', '2']
        })

        output_folder = '/test/output'
        logger = Mock()

        generate_hierarchical_structure(df, output_folder, logger)

        # Should create files for levels 1 and 2
        assert mock_create_file.call_count == 2


class TestCreateLevelMarkdownFile:
    """Test level markdown file creation."""

    def test_level_file_creation(self):
        """Test creation of level markdown file."""
        functions = [
            pd.Series({COL_ID: 'GO1-1', COL_TEXT: 'Function 1 text', COL_EXECUTOR: 'Agency 1', COL_TYPE: 'Регулятивные'}),
            pd.Series({COL_ID: 'GO1-2', COL_TEXT: 'Function 2 text', COL_EXECUTOR: 'Agency 1', COL_TYPE: 'Реализационные'})
        ]

        output_folder = '/test/output'
        logger = Mock()

        # Create temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            create_level_markdown_file('1', functions, temp_dir, logger)

            # Check that file was created
            expected_file = os.path.join(temp_dir, 'level_1_functions.md')
            assert os.path.exists(expected_file)

            # Check file content
            with open(expected_file, 'r', encoding='utf-8') as f:
                content = f.read()

            assert '# Функции уровня 1' in content
            assert '#Центральные_аппараты' in content
            assert 'GO1-1' in content
            assert 'GO1-2' in content
            assert 'Регулятивные' in content
            assert 'Реализационные' in content


class TestGenerateCollisionGroups:
    """Test collision group generation."""

    @patch('pipeline.markdown_generator.create_collision_group_file')
    def test_collision_group_generation(self, mock_create_file):
        """Test collision group generation."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2', 'GO2-1'],
            COL_COLLISION_GROUP: ['COLLISION_1', 'COLLISION_1', 'COLLISION_2']
        })

        output_folder = '/test/output'
        logger = Mock()

        generate_collision_groups(df, output_folder, logger)

        # Should create 2 collision group files
        assert mock_create_file.call_count == 2


class TestCreateCollisionGroupFile:
    """Test collision group file creation."""

    def test_collision_group_file_creation(self):
        """Test creation of collision group file."""
        functions = [
            pd.Series({
                COL_ID: 'GO1-1',
                COL_TEXT: 'Function 1 text',
                COL_EXECUTOR: 'Agency 1',
                COL_COLLISION_VERDICT: 'REAL'
            }),
            pd.Series({
                COL_ID: 'GO2-1',
                COL_TEXT: 'Function 2 text',
                COL_EXECUTOR: 'Agency 2',
                COL_COLLISION_VERDICT: 'REAL'
            })
        ]

        output_folder = '/test/output'
        logger = Mock()

        # Create temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            create_collision_group_file('COLLISION_1', functions, temp_dir, logger)

            # Check that file was created
            expected_file = os.path.join(temp_dir, 'collision_COLLISION_1.md')
            assert os.path.exists(expected_file)

            # Check file content
            with open(expected_file, 'r', encoding='utf-8') as f:
                content = f.read()

            assert '# Группа коллизий: COLLISION_1' in content
            assert 'GO1-1' in content
            assert 'GO2-1' in content
            assert 'Agency 1' in content
            assert 'Agency 2' in content
            assert 'REAL' in content


class TestGenerateMinistryPortraits:
    """Test ministry portrait generation."""

    @patch('pipeline.markdown_generator.create_ministry_portrait')
    def test_ministry_portrait_generation(self, mock_create_portrait):
        """Test ministry portrait generation."""
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2', 'GO2-1'],
            COL_EXECUTOR: ['Agency 1', 'Agency 1', 'Agency 2']
        })

        output_folder = '/test/output'
        logger = Mock()

        generate_ministry_portraits(df, output_folder, logger)

        # Should create 2 ministry portraits
        assert mock_create_portrait.call_count == 2


class TestCreateMinistryPortrait:
    """Test ministry portrait creation."""

    def test_ministry_portrait_creation(self):
        """Test creation of ministry portrait."""
        functions = [
            pd.Series({
                COL_ID: 'GO1-1',
                COL_TEXT: 'Function 1 text',
                COL_TYPE: 'Регулятивные',
                COL_SPHERE: '01',
                COL_COLLISION_GROUP: 'NO_COLLISION'
            }),
            pd.Series({
                COL_ID: 'GO1-2',
                COL_TEXT: 'Function 2 text',
                COL_TYPE: 'Реализационные',
                COL_SPHERE: '02',
                COL_COLLISION_GROUP: 'COLLISION_1'
            })
        ]

        output_folder = '/test/output'
        logger = Mock()

        # Create temporary directory for testing
        with tempfile.TemporaryDirectory() as temp_dir:
            create_ministry_portrait('Agency 1', functions, temp_dir, logger)

            # Check that file was created
            expected_file = os.path.join(temp_dir, 'portrait_Agency_1.md')
            assert os.path.exists(expected_file)

            # Check file content
            with open(expected_file, 'r', encoding='utf-8') as f:
                content = f.read()

            assert '# Портрет исполнителя: Agency 1' in content
            assert '**Общее количество функций:** 2' in content
            assert '**Функций с коллизиями:** 1' in content
            assert 'GO1-1' in content
            assert 'GO1-2' in content
            assert 'Регулятивные' in content
            assert 'Реализационные' in content


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_sanitize_valid_filename(self):
        """Test sanitizing valid filename."""
        result = sanitize_filename('Valid Name')
        assert result == 'Valid_Name'

    def test_sanitize_filename_with_special_chars(self):
        """Test sanitizing filename with special characters."""
        result = sanitize_filename('Name with <>:|?* chars')
        assert result == 'Name_with_chars'

    def test_sanitize_empty_filename(self):
        """Test sanitizing empty filename."""
        result = sanitize_filename('')
        assert result == 'unnamed'

    def test_sanitize_none_filename(self):
        """Test sanitizing None filename."""
        result = sanitize_filename(None)
        assert result == 'unnamed'


class TestIntegration:
    """Integration tests for markdown generator."""

    def test_end_to_end_markdown_generation(self):
        """Test complete markdown generation workflow with temporary directory."""
        config = {
            'output_markdown': '/tmp/test_markdown_output',
            'log_level': 'INFO'
        }

        # Create test data
        df = pd.DataFrame({
            COL_ID: ['GO1-1', 'GO1-2'],
            COL_TEXT: ['Function 1 text', 'Function 2 text'],
            COL_EXECUTOR: ['Agency 1', 'Agency 2'],
            COL_TYPE: ['Регулятивные', 'Реализационные'],
            COL_SPHERE: ['01', '02'],
            COL_COLLISION_GROUP: ['NO_COLLISION', 'NO_COLLISION'],
            COL_COLLISION_VERDICT: ['FALSE', 'FALSE'],
            'Level': ['1', '1']
        })

        # Write test data to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xlsx', delete=False) as f:
            df.to_excel(f.name, index=False)
            temp_excel_file = f.name

        try:
            # Run markdown generation
            result_folder = run_markdown_generator(config, temp_excel_file)

            assert result_folder == '/tmp/test_markdown_output'

            # Check that files were created
            assert os.path.exists(os.path.join(result_folder, 'level_1_functions.md'))

            # Check file content
            with open(os.path.join(result_folder, 'level_1_functions.md'), 'r', encoding='utf-8') as f:
                content = f.read()
                assert 'Функции уровня 1' in content
                assert 'GO1-1' in content
                assert 'GO1-2' in content

        finally:
            # Clean up
            if os.path.exists(temp_excel_file):
                os.unlink(temp_excel_file)
            if os.path.exists('/tmp/test_markdown_output'):
                import shutil
                shutil.rmtree('/tmp/test_markdown_output')
