"""LLM-powered read-only SQL agent for structured taxi data."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import LLMConfigError, LLMError, chat_json, require_llm_settings
from app.agents.sql.models import SQLAgentResult, SQLExecutionMeta
from app.agents.sql.schema_context import INTENT_HINTS, SCHEMA_DESCRIPTION
from app.agents.sql.validator import SQLValidationError, validate_sql
from app.config import Settings, get_settings
from app.db.session import redact_db_error, session_scope

DEFAULT_ROW_CAP = 50
STATEMENT_TIMEOUT_MS = 15_000

GENERATE_SYSTEM = f"""You convert natural-language UK taxi operations questions into safe PostgreSQL.

{SCHEMA_DESCRIPTION}

Respond with a JSON object only:
{{
  "intent": one of {list(INTENT_HINTS)},
  "sql": "SELECT ...",
  "confidence": 0.0-1.0
}}

Do not include explanations, markdown, or credentials.
Prefer aggregates. If returning detail rows, include LIMIT {DEFAULT_ROW_CAP}.
"""

SUMMARIZE_SYSTEM = """You summarize SQL query results for a UK taxi operations assistant.
Be concise (1-3 sentences). Use British English. Do not invent numbers not present in the data.
Do not mention SQL, databases, or internal tooling unless asked.
Respond with JSON: {"summary": "..."}
"""


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001
            return str(value)
    return value


class SQLAgent:
    """Natural language → validated SELECT → execute → summarize."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.row_cap = DEFAULT_ROW_CAP
        self.statement_timeout_ms = STATEMENT_TIMEOUT_MS

    def generate_sql(self, question: str) -> dict[str, Any]:
        """Use the LLM to produce intent + SQL (no DB access, no credentials)."""
        require_llm_settings(self.settings)
        q = (question or "").strip()
        if not q:
            raise ValueError("question must be non-empty")

        payload = chat_json(
            system=GENERATE_SYSTEM,
            user=f"Question: {q}",
            settings=self.settings,
            temperature=0.0,
        )
        sql = str(payload.get("sql") or "").strip()
        intent = str(payload.get("intent") or "general_query").strip()
        if intent not in INTENT_HINTS:
            intent = "general_query"
        try:
            confidence = float(payload.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        if not sql:
            raise LLMError("LLM returned empty SQL")
        return {"intent": intent, "sql": sql, "confidence": confidence}

    def validate_sql(self, sql: str) -> str:
        """Validate and normalize SQL; raises SQLValidationError if unsafe."""
        return validate_sql(sql, max_limit=self.row_cap)

    async def execute_sql(
        self, sql: str, session: AsyncSession | None = None
    ) -> dict[str, Any]:
        """
        Execute a previously validated SELECT via the app DB pool.

        Never accepts a raw connection string from the caller.
        """
        safe_sql = self.validate_sql(sql)
        started = time.perf_counter()

        async def _run(sess: AsyncSession) -> dict[str, Any]:
            await sess.execute(
                text(f"SET LOCAL statement_timeout = '{self.statement_timeout_ms}'")
            )
            result = await sess.execute(text(safe_sql))
            columns = list(result.keys())
            raw_rows = result.fetchmany(self.row_cap + 1)
            truncated = len(raw_rows) > self.row_cap
            rows = [
                [_json_safe(v) for v in row]
                for row in raw_rows[: self.row_cap]
            ]
            elapsed = (time.perf_counter() - started) * 1000
            return {
                "columns": columns,
                "rows": rows,
                "execution": SQLExecutionMeta(
                    row_count=len(rows),
                    truncated=truncated,
                    elapsed_ms=round(elapsed, 2),
                    statement_timeout_ms=self.statement_timeout_ms,
                ),
            }

        try:
            if session is not None:
                return await _run(session)
            async with session_scope(self.settings) as sess:
                return await _run(sess)
        except SQLValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"SQL execution failed: {redact_db_error(exc)}"
            ) from None

    def summarize_result(
        self,
        question: str,
        sql: str,
        columns: list[str],
        rows: list[list[Any]],
        *,
        intent: str | None = None,
    ) -> str:
        """Produce a short natural-language summary (LLM preferred, template fallback)."""
        preview_rows = rows[:10]
        if not self.settings.llm_configured:
            return self._template_summary(question, columns, preview_rows)

        try:
            payload = chat_json(
                system=SUMMARIZE_SYSTEM,
                user=(
                    f"Question: {question}\n"
                    f"Intent: {intent or 'general_query'}\n"
                    f"Columns: {columns}\n"
                    f"Rows (capped): {preview_rows}\n"
                    f"SQL (for context only): {sql}"
                ),
                settings=self.settings,
                temperature=0.2,
            )
            summary = str(payload.get("summary") or "").strip()
            if summary:
                return summary
        except (LLMConfigError, LLMError):
            pass
        return self._template_summary(question, columns, preview_rows)

    @staticmethod
    def _template_summary(
        question: str, columns: list[str], rows: list[list[Any]]
    ) -> str:
        if not rows:
            return "No matching records were found for that question."
        if len(columns) == 1 and len(rows) == 1:
            return f"Result: {rows[0][0]}."
        if len(rows) == 1:
            pairs = ", ".join(f"{c}={v}" for c, v in zip(columns, rows[0]))
            return f"Result: {pairs}."
        return f"Returned {len(rows)} row(s) with columns {', '.join(columns)}."

    async def run(
        self, question: str, session: AsyncSession | None = None
    ) -> SQLAgentResult:
        """Full pipeline: generate → validate → execute → summarize."""
        generated = self.generate_sql(question)
        safe_sql = self.validate_sql(generated["sql"])
        executed = await self.execute_sql(safe_sql, session=session)
        summary = self.summarize_result(
            question,
            safe_sql,
            executed["columns"],
            executed["rows"],
            intent=generated["intent"],
        )
        return SQLAgentResult(
            intent=generated["intent"],
            sql=safe_sql,
            columns=executed["columns"],
            rows=executed["rows"],
            summary=summary,
            confidence=generated["confidence"],
            execution=executed["execution"],
        )
