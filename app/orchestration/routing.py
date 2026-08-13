"""Deterministic routing rules for the LangGraph orchestrator."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    route: str  # sql | rag | analytics | hybrid
    agents: tuple[str, ...]
    reason: str


_HYBRID_PATTERNS = (
    r"\bwhy\b.+\b(fewer|lower|higher|less|more)\b.+\b(usual|normal|typical|baseline|average)\b",
    r"\bwhy\b.+\b(availability|demand)\b.+\b(usual|normal|typical|lower than|higher than)\b",
    r"\bwhy\b.+\b(lower|higher)\b.+\b(than\s+)?(usual|normal|typical)\b",
    r"\bwhy\b.+\bheathrow\b.+\bavailability\b",
    r"\bwhy\b.+\bavailability\b.+\bheathrow\b",
)

_ANALYTICS_PATTERNS = (
    r"\bunusual(?:ly)?\b",
    r"\babnormal\b",
    r"\banomal",
    r"\bcompare\b",
    r"\bcomparison\b",
    r"\btrend\b",
    r"\bbusiest\b",
    r"\bpeak hours?\b",
    r"\bdemand/?supply\b",
    r"\bsupply/?demand\b",
    r"\bdemand.?supply.?gap\b",
    r"\bincreased recently\b",
    r"\bdecreased recently\b",
    r"\bhistorical\b",
    r"\bover time\b",
    r"\bvs\.?\b.+\b(demand|availability|city|manchester|london)\b",
    r"\bbetween\b.+\b(london|manchester|birmingham|leeds|liverpool)\b.+\b(london|manchester|birmingham|leeds|liverpool)\b",
    r"\bwhy\b.+\b(availability|demand)\b",
    r"\bzones?\b.+\b(low|high)\b.+\b(availability|demand)\b",
    r"\b(low|high)\b.+\b(availability|demand)\b.+\bzones?\b",
)

_RAG_PATTERNS = (
    r"\bpolicy\b",
    r"\bpolicies\b",
    r"\bdifference between\b",
    r"\btaxi\b.+\bphv\b",
    r"\bphv\b.+\btaxi\b",
    r"\bprivate hire\b",
    r"\baccessibility requirements?\b",
    r"\baccessible(?:ility)? rules?\b",
    r"\bcancellation policy\b",
    r"\bbooking policy\b",
    r"\bvehicle policy\b",
    r"\bdriver policy\b",
    r"\bservice areas?\b",
    r"\bdefinitions?\b",
    r"\brules\b",
    r"\bwhat are the accessibility\b",
    r"\bwhat is the difference\b",
)

_SQL_PATTERNS = (
    r"\bhow many\b",
    r"\bcount\b",
    r"\baverage trip\b",
    r"\bavg trip\b",
    r"\baverage (distance|duration|fare)\b",
    r"\bavailable (vehicles?|cars?|taxis?)\b",
    r"\b(vehicles?|cars?|taxis?)\b.+\bavailable\b",
    r"\bdriver information\b",
    r"\bvehicle information\b",
    r"\bbookings?\b",
    r"\bfares?\b",
    r"\brevenue\b",
    r"\blookup\b",
)


def _matches_any(question: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, question, flags=re.IGNORECASE) for p in patterns)


def decide_route(question: str) -> RouteDecision:
    """
    Deterministic multi-label route selection.

    Priority: hybrid → analytics → rag → sql (default operational).
    """
    q = (question or "").strip()
    if not q:
        return RouteDecision("sql", ("sql",), "empty_question_default_sql")

    # 1) Hybrid: why + baseline / fewer than usual style operational questions
    if _matches_any(q, _HYBRID_PATTERNS):
        agents: tuple[str, ...] = ("sql", "analytics")
        # Include RAG only when explicit policy/context is requested
        if _matches_any(q, _RAG_PATTERNS):
            agents = ("sql", "analytics", "rag")
        return RouteDecision(
            "hybrid",
            agents,
            "hybrid_why_vs_baseline_requires_sql_snapshot_and_analytics",
        )

    # 2) Analytics: trends, comparisons, anomalies
    if _matches_any(q, _ANALYTICS_PATTERNS):
        return RouteDecision(
            "analytics",
            ("analytics",),
            "analytics_keywords_trend_compare_or_anomaly",
        )

    # 3) RAG: policies / definitions / rules
    # Guard: factual averages/counts stay on SQL even if wording overlaps
    if _matches_any(q, _RAG_PATTERNS) and not _matches_any(
        q, (r"\bhow many\b", r"\baverage trip\b", r"\bavg trip\b")
    ):
        return RouteDecision(
            "rag",
            ("rag",),
            "rag_policy_definition_or_rules",
        )

    # 4) SQL: counts, lookups, direct aggregations
    if _matches_any(q, _SQL_PATTERNS):
        return RouteDecision(
            "sql",
            ("sql",),
            "sql_count_lookup_or_direct_aggregation",
        )

    # Default: operational structured query
    return RouteDecision(
        "sql",
        ("sql",),
        "default_operational_sql",
    )
