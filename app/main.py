"""FastAPI application entrypoint — structured data layer foundation."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import api_router
from app.config import get_settings
from app.agents.llm import reset_groq_client
from app.db.session import dispose_engine, get_engine, require_neon_settings
from app.rag.client import reset_qdrant_client
from app.rag.embeddings import reset_embedding_model, warm_embedding_model


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail clearly at startup if Neon is not configured
    require_neon_settings()
    get_engine()
    # Load RAG encoder once for the process — not per chat session
    try:
        warm_embedding_model()
    except Exception as exc:  # noqa: BLE001
        # Keep API up for non-RAG journeys; first RAG call will retry
        print(f"[startup] Embedding model warmup skipped: {exc}")
    try:
        yield
    finally:
        await dispose_engine()
        reset_qdrant_client()
        reset_groq_client()
        reset_embedding_model()

def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "UK Taxi / PHV Operations Assistant POC backend. "
            "Neon SQL data layer, Qdrant RAG, Analytics, and LangGraph chat routing."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    return app


app = create_app()
