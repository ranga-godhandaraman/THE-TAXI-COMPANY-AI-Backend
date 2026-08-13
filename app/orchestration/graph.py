"""LangGraph orchestration over existing SQL / RAG / Analytics agents."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.analytics import AnalyticsAgent
from app.agents.rag import RAGAgent
from app.agents.sql import SQLAgent
from app.config import get_settings
from app.db.session import session_scope
from app.orchestration.models import ChatResponse
from app.orchestration.routing import RouteDecision, decide_route

logger = logging.getLogger(__name__)

# End-to-end budget for hybrid (SQL + analytics + RAG + LLM calls).
CHAT_TIMEOUT_SECONDS = 90.0
_USER_SAFE_FAIL = (
    "I couldn't complete that enquiry from the operations systems. "
    "Please retry or rephrase your question."
)


class OrchestratorState(TypedDict, total=False):
    question: str
    route: str
    agents: list[str]
    route_reason: str
    sql_result: dict[str, Any] | None
    rag_result: dict[str, Any] | None
    analytics_result: dict[str, Any] | None
    answer: str
    sources: list[dict[str, Any]]
    data: list[dict[str, Any]]
    metadata: dict[str, Any]
    error: str | None


_ZONE_NAMES = (
    "Heathrow",
    "Gatwick",
    "Westminster",
    "Camden",
    "Manchester Airport",
    "Birmingham Airport",
    "Leeds Bradford Airport",
)
_CITIES = ("London", "Manchester", "Birmingham", "Leeds", "Liverpool")


def _sql_question_for(state: OrchestratorState) -> str:
    """
    For hybrid routes, ask SQL a concrete snapshot question.

    Existing SQLAgent is unchanged; the orchestrator only rewrites the prompt.
    """
    question = state["question"]
    if state.get("route") != "hybrid":
        return question

    q = question.lower()
    zone = next((z for z in _ZONE_NAMES if z.lower() in q), None)
    city = next((c for c in _CITIES if c.lower() in q), None)
    if zone:
        return f"How many vehicles are available in the {zone} zone?"
    if city and "available" in q:
        return f"How many vehicles are available in {city}?"
    if "available" in q or "availability" in q:
        return "How many vehicles are currently available?"
    return question


def _router_node(state: OrchestratorState) -> dict[str, Any]:
    decision: RouteDecision = decide_route(state["question"])
    return {
        "route": decision.route,
        "agents": list(decision.agents),
        "route_reason": decision.reason,
        "sql_result": None,
        "rag_result": None,
        "analytics_result": None,
        "error": None,
    }


def _pick_next(agents: list[str], completed: set[str]) -> str:
    order = ("sql", "analytics", "rag")
    mapping = {
        "sql": "sql_agent",
        "analytics": "analytics_agent",
        "rag": "rag_agent",
    }
    for agent in order:
        if agent in agents and agent not in completed:
            return mapping[agent]
    return "formatter"


def _after_router(state: OrchestratorState) -> str:
    return _pick_next(state.get("agents", []), set())


def _after_sql(state: OrchestratorState) -> str:
    return _pick_next(state.get("agents", []), {"sql"})


def _after_analytics(state: OrchestratorState) -> str:
    return _pick_next(state.get("agents", []), {"sql", "analytics"})


def _after_rag(state: OrchestratorState) -> str:
    return _pick_next(state.get("agents", []), {"sql", "analytics", "rag"})


async def _sql_agent_node(state: OrchestratorState) -> dict[str, Any]:
    settings = get_settings()
    agent = SQLAgent(settings=settings)
    sql_question = _sql_question_for(state)
    try:
        async with session_scope(settings) as session:
            result = await agent.run(sql_question, session=session)
        return {
            "sql_result": {
                "intent": result.intent,
                "sql": result.sql,
                "summary": result.summary,
                "columns": result.columns,
                "rows": result.rows,
                "confidence": result.confidence,
                "asked_question": sql_question,
                "execution": result.execution.model_dump() if result.execution else None,
            }
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("sql agent failed: %s", type(exc).__name__)
        return {
            "sql_result": {
                "error": type(exc).__name__,
                "summary": _USER_SAFE_FAIL,
                "asked_question": sql_question,
            },
            "error": type(exc).__name__,
        }


async def _rag_agent_node(state: OrchestratorState) -> dict[str, Any]:
    settings = get_settings()
    agent = RAGAgent(settings=settings)
    try:
        # Sync encode + Qdrant must not block the event loop.
        result = await asyncio.to_thread(agent.run, state["question"])
        return {
            "rag_result": {
                "answer": result.answer,
                "sources": [s.model_dump() for s in result.sources],
                "confidence": result.confidence,
            }
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag agent failed: %s", type(exc).__name__)
        return {
            "rag_result": {"error": type(exc).__name__, "answer": _USER_SAFE_FAIL},
            "error": type(exc).__name__,
        }


async def _analytics_agent_node(state: OrchestratorState) -> dict[str, Any]:
    settings = get_settings()
    agent = AnalyticsAgent(settings=settings)
    try:
        async with session_scope(settings) as session:
            result = await agent.run(state["question"], session=session)
        return {
            "analytics_result": {
                "analysis_type": result.analysis_type,
                "summary": result.summary,
                "metrics": result.metrics,
                "observations": result.observations,
                "recommendations": result.recommendations,
                "data": result.data,
            }
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics agent failed: %s", type(exc).__name__)
        return {
            "analytics_result": {
                "error": type(exc).__name__,
                "summary": _USER_SAFE_FAIL,
                "data": [],
            },
            "error": type(exc).__name__,
        }


def _format_answer(state: OrchestratorState) -> dict[str, Any]:
    route = state.get("route") or "sql"
    sources: list[dict[str, Any]] = []
    data: list[dict[str, Any]] = []
    parts: list[str] = []
    meta_extra: dict[str, Any] = {}

    sql_result = state.get("sql_result")
    rag_result = state.get("rag_result")
    analytics_result = state.get("analytics_result")

    if route == "sql" and sql_result:
        if sql_result.get("error"):
            parts.append(_USER_SAFE_FAIL)
            meta_extra = {"error": True, "error_class": "sql"}
        else:
            parts.append(str(sql_result.get("summary") or "").strip())
            cols = sql_result.get("columns") or []
            rows = sql_result.get("rows") or []
            if cols and rows:
                data = [
                    {str(cols[i]): row[i] for i in range(min(len(cols), len(row)))}
                    for row in rows
                ]
            meta_extra = {
                "sql": sql_result.get("sql"),
                "intent": sql_result.get("intent"),
                "confidence": sql_result.get("confidence"),
            }
    elif route == "rag" and rag_result:
        if rag_result.get("error"):
            parts.append(_USER_SAFE_FAIL)
            meta_extra = {"error": True, "error_class": "rag"}
        else:
            parts.append(str(rag_result.get("answer") or "").strip())
            sources = list(rag_result.get("sources") or [])
            meta_extra = {"confidence": rag_result.get("confidence")}
    elif route == "analytics" and analytics_result:
        if analytics_result.get("error"):
            parts.append(_USER_SAFE_FAIL)
            meta_extra = {"error": True, "error_class": "analytics"}
        else:
            parts.append(str(analytics_result.get("summary") or "").strip())
            observations = analytics_result.get("observations") or []
            recommendations = analytics_result.get("recommendations") or []
            if observations:
                parts.append("Observations: " + "; ".join(observations[:5]))
            if recommendations:
                parts.append("Recommendations: " + "; ".join(recommendations[:3]))
            data = list(analytics_result.get("data") or [])
            meta_extra = {
                "analysis_type": analytics_result.get("analysis_type"),
                "metrics": analytics_result.get("metrics") or {},
            }
    else:
        meta_extra = {"agents_run": state.get("agents", [])}
        if sql_result and not sql_result.get("error"):
            parts.append(
                "Current operational snapshot (SQL): "
                + str(sql_result.get("summary") or "").strip()
            )
            cols = sql_result.get("columns") or []
            rows = sql_result.get("rows") or []
            if cols and rows:
                data.extend(
                    [
                        {
                            **{
                                str(cols[i]): row[i]
                                for i in range(min(len(cols), len(row)))
                            },
                            "_source": "sql",
                        }
                        for row in rows
                    ]
                )
            meta_extra["sql"] = {
                "sql": sql_result.get("sql"),
                "intent": sql_result.get("intent"),
                "asked_question": sql_result.get("asked_question"),
            }
        elif sql_result and sql_result.get("error"):
            meta_extra["sql_error"] = sql_result.get("error")

        if analytics_result and not analytics_result.get("error"):
            parts.append(
                "Historical / statistical analysis: "
                + str(analytics_result.get("summary") or "").strip()
            )
            obs = analytics_result.get("observations") or []
            if obs:
                parts.append("Key observations: " + "; ".join(obs[:5]))
            rec = analytics_result.get("recommendations") or []
            if rec:
                parts.append("Recommendations: " + "; ".join(rec[:3]))
            for row in analytics_result.get("data") or []:
                item = dict(row)
                item["_source"] = "analytics"
                data.append(item)
            meta_extra["analysis_type"] = analytics_result.get("analysis_type")
            meta_extra["metrics"] = analytics_result.get("metrics") or {}
        elif analytics_result and analytics_result.get("error"):
            meta_extra["analytics_error"] = analytics_result.get("error")

        if rag_result and not rag_result.get("error"):
            parts.append(
                "Policy / reference context: "
                + str(rag_result.get("answer") or "").strip()
            )
            sources = list(rag_result.get("sources") or [])
            meta_extra["rag_confidence"] = rag_result.get("confidence")
        elif rag_result and rag_result.get("error"):
            meta_extra["rag_error"] = rag_result.get("error")

        if not parts:
            parts.append(_USER_SAFE_FAIL)
            meta_extra["error"] = True

    answer = "\n\n".join(p for p in parts if p).strip()
    metadata = {
        "route": route,
        "route_reason": state.get("route_reason"),
        "agents": state.get("agents", []),
        **meta_extra,
    }
    return {
        "answer": answer,
        "sources": sources,
        "data": data,
        "metadata": metadata,
    }


def build_graph():
    """
    Graph:

        START → router → sql_agent? → analytics_agent? → rag_agent? → formatter → END
    """
    graph = StateGraph(OrchestratorState)
    graph.add_node("router", _router_node)
    graph.add_node("sql_agent", _sql_agent_node)
    graph.add_node("rag_agent", _rag_agent_node)
    graph.add_node("analytics_agent", _analytics_agent_node)
    graph.add_node("formatter", _format_answer)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        _after_router,
        ["sql_agent", "analytics_agent", "rag_agent", "formatter"],
    )
    graph.add_conditional_edges(
        "sql_agent",
        _after_sql,
        ["analytics_agent", "rag_agent", "formatter"],
    )
    graph.add_conditional_edges(
        "analytics_agent",
        _after_analytics,
        ["rag_agent", "formatter"],
    )
    graph.add_conditional_edges(
        "rag_agent",
        _after_rag,
        ["formatter"],
    )
    graph.add_edge("formatter", END)
    return graph.compile()


_GRAPH = None


def get_graph(*, force_reload: bool = False):
    global _GRAPH
    if force_reload or _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_chat(message: str) -> ChatResponse:
    """Execute the LangGraph orchestrator for a user message."""
    started = time.perf_counter()
    question = (message or "").strip()
    if not question:
        raise ValueError("message must be non-empty")

    graph = get_graph()
    try:
        final: OrchestratorState = await asyncio.wait_for(
            graph.ainvoke({"question": question}),
            timeout=CHAT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.warning("chat timed out after %sms", elapsed_ms)
        return ChatResponse(
            question=question,
            route="sql",
            answer=(
                "The request timed out while querying operations systems. "
                "Please try a simpler question or retry."
            ),
            sources=[],
            data=[],
            metadata={
                "error": True,
                "timeout": True,
                "execution_time_ms": elapsed_ms,
            },
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    metadata = dict(final.get("metadata") or {})
    metadata["execution_time_ms"] = elapsed_ms
    metadata["route_selected"] = final.get("route")
    metadata["route_reason"] = final.get("route_reason")
    metadata["agents_selected"] = final.get("agents", [])

    logger.info(
        "chat route=%s agents=%s ms=%s error=%s",
        final.get("route"),
        final.get("agents"),
        elapsed_ms,
        bool(metadata.get("error")),
    )

    return ChatResponse(
        question=question,
        route=str(final.get("route") or "sql"),
        answer=str(final.get("answer") or ""),
        sources=list(final.get("sources") or []),
        data=list(final.get("data") or []),
        metadata=metadata,
    )
