"""Query-time encoder for the existing BAAI/bge-m3 collection.

This module only encodes search queries in memory. It never upserts vectors
or re-embeds stored documents.

The SentenceTransformer weights are loaded once per process (not per chat
session) and reused for every request.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import get_settings

# Must match the model used when the collection was populated.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
VECTOR_SIZE = 1024

logger = logging.getLogger(__name__)

_model: Any | None = None
_model_name: str | None = None
_lock = threading.Lock()


def get_embedding_model_name() -> str:
    return get_settings().rag_embedding_model


def _create_model(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    # Prefer the local HF cache — avoid re-checking / re-downloading the Hub
    # on every process start when weights are already present.
    try:
        return SentenceTransformer(model_name, local_files_only=True)
    except Exception:
        logger.info(
            "Embedding model %s not fully cached locally; loading from Hugging Face Hub once",
            model_name,
        )
        return SentenceTransformer(model_name)


def get_embedding_model() -> Any:
    """Return the process-wide embedding model (load once, reuse forever)."""
    global _model, _model_name
    name = get_embedding_model_name()
    if _model is not None and _model_name == name:
        return _model
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        logger.info("Loading embedding model once for this process: %s", name)
        _model = _create_model(name)
        _model_name = name
        logger.info("Embedding model ready (shared across all chat sessions)")
        return _model


def warm_embedding_model() -> None:
    """Eager-load weights at application startup (optional but recommended)."""
    get_embedding_model()


def reset_embedding_model() -> None:
    """Test / shutdown helper — does not run on new chat sessions."""
    global _model, _model_name
    with _lock:
        _model = None
        _model_name = None


def embed_query(query: str) -> list[float]:
    """Encode a single user query into a dense vector for Qdrant search."""
    text = (query or "").strip()
    if not text:
        raise ValueError("query must be a non-empty string")

    model = get_embedding_model()
    vector = model.encode(
        text,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    values = vector.tolist()
    if len(values) != VECTOR_SIZE:
        raise RuntimeError(
            f"Unexpected embedding size {len(values)}; expected {VECTOR_SIZE} "
            f"for {get_embedding_model_name()}"
        )
    return values
