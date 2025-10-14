import os
import logging
import torch
from typing import Dict, Any, List, Optional
from sentence_transformers import SentenceTransformer

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

_embedding_model_cache: Optional[SentenceTransformer] = None

def get_embedding_local(model_name: str, texts: List[str], logger: Optional[logging.Logger] = None) -> List[List[float]]:
    """Load and use local sentence-transformers model for embeddings (GPU-accelerated)."""
    global _embedding_model_cache
    
    if logger is None:
        logger = logging.getLogger('functions_analysis')
    
    if _embedding_model_cache is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        logger.info(f"Loading embedding model: {model_name} on {device}")
        _embedding_model_cache = SentenceTransformer(model_name, device=device)
        logger.info(f"Embedding model loaded successfully")
    
    logger.info(f"Generating embeddings for {len(texts)} texts")
    embeddings = _embedding_model_cache.encode(
        texts,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True
    )
    
    return embeddings.tolist()

def get_embedding_from_server(client, model: str, texts: list, retries: int = 3):
    """Get embeddings from server with retry logic."""
    logger = logging.getLogger('functions_analysis')
    for attempt in range(retries):
        try:
            resp = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning(f"Embedding attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                import time
                time.sleep(1)
    return None

def get_embeddings(config: Dict[str, Any], texts: List[str], logger: Optional[logging.Logger] = None) -> Optional[List[List[float]]]:
    """Get embeddings using either local model or API server based on configuration."""
    if logger is None:
        logger = logging.getLogger('functions_analysis')
    
    use_local = os.getenv('USE_LOCAL_EMBEDDINGS', 'true').lower() == 'true'
    
    if use_local and config['ai_mode'] in ('local', 'alternative'):
        model_name = config.get('embedding_model', 'sentence-transformers/paraphrase-multilingual-mpnet-base-v2')
        return get_embedding_local(model_name, texts, logger)
    else:
        from openai import OpenAI
        import httpx
        
        if config['ai_mode'] == 'online':
            embed_client = OpenAI(
                api_key=config['ai_api_key'],
                http_client=httpx.Client(timeout=60.0)
            )
            embedding_model = 'text-embedding-3-small'
        else:
            embed_client = OpenAI(
                base_url=prepare_api_base_url(config['embedding_server']),
                api_key="not-needed",
                http_client=httpx.Client(timeout=60.0)
            )
            embedding_model = config['embedding_model']
        
        return get_embedding_from_server(embed_client, embedding_model, texts)
