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
    python main.py
    # or with uv:
    uv run python main.py
"""

import os
import sys
import time
from typing import Dict, Any

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.config import load_config, validate_config, setup_logging
from pipeline.utils import load_prompt


def main():
    """Main pipeline execution"""
    print("Starting Functions Analysis Pipeline")

    # Load and validate configuration
    config = load_config()
    validate_config(config)
    logger = setup_logging(config)

    logger.info("Configuration loaded successfully")
    logger.info(f"AI Mode: {config['ai_mode']}")
    logger.info(f"Input folder: {config['input_folder']}")
    logger.info(f"Output Excel: {config['output_excel']}")

    start_time = time.time()

    try:
        # Import pipeline steps dynamically to avoid circular imports
        from pipeline import document_parser, function_classifier, sphere_classifier, collision_detector, markdown_generator

        # Step 1: Parse documents
        logger.info("Step 1: Parsing documents...")
        excel_file = document_parser.run_document_parser(config)
        logger.info(f"Document parsing completed: {excel_file}")

        # Step 2: Classify function types
        logger.info("Step 2: Classifying function types...")
        function_classifier.run_function_classifier(config, excel_file)
        logger.info("Function classification completed")

        # Step 3: Classify spheres
        logger.info("Step 3: Classifying spheres...")
        from pipeline import sphere_classifier
        sphere_classifier.run_sphere_classifier(config, excel_file)
        logger.info("Sphere classification completed")

        # Step 4: Detect collisions
        logger.info("Step 4: Detecting collisions...")
        from pipeline import collision_detector
        collision_detector.run_collision_detector(config, excel_file)
        logger.info("Collision detection completed")

        # Step 5: Generate markdown
        logger.info("Step 5: Generating markdown...")
        from pipeline import markdown_generator
        markdown_generator.run_markdown_generator(config, excel_file)
        logger.info("Markdown generation completed")

        # Final summary
        elapsed_time = time.time() - start_time
        logger.info(f"Pipeline completed successfully in {elapsed_time:.2f}s")
        print(f"Pipeline completed in {elapsed_time:.2f}s")

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        print("\nPipeline interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        print(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
