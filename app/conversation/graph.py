"""LangGraph conversational workflow over domain services."""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.conversation.dispatch import dispatch
from app.conversation.journey_flow import handle_journey_turn, is_journey_planning
from app.conversation.locations import load_catalogue, resolve_location
from app.conversation.memory import get_state, new_conversation_id, save_state
from app.conversation.models import ConversationState, DomainResult
from app.conversation.nlu import understand
from app.conversation.response import (
    clarification_message,
    compose_answer,
    public_route_label,
)
from app.db.session import session_scope
from app.orchestration.models import ChatResponse

logger = logging.getLogger(__name__)


class ConvGraphState(TypedDict, total=False):
    conversation_id: str
    message: str
    state: dict[str, Any]
    clarification: str | None
    domain_result: dict[str, Any] | None
    answer: str
    route: str
    sources: list[dict[str, Any]]
    data: list[dict[str, Any]]
    metadata: dict[str, Any]


def _understand_node(state: ConvGraphState) -> dict[str, Any]:
    prior = ConversationState.model_validate(state.get("state") or {})
    merged = understand(state["message"], prior)
    return {"state": merged.model_dump(), "clarification": None}


async def _act_node(state: ConvGraphState) -> dict[str, Any]:
    """Resolve locations, clarify if needed, otherwise call domain services."""
    from app.conversation.models import Intent

    conv = ConversationState.model_validate(state.get("state") or {})

    async with session_scope() as session:
        catalogue = await load_catalogue(session)
        if conv.pickup and conv.pickup.raw:
            if not conv.pickup.zone_id or conv.pickup.confidence < 0.9:
                conv.pickup = resolve_location(conv.pickup.raw, catalogue)
        if conv.destination and conv.destination.raw:
            prefer_city = None
            raw_dest = (conv.destination.raw or "").lower()
            if "birmingham" in raw_dest or "brum" in raw_dest:
                prefer_city = "Birmingham"
            elif "manchester" in raw_dest:
                prefer_city = "Manchester"
            elif "london" in raw_dest:
                prefer_city = "London"
            elif "leeds" in raw_dest:
                prefer_city = "Leeds"
            conv.destination = resolve_location(
                conv.destination.raw, catalogue, prefer_city=prefer_city
            )

        non_journey = {
            Intent.POLICY,
            Intent.OPERATIONS_ANALYSIS,
            Intent.DEMAND_ANALYSIS,
            Intent.TRIP_LOOKUP,
            Intent.NEARBY_VEHICLES,
            Intent.VEHICLE_AVAILABILITY,
            Intent.FLEET_SEARCH,
        }
        use_journey = is_journey_planning(conv) or (
            conv.pickup is not None
            and conv.destination is not None
            and conv.intent not in non_journey
        )

        if use_journey:
            domain, conv, clarify, slot = await handle_journey_turn(session, conv)
            if clarify:
                conv.pending_clarification = slot
                return {
                    "clarification": clarify,
                    "state": conv.model_dump(),
                    "domain_result": None,
                    "sources": [],
                    "data": [],
                }
            if domain is not None:
                conv.last_domain = domain.domain
                return {
                    "clarification": None,
                    "domain_result": domain.model_dump(),
                    "state": conv.model_dump(),
                    "sources": domain.sources,
                    "data": domain.data,
                }
            # Locations not yet usable — fall through to generic clarify

        found = clarification_message(conv)
        if found:
            msg, slot = found
            conv.pending_clarification = slot
            return {
                "clarification": msg,
                "state": conv.model_dump(),
                "domain_result": None,
                "sources": [],
                "data": [],
            }

        conv.pending_clarification = None
        result = await dispatch(session, conv, state["message"])
        conv.last_domain = result.domain
        return {
            "clarification": None,
            "domain_result": result.model_dump(),
            "state": conv.model_dump(),
            "sources": result.sources,
            "data": result.data,
        }


def _respond_node(state: ConvGraphState) -> dict[str, Any]:
    conv = ConversationState.model_validate(state.get("state") or {})
    clarification = state.get("clarification")
    domain = None
    if state.get("domain_result"):
        domain = DomainResult.model_validate(state["domain_result"])
    answer = compose_answer(state=conv, domain=domain, clarification=clarification)
    route = public_route_label(conv.intent, domain.domain if domain else None)
    meta = {
        "intent": conv.intent.value,
        "clarification": bool(clarification),
        "estimate": bool(domain.estimate) if domain else False,
    }
    if domain and domain.meta:
        meta.update({k: v for k, v in domain.meta.items() if k != "sql"})
    return {
        "answer": answer,
        "route": route,
        "metadata": meta,
        "sources": state.get("sources") or (domain.sources if domain else []),
        "data": state.get("data") or (domain.data if domain else []),
    }


def _persist_node(state: ConvGraphState) -> dict[str, Any]:
    save_state(state["conversation_id"], ConversationState.model_validate(state.get("state") or {}))
    return {}


def build_conversation_graph():
    graph = StateGraph(ConvGraphState)
    graph.add_node("understand", _understand_node)
    graph.add_node("act", _act_node)
    graph.add_node("respond", _respond_node)
    graph.add_node("persist", _persist_node)

    graph.add_edge(START, "understand")
    graph.add_edge("understand", "act")
    graph.add_edge("act", "respond")
    graph.add_edge("respond", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


_GRAPH = None


def get_conversation_graph(*, force_reload: bool = False):
    global _GRAPH
    if force_reload or _GRAPH is None:
        _GRAPH = build_conversation_graph()
    return _GRAPH


async def run_chat(
    message: str,
    *,
    conversation_id: str | None = None,
) -> ChatResponse:
    """Conversational entrypoint used by POST /api/chat."""
    started = time.perf_counter()
    question = (message or "").strip()
    if not question:
        raise ValueError("message must be non-empty")

    cid = conversation_id or new_conversation_id()
    prior = get_state(cid)

    graph = get_conversation_graph()
    final: ConvGraphState = await graph.ainvoke(
        {
            "conversation_id": cid,
            "message": question,
            "state": prior.model_dump(),
        }
    )

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    metadata = dict(final.get("metadata") or {})
    metadata["execution_time_ms"] = elapsed_ms
    metadata["conversation_id"] = cid
    metadata["agents_selected"] = [metadata.get("intent", "assistant")]

    logger.info(
        "conv intent=%s route=%s ms=%s clarification=%s",
        metadata.get("intent"),
        final.get("route"),
        elapsed_ms,
        metadata.get("clarification"),
    )

    return ChatResponse(
        question=question,
        conversation_id=cid,
        route=str(final.get("route") or "assistant"),
        answer=str(final.get("answer") or ""),
        sources=list(final.get("sources") or []),
        data=list(final.get("data") or []),
        metadata=metadata,
    )


async def run_turn(
    message: str, *, conversation_id: str | None = None
):
    """Typed turn helper for tests."""
    from app.conversation.models import TurnResult

    resp = await run_chat(message, conversation_id=conversation_id)
    cid = resp.conversation_id or conversation_id or ""
    state = get_state(cid)
    return TurnResult(
        conversation_id=cid,
        answer=resp.answer,
        route=resp.route,
        intent=state.intent,
        state=state,
        sources=resp.sources,
        data=resp.data,
        metadata=resp.metadata,
    )
