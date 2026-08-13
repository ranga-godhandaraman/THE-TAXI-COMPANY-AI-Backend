#!/usr/bin/env python3
"""
Chunk UK taxi policy markdown docs with structure-aware (header-based) splitting,
embed with BAAI/bge-m3, and upsert into Qdrant.

Env (from .env via python-dotenv):
  QUAD_ENDPOINT  — Qdrant cluster URL
  QUAD_API_KEY   — Qdrant API key
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLICIES_DIR = Path("./uk_taxi_dataset/policies")
COLLECTION_NAME = "uk_taxi_policies"
EMBEDDING_MODEL = "BAAI/bge-m3"
# BGE-M3 dense vector size
VECTOR_SIZE = 1024
BATCH_SIZE = 32

HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass
class Chunk:
    text: str
    source: str
    header_path: str
    headers: dict[str, str]  # e.g. {"h1": "...", "h2": "..."}
    chunk_index: int


# ---------------------------------------------------------------------------
# Structure-aware (header-based) chunking
# ---------------------------------------------------------------------------

def _header_path(headers: dict[int, str]) -> str:
    return " > ".join(headers[level] for level in sorted(headers))


def _headers_payload(headers: dict[int, str]) -> dict[str, str]:
    return {f"h{level}": title for level, title in sorted(headers.items())}


def chunk_markdown_by_headers(text: str, source: str) -> list[Chunk]:
    """
    Split markdown on ATX headers (# … ######).

    Each chunk is one section: the header line plus body until the next header
    of the same or higher level. Nested header ancestry is kept in metadata so
    retrieval stays structure-aware.
    """
    lines = text.splitlines()
    chunks: list[Chunk] = []
    active_headers: dict[int, str] = {}
    buffer: list[str] = []
    section_headers: dict[int, str] = {}
    chunk_index = 0

    def flush() -> None:
        nonlocal chunk_index, buffer, section_headers
        body = "\n".join(buffer).strip()
        if not body:
            buffer = []
            return
        chunks.append(
            Chunk(
                text=body,
                source=source,
                header_path=_header_path(section_headers) if section_headers else "",
                headers=_headers_payload(section_headers),
                chunk_index=chunk_index,
            )
        )
        chunk_index += 1
        buffer = []

    for line in lines:
        match = HEADER_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            # Drop headers at this level or deeper; keep ancestors.
            active_headers = {
                lvl: title_
                for lvl, title_ in active_headers.items()
                if lvl < level
            }
            active_headers[level] = title
            section_headers = dict(active_headers)
            buffer = [line]
        else:
            buffer.append(line)

    flush()

    # Documents with no headers → single whole-doc chunk
    if not chunks and text.strip():
        chunks.append(
            Chunk(
                text=text.strip(),
                source=source,
                header_path="",
                headers={},
                chunk_index=0,
            )
        )

    return chunks


def load_and_chunk_policies(policies_dir: Path) -> list[Chunk]:
    if not policies_dir.is_dir():
        raise FileNotFoundError(f"Policies directory not found: {policies_dir}")

    all_chunks: list[Chunk] = []
    for path in sorted(policies_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        all_chunks.extend(chunk_markdown_by_headers(text, source=path.name))
        print(f"  {path.name}: {sum(1 for c in all_chunks if c.source == path.name)} chunk(s)")

    return all_chunks


# ---------------------------------------------------------------------------
# Embeddings + Qdrant
# ---------------------------------------------------------------------------

def stable_point_id(source: str, chunk_index: int) -> str:
    """Deterministic UUID so re-runs upsert the same points."""
    digest = hashlib.sha256(f"{source}::{chunk_index}".encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


def get_qdrant_client() -> QdrantClient:
    load_dotenv()
    endpoint = os.getenv("QUAD_ENDPOINT")
    api_key = os.getenv("QUAD_API_KEY")
    if not endpoint:
        raise RuntimeError("QUAD_ENDPOINT is not set (expected in .env)")
    if not api_key:
        raise RuntimeError("QUAD_API_KEY is not set (expected in .env)")
    return QdrantClient(url=endpoint, api_key=api_key)


def ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in existing:
        print(f"Collection '{COLLECTION_NAME}' already exists — will upsert.")
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=qm.VectorParams(
            size=VECTOR_SIZE,
            distance=qm.Distance.COSINE,
        ),
    )
    print(f"Created collection '{COLLECTION_NAME}' (dim={VECTOR_SIZE}, cosine).")


def embed_texts(model: SentenceTransformer, texts: list[str]) -> list[list[float]]:
    vectors = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    return [v.tolist() for v in vectors]


def upsert_chunks(
    client: QdrantClient,
    model: SentenceTransformer,
    chunks: list[Chunk],
) -> None:
    if not chunks:
        print("No chunks to upsert.")
        return

    print(f"Embedding {len(chunks)} chunk(s) with {EMBEDDING_MODEL}…")
    vectors = embed_texts(model, [c.text for c in chunks])

    points = [
        qm.PointStruct(
            id=stable_point_id(chunk.source, chunk.chunk_index),
            vector=vector,
            payload={
                "text": chunk.text,
                "source": chunk.source,
                "header_path": chunk.header_path,
                "chunk_index": chunk.chunk_index,
                "chunking": "structure_aware_header",
                "embedding_model": EMBEDDING_MODEL,
                **chunk.headers,
            },
        )
        for chunk, vector in zip(chunks, vectors)
    ]

    client.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)
    print(f"Upserted {len(points)} point(s) into '{COLLECTION_NAME}'.")


def main() -> None:
    print(f"Loading policies from {POLICIES_DIR.resolve()}")
    chunks = load_and_chunk_policies(POLICIES_DIR)
    print(f"Total chunks: {len(chunks)}")

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    client = get_qdrant_client()
    ensure_collection(client)
    upsert_chunks(client, model, chunks)

    info = client.get_collection(COLLECTION_NAME)
    print(f"Done. Collection points: {info.points_count}")


if __name__ == "__main__":
    main()
