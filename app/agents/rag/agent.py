"""RAG Agent — grounded answers from the existing Qdrant policy knowledge base."""

from __future__ import annotations

import re
from typing import Any

from app.agents.llm import chat_json, require_llm_settings
from app.agents.rag.models import RagAgentResult, RagSource
from app.config import Settings, get_settings
from app.rag import QdrantConfigError, QdrantUnavailableError, retrieve_documents
from app.rag.schemas import RetrievalHit

# Reject clear operational / live-fleet questions (SQL Agent territory)
_OPERATIONAL_PATTERNS = (
    r"\bhow many\b.+\b(vehicle|car|taxi|driver|trip|booking|available)\b",
    r"\b(count|number of)\b.+\b(vehicle|car|taxi|driver|trip|booking)\b",
    r"\baverage\b.+\b(distance|duration|fare|rating|trip)\b",
    r"\b(total|sum)\b.+\b(revenue|fare|demand)\b",
    r"\brevenue\b",
    r"\bavailable (vehicles|cars|taxis)\b.+\b(in|at|near)\b",
    r"\b(vehicles|cars)\b.+\bavailable\b.+\b(in|at|near)\b",
    r"\bunserved (demand|requests)\b",
    r"\bhighest demand\b",
    r"\blowest (availability|demand)\b",
    r"\bwhich city has\b",
    r"\bcancellation rate\b",
)

MIN_SCORE = 0.35
INSUFFICIENT_MSG = (
    "The available knowledge base does not contain enough information "
    "to answer this question."
)
OPERATIONAL_MSG = (
    "This looks like an operational or live-data question "
    "(for example fleet counts, revenue, or trip statistics). "
    "The RAG Agent only answers policy and reference questions from the "
    "document knowledge base. Please use the SQL Agent / database for "
    "operational queries."
)

ANSWER_SYSTEM = """You are a UK taxi/PHV policy assistant.

You MUST answer ONLY using the provided CONTEXT documents.
Rules:
1. Never invent policy rules, numbers, cities, or procedures that are not in CONTEXT.
2. If CONTEXT is insufficient, set grounded=false and answer exactly:
   "The available knowledge base does not contain enough information to answer this question."
3. When grounded=true, write a clear answer in British English and cite source file names
   in parentheses, e.g. (taxi_vs_phv.md).
4. Do not answer operational/live fleet questions (counts, averages, revenue). If the
   question is operational, set grounded=false and say it requires operational database data.
5. Do not mention Qdrant, embeddings, or internal systems.

Respond with JSON only:
{
  "grounded": true/false,
  "answer": "...",
  "confidence": 0.0-1.0
}
"""


class RAGAgent:
    """Retrieve policy docs from Qdrant, then produce a grounded LLM answer."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.min_score = MIN_SCORE

    @staticmethod
    def is_operational_question(question: str) -> bool:
        q = (question or "").strip().lower()
        if not q:
            return False
        # Policy-looking questions about "available" in a definitional sense
        if any(h in q for h in ("policy", "mean", "interpret", "definition", "what does")):
            if re.search(r"\bhow many\b|\bcount\b|\brevenue\b", q):
                return True
            return False
        for pattern in _OPERATIONAL_PATTERNS:
            if re.search(pattern, q, flags=re.IGNORECASE):
                return True
        return False

    def retrieve(
        self, question: str, *, top_k: int = 5
    ) -> list[RetrievalHit]:
        """Delegate to the existing read-only Qdrant retrieval service."""
        result = retrieve_documents(query=question, top_k=top_k)
        return [
            hit
            for hit in result.results
            if hit.score >= self.min_score and (hit.text or "").strip()
        ]

    def _generate_answer(
        self, question: str, hits: list[RetrievalHit]
    ) -> dict[str, Any]:
        require_llm_settings(self.settings)
        context_blocks = []
        for i, hit in enumerate(hits, start=1):
            context_blocks.append(
                f"[{i}] source={hit.source} score={hit.score:.4f}\n{hit.text.strip()}"
            )
        context = "\n\n".join(context_blocks) if context_blocks else "(no documents)"
        payload = chat_json(
            system=ANSWER_SYSTEM,
            user=f"QUESTION:\n{question}\n\nCONTEXT:\n{context}",
            settings=self.settings,
            temperature=0.0,
        )
        return payload

    def run(self, question: str, *, top_k: int = 5) -> RagAgentResult:
        q = (question or "").strip()
        if not q:
            raise ValueError("question must be non-empty")

        if self.is_operational_question(q):
            return RagAgentResult(
                answer=OPERATIONAL_MSG,
                sources=[],
                confidence=0.0,
            )

        try:
            hits = self.retrieve(q, top_k=top_k)
        except (QdrantConfigError, QdrantUnavailableError):
            raise

        if not hits:
            return RagAgentResult(
                answer=INSUFFICIENT_MSG,
                sources=[],
                confidence=0.0,
            )

        payload = self._generate_answer(q, hits)
        grounded = bool(payload.get("grounded", False))
        answer = str(payload.get("answer") or "").strip()
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        sources = [RagSource(source=h.source, score=round(h.score, 4)) for h in hits]

        if not grounded or not answer:
            return RagAgentResult(
                answer=INSUFFICIENT_MSG,
                sources=sources,
                confidence=min(confidence, 0.2),
            )

        # Ensure at least one retrieved source name appears when grounded
        source_names = {h.source for h in hits}
        if not any(name in answer for name in source_names):
            cited = ", ".join(sorted(source_names))
            answer = f"{answer.rstrip()} (Sources: {cited})"

        return RagAgentResult(
            answer=answer,
            sources=sources,
            confidence=confidence,
        )
