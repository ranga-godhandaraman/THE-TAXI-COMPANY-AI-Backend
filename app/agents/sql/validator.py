"""Strict read-only SQL validation for the SQL agent."""

from __future__ import annotations

import re

import sqlparse
from sqlparse.tokens import DML, Keyword

from app.agents.sql.schema_context import ALLOWED_TABLES

FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "MERGE",
        "REPLACE",
        "COPY",
        "CALL",
        "EXECUTE",
        "EXEC",
        "INTO",  # blocks SELECT INTO / INSERT INTO
        "SET",  # blocks SET role / config mid-script
        "VACUUM",
        "ANALYZE",
        "REINDEX",
        "CLUSTER",
        "COMMENT",
        "SECURITY",
        "OWNER",
        "ATTACH",
        "DETACH",
        "LOAD",
        "INSTALL",
        "DO",
        "LISTEN",
        "NOTIFY",
        "PREPARE",
        "DEALLOCATE",
        "DISCARD",
        "LOCK",
        "UNLOCK",
        "REASSIGN",
        "REFRESH",
        "SHOW",
        "EXPLAIN",  # optional; block to keep surface minimal
    }
)

_MULTI_STMT = re.compile(r";\s*\S", re.DOTALL)
_COMMENT_LINE = re.compile(r"--.*?$", re.MULTILINE)
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


class SQLValidationError(ValueError):
    """Raised when SQL fails read-only / schema safety checks."""


def _strip_comments(sql: str) -> str:
    sql = _COMMENT_BLOCK.sub(" ", sql)
    sql = _COMMENT_LINE.sub(" ", sql)
    return sql.strip()


def _iter_keyword_tokens(statement) -> list[str]:
    keywords: list[str] = []
    for token in statement.flatten():
        if token.ttype in (Keyword, DML) or (
            token.ttype is not None and str(token.ttype).startswith("Token.Keyword")
        ):
            keywords.append(token.value.upper())
        elif token.is_keyword:
            keywords.append(token.value.upper())
    return keywords


def validate_sql(sql: str, *, max_limit: int = 50) -> str:
    """
    Validate that `sql` is a single read-only SELECT against allowed tables.

    Returns a cleaned SQL string (trailing semicolon removed).
    Raises SQLValidationError on any violation.
    """
    if not sql or not str(sql).strip():
        raise SQLValidationError("SQL is empty")

    cleaned = _strip_comments(str(sql)).strip()
    if not cleaned:
        raise SQLValidationError("SQL is empty after removing comments")

    # Disallow multiple statements
    if _MULTI_STMT.search(cleaned.rstrip(";")):
        raise SQLValidationError("Multiple SQL statements are not allowed")

    cleaned = cleaned.rstrip(";").strip()

    parsed = sqlparse.parse(cleaned)
    if not parsed:
        raise SQLValidationError("Unable to parse SQL")
    if len(parsed) != 1:
        raise SQLValidationError("Multiple SQL statements are not allowed")

    statement = parsed[0]
    leading = cleaned.lstrip().upper()
    if not (leading.startswith("SELECT") or leading.startswith("WITH")):
        raise SQLValidationError("Only SELECT / WITH…SELECT queries are allowed")

    keywords = _iter_keyword_tokens(statement)
    if "SELECT" not in keywords and not leading.startswith("SELECT"):
        if not leading.startswith("WITH"):
            raise SQLValidationError("Query must be a SELECT")

    for kw in keywords:
        if kw in FORBIDDEN_KEYWORDS:
            raise SQLValidationError(f"Forbidden keyword: {kw}")

    # Extra hard scan on raw text for forbidden verbs (word boundaries)
    upper = f" {cleaned.upper()} "
    for kw in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
    ):
        if re.search(rf"\b{kw}\b", upper):
            raise SQLValidationError(f"Forbidden keyword: {kw}")

    # Extract relation names only from FROM / JOIN clauses
    tables = {
        m.group(1).lower()
        for m in re.finditer(
            r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w]*)", cleaned, flags=re.IGNORECASE
        )
    }
    if not tables:
        raise SQLValidationError(
            "Query must reference at least one allowlisted table via FROM/JOIN"
        )
    disallowed = sorted(t for t in tables if t not in ALLOWED_TABLES)
    if disallowed:
        raise SQLValidationError(
            f"Query references disallowed table(s): {', '.join(disallowed)}"
        )

    # Ensure LIMIT on non-aggregate wide selects — soft enforce by appending if missing
    # when SELECT * or many columns without aggregation. Keep simple: if no LIMIT and
    # no aggregate keywords, append LIMIT.
    has_limit = bool(re.search(r"\bLIMIT\b", cleaned, flags=re.IGNORECASE))
    has_agg = bool(
        re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", cleaned, flags=re.IGNORECASE)
    )
    has_group = bool(re.search(r"\bGROUP\s+BY\b", cleaned, flags=re.IGNORECASE))
    if not has_limit and not (has_agg or has_group):
        cleaned = f"{cleaned}\nLIMIT {max_limit}"

    return cleaned
