import os
import logging
from typing import Dict, Any

def load_prompt(file_path: str) -> str:
    """Load prompt from external file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read().strip()

def validate_file_exists(file_path: str, description: str) -> None:
    """Validate that a required file exists."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Required {description} not found: {file_path}")

def read_table_auto(fpath: str) -> 'pd.DataFrame':
    """Auto-detect and read Excel or CSV files."""
    import pandas as pd

    low = fpath.lower()
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(fpath)
    if low.endswith(".csv"):
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            for sep in (",", ";", "\t", "|"):
                try:
                    return pd.read_csv(fpath, encoding=enc, sep=sep)
                except Exception as e:
                    last_err = e
        if last_err:
            raise last_err
    raise ValueError(f"Unsupported file format: {fpath}")

def prepare_api_base_url(url: str) -> str:
    """Prepare URL for OpenAI-compatible API."""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url

def get_embedding_from_server(client, model: str, texts: list, retries: int = 3):
    """Get embeddings from server with retry logic."""
    for attempt in range(retries):
        try:
            resp = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in resp.data]
        except Exception as e:
            print(f"Embedding attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                import time
                time.sleep(1)
    return None
