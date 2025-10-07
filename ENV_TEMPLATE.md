# Environment Variables Configuration

This file documents all required environment variables for the Functions Analysis Pipeline.

## Setup Instructions

1. Create a `.env` file in the project root
2. Copy the template below and update with your values
3. **Important**: Do NOT use quotes around values unless they contain spaces

## .env Template

```bash
# ========================================
# CORE SETTINGS (REQUIRED)
# ========================================

# Input folder containing .docx documents to process
INPUT_FOLDER=input_documents/Агенство по делам государственной службы

# Output Excel file for analysis results  
OUTPUT_EXCEL=data/analysis_results.xlsx

# Output folder for generated markdown documentation
OUTPUT_MARKDOWN_FOLDER=data/markdown_output

# Reference files (should point to your existing data)
SPHERES_FILE=files/SPHERES.xlsx
UNIVERSAL_FUNCTIONS_JSON=files/universal_functions.json

# ========================================
# AI CONFIGURATION (REQUIRED)
# ========================================

# AI mode: 'online', 'local', or 'alternative'
#   - 'online': Use OpenAI API (requires OPENAI_API_KEY)
#   - 'local': Use local LLM server (requires EMBEDDING_SERVER, EMBEDDING_MODEL)
#   - 'alternative': Use alternative OpenAI-compatible API (requires all AI settings)
AI_MODE=online

# OpenAI API key (required when AI_MODE=online)
OPENAI_API_KEY=sk-proj-your-api-key-here

# AI model for chat completions (required)
AI_MODEL=gpt-4o-mini

# Embedding server URL (required when AI_MODE=local or alternative)
EMBEDDING_SERVER=http://localhost:1234

# Embedding model name (required when AI_MODE=local or alternative)
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B-GGUF

# ========================================
# PROCESSING PARAMETERS (REQUIRED)
# ========================================

# Similarity threshold for collision detection (0.0-1.0)
SIMILARITY_THRESHOLD=0.5

# Universal similarity threshold for suggestions (0.0-1.0)  
UNIVERSAL_SIMILARITY_THRESHOLD=0.75

# Number of parallel workers for embedding generation
EMBEDDING_WORKERS=8

# Number of parallel workers for AI API calls
AI_WORKERS=30

# Batch size for processing
BATCH_SIZE=64

# ========================================
# LOGGING (REQUIRED)
# ========================================

# Log level: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=INFO
```

## Mode-Specific Requirements

### Online Mode (AI_MODE=online)
**Required variables:**
- `OPENAI_API_KEY` - Your OpenAI API key

**Optional variables:**
- `EMBEDDING_SERVER` - Not used
- `EMBEDDING_MODEL` - Not used

### Local Mode (AI_MODE=local)
**Required variables:**
- `EMBEDDING_SERVER` - URL of your local LLM server
- `EMBEDDING_MODEL` - Model identifier for embeddings

**Optional variables:**
- `OPENAI_API_KEY` - Not used (uses "not-needed" as placeholder)

### Alternative Mode (AI_MODE=alternative)
**Required variables:**
- `OPENAI_API_KEY` - API key for the alternative provider
- `EMBEDDING_SERVER` - URL of the alternative OpenAI-compatible API
- `EMBEDDING_MODEL` - Model identifier

## Common Issues

### Issue: "Missing required environment variables"
**Solution:** Ensure all required variables are defined in your `.env` file.

### Issue: "OPENAI_API_KEY is required when AI_MODE='online'"
**Solution:** Add your OpenAI API key to the `.env` file when using online mode.

### Issue: Values not loading correctly
**Solution:** Remove quotes from values in `.env` file (except for paths with spaces).

### Issue: "Cannot save file into a non-existent directory"
**Solution:** Ensure output directories exist or use relative paths from project root.

## Validation

The pipeline will validate:
1. All required environment variables are present
2. Mode-specific requirements are met (API keys, server URLs)
3. Required files and directories exist (via `validate_config()`)
4. Prompt files are present

If validation fails, the pipeline will exit with a clear error message indicating what's missing.

