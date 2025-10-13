#!/usr/bin/env python3
"""
Main entrypoint for the Functions Analysis Pipeline

This script orchestrates the entire pipeline:
1. Document Parser - Extract functions from documents
2. Function Classifier - Classify function types
3. Sphere Classifier - Classify function spheres
4. Collision Detector - Detect and verify collisions
5. Markdown Generator - Generate markdown knowledge base

Usage:
    # Run all stages (default):
    uv run python main.py
    
    # Run specific stages:
    uv run python main.py --stages 1 3 5
    uv run python main.py --stages 3
    
    # Run from a specific stage onwards:
    uv run python main.py --from 3
    
    # List available stages:
    uv run python main.py --list
"""

import os
import sys
import time
import argparse
from typing import Dict, Any, List, Callable

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.config import load_config, validate_config, setup_logging

# Define pipeline stages
STAGES = {
    1: {
        'name': 'document_parser',
        'description': 'Parse documents and extract functions',
        'requires_excel': False,
    },
    2: {
        'name': 'function_classifier',
        'description': 'Classify function types',
        'requires_excel': True,
    },
    3: {
        'name': 'sphere_classifier',
        'description': 'Classify function spheres',
        'requires_excel': True,
    },
    4: {
        'name': 'collision_detector',
        'description': 'Detect and verify collisions',
        'requires_excel': True,
    },
    5: {
        'name': 'markdown_generator',
        'description': 'Generate markdown knowledge base',
        'requires_excel': True,
    },
}


def list_stages():
    """Print available pipeline stages."""
    print("\n📋 Available Pipeline Stages:\n")
    for stage_num, stage_info in STAGES.items():
        print(f"  {stage_num}. {stage_info['name']}")
        print(f"     {stage_info['description']}")
        if stage_info['requires_excel']:
            print(f"     Requires Excel file from previous stages")
        print()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Functions Analysis Pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all stages:
  uv run python main.py
  
  # Run specific stages:
  uv run python main.py --stages 1 3 5
  
  # Run from stage 3 onwards:
  uv run python main.py --from 3
  
  # List available stages:
  uv run python main.py --list
        """
    )
    
    parser.add_argument(
        '--stages',
        nargs='+',
        type=int,
        choices=list(STAGES.keys()),
        help='Specific stage(s) to run (e.g., --stages 1 3 5)'
    )
    
    parser.add_argument(
        '--from',
        dest='from_stage',
        type=int,
        choices=list(STAGES.keys()),
        help='Run from this stage onwards (e.g., --from 3)'
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available stages and exit'
    )
    
    return parser.parse_args()


def run_stage(stage_num: int, config: Dict, excel_file: str, logger) -> str:
    """Run a specific pipeline stage."""
    stage_info = STAGES[stage_num]
    stage_name = stage_info['name']
    
    logger.info(f"Stage {stage_num}: {stage_info['description']}...")
    
    # Import the module dynamically
    module = __import__(f'pipeline.{stage_name}', fromlist=[stage_name])
    runner_func = getattr(module, f'run_{stage_name}')
    
    # Run the stage
    if stage_num == 1:
        # Document parser creates the Excel file
        result = runner_func(config)
        logger.info(f"Stage {stage_num} completed: {result}")
        return result
    else:
        # Other stages update the Excel file
        result = runner_func(config, excel_file)
        logger.info(f"Stage {stage_num} completed")
        return excel_file


def main():
    """Main pipeline execution"""
    args = parse_arguments()
    
    # Handle --list flag
    if args.list:
        list_stages()
        sys.exit(0)
    
    print("Starting Functions Analysis Pipeline\n")
    
    # Load and validate configuration
    config = load_config()
    validate_config(config)
    logger = setup_logging(config)
    
    logger.info("Configuration loaded successfully")
    logger.info(f"AI Mode: {config['ai_mode']}")
    logger.info(f"Input folder: {config['input_folder']}")
    logger.info(f"Output Excel: {config['output_excel']}")
    
    # Determine which stages to run
    if args.stages:
        stages_to_run = sorted(args.stages)
        print(f"Running stages: {', '.join(map(str, stages_to_run))}\n")
    elif args.from_stage:
        stages_to_run = list(range(args.from_stage, len(STAGES) + 1))
        print(f"Running from stage {args.from_stage} onwards: {', '.join(map(str, stages_to_run))}\n")
    else:
        stages_to_run = list(STAGES.keys())
        print(f"Running all stages: {', '.join(map(str, stages_to_run))}\n")
    
    start_time = time.time()
    excel_file = config['output_excel']
    
    try:
        # Run each stage
        for stage_num in stages_to_run:
            stage_info = STAGES[stage_num]
            
            # Check if Excel file is required but doesn't exist
            if stage_info['requires_excel'] and not os.path.exists(excel_file):
                error_msg = f"Stage {stage_num} requires Excel file from stage 1, but {excel_file} doesn't exist. Run stage 1 first."
                logger.error(error_msg)
                print(f"\nError: {error_msg}")
                sys.exit(1)
            
            # Run the stage
            excel_file = run_stage(stage_num, config, excel_file, logger)
        
        # Final summary
        elapsed_time = time.time() - start_time
        logger.info(f"Pipeline completed successfully in {elapsed_time:.2f}s")
        print(f"\nPipeline completed in {elapsed_time:.2f}s")
        
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        print("\n\nPipeline interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        print(f"\nPipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

