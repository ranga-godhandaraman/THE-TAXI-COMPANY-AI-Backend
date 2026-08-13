"""Dispatch conversational intents to domain services (SQL/RAG/Analytics reused)."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analytics import AnalyticsAgent
from app.agents.rag import RAGAgent
from app.agents.sql import SQLAgent
from app.config import get_settings
from app.conversation.models import ConversationState, DomainResult, Intent
from app.services import booking as booking_service
from app.services import fleet_conversation

logger = logging.getLogger(__name__)


async def dispatch(
    session: AsyncSession,
    state: ConversationState,
    user_message: str,
) -> DomainResult:
    intent = state.intent

    try:
        if intent in {Intent.FARE_ESTIMATE, Intent.JOURNEY_ESTIMATE}:
            # Journey planning is handled in graph via journey_flow + pricing engine.
            # Fallback if somehow reached here:
            from app.conversation.journey_flow import handle_journey_turn

            domain, _, clarify, _ = await handle_journey_turn(session, state)
            if clarify:
                return DomainResult(domain="journey", summary=clarify)
            return domain or DomainResult(
                domain="journey",
                summary="I can estimate that journey once I have a few more details.",
            )

        if intent == Intent.BOOKING_REQUEST:
            from app.conversation.journey_flow import handle_journey_turn

            domain, _, clarify, _ = await handle_journey_turn(session, state)
            if clarify:
                return DomainResult(domain="journey", summary=clarify)
            if domain:
                return domain
            booking = await booking_service.handle_booking_request(state)
            return booking

        if intent in {
            Intent.VEHICLE_AVAILABILITY,
            Intent.NEARBY_VEHICLES,
            Intent.VEHICLE_SEARCH,
            Intent.ACCESSIBILITY_SEARCH,
            Intent.FLEET_SEARCH,
        }:
            return await fleet_conversation.fleet_availability(session, state)

        if intent == Intent.POLICY:
            return await _run_rag(user_message)

        if intent in {Intent.OPERATIONS_ANALYSIS, Intent.DEMAND_ANALYSIS}:
            return await _run_analytics(session, user_message, state)

        if intent == Intent.TRIP_LOOKUP:
            return await _run_sql(
                session,
                user_message
                if "trip" in user_message.lower()
                else f"Show recent trips related to: {user_message}",
            )

        if intent == Intent.GENERAL_TAXI_QUERY:
            if state.pickup and state.destination:
                from app.conversation.journey_flow import handle_journey_turn

                domain, _, clarify, _ = await handle_journey_turn(session, state)
                if clarify:
                    return DomainResult(domain="journey", summary=clarify)
                if domain:
                    return domain
            return await _run_rag(user_message)

        return DomainResult(
            domain="assistant",
            summary=(
                "I can help with fares, journey times, cars near an area, "
                "accessibility, bookings requests, and taxi/PHV policy."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("domain dispatch failed: %s", type(exc).__name__)
        return DomainResult(
            domain="assistant",
            summary=(
                "Sorry — something went wrong while looking that up. "
                "Please try again in a moment."
            ),
            error=True,
            meta={"error_class": type(exc).__name__},
        )


async def _run_rag(question: str) -> DomainResult:
    agent = RAGAgent(settings=get_settings())
    result = agent.run(question)
    # Soften operational rejection for conversational UX
    answer = result.answer
    if "SQL Agent" in answer or "RAG Agent" in answer:
        answer = (
            "For live vehicle counts and availability, ask me something like "
            "“any cars near Heathrow?” For policy, ask about taxi vs PHV rules."
        )
    return DomainResult(
        domain="policy",
        summary=answer,
        sources=[s.model_dump() for s in result.sources],
        meta={"confidence": result.confidence},
    )


async def _run_analytics(
    session: AsyncSession, question: str, state: ConversationState
) -> DomainResult:
    # Prefer a grounded question if we know the place
    q = question
    if state.pickup and state.pickup.resolved and "heathrow" in (state.pickup.resolved or "").lower():
        if "why" in question.lower() or "hardly" in question.lower():
            q = "Why is Heathrow availability lower than normal?"
    agent = AnalyticsAgent(settings=get_settings())
    result = await agent.run(q, session=session)
    parts = [result.summary]
    if result.observations:
        parts.append(" ".join(result.observations[:3]))
    return DomainResult(
        domain="operations",
        summary=" ".join(parts).strip(),
        data=list(result.data or []),
        meta={"analysis_type": result.analysis_type, "metrics": result.metrics},
    )


async def _run_sql(session: AsyncSession, question: str) -> DomainResult:
    agent = SQLAgent(settings=get_settings())
    result = await agent.run(question, session=session)
    data = []
    if result.columns and result.rows:
        data = [
            {str(result.columns[i]): row[i] for i in range(min(len(result.columns), len(row)))}
            for row in result.rows
        ]
    return DomainResult(
        domain="trips",
        summary=result.summary,
        data=data,
        meta={"sql": result.sql},
    )
