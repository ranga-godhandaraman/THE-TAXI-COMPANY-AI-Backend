"""Analytics Agent — deterministic SQL/Python analysis with optional LLM routing."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analytics.models import AnalyticsAgentResult
from app.agents.llm import LLMConfigError, chat_json, require_llm_settings
from app.analytics import computations as calc
from app.config import Settings, get_settings

ANALYSIS_TYPES = (
    "demand_trend",
    "availability_trend",
    "demand_vs_availability",
    "unserved_demand",
    "revenue_trend",
    "average_trip_distance",
    "average_trip_duration",
    "cancellation_rate",
    "peak_hours",
    "zone_performance",
    "city_comparison",
    "vehicle_utilization",
    "availability_anomaly",
    "demand_anomaly",
    "demand_supply_gap",
    "normal_snapshot",
)

CLASSIFY_SYSTEM = f"""You classify UK taxi operational analytics questions.

Return JSON only:
{{
  "analysis_type": one of {list(ANALYSIS_TYPES)},
  "city": string or null,
  "cities": [string] or null,
  "zone_name": string or null,
  "hour": 0-23 or null,
  "is_anomaly": true/false,
  "confidence": 0.0-1.0
}}

Guidance:
- "unusually high/low", "abnormal", "why is availability low", "anomaly" → availability_anomaly or demand_anomaly
- "largest demand/supply gap" → demand_supply_gap
- "busiest hours" / peak → peak_hours
- "compare ... London and Manchester" → city_comparison with cities list
- "has demand increased" / trend → demand_trend
- "what is London demand at 8 AM" (factual, not unusual) → normal_snapshot with hour=8
- revenue over time → revenue_trend
- cancellation rate → cancellation_rate
- utilization / busy vs available fleet → vehicle_utilization
- average distance/duration → average_trip_distance / average_trip_duration
Known cities: London, Manchester, Birmingham, Leeds, Liverpool.
Zone examples: Heathrow, Westminster, Gatwick.
Never invent metrics. Classification only.
"""


class AnalyticsAgent:
    """Route analytical questions to reproducible SQL/pandas computations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def classify(self, question: str) -> dict[str, Any]:
        """LLM classification with heuristic fallback (no metrics invented)."""
        q = (question or "").strip()
        if not q:
            raise ValueError("question must be non-empty")

        try:
            require_llm_settings(self.settings)
            payload = chat_json(
                system=CLASSIFY_SYSTEM,
                user=f"Question: {q}",
                settings=self.settings,
                temperature=0.0,
            )
            analysis_type = str(payload.get("analysis_type") or "unsupported")
            if analysis_type not in ANALYSIS_TYPES:
                analysis_type = self._heuristic_type(q)
            city = payload.get("city")
            cities = payload.get("cities")
            zone_name = payload.get("zone_name")
            hour = payload.get("hour")
            return {
                "analysis_type": analysis_type,
                "city": str(city).strip() if city else None,
                "cities": [str(c) for c in cities] if isinstance(cities, list) else None,
                "zone_name": str(zone_name).strip() if zone_name else None,
                "hour": int(hour) if hour is not None and str(hour).isdigit() else (
                    int(hour) if isinstance(hour, int) else None
                ),
                "is_anomaly": bool(payload.get("is_anomaly", False)),
            }
        except (LLMConfigError, Exception):
            return self._heuristic_plan(q)

    def _heuristic_type(self, q: str) -> str:
        ql = q.lower()
        if any(w in ql for w in ("unusual", "abnormal", "anomal", "why is availability")):
            if "demand" in ql:
                return "demand_anomaly"
            return "availability_anomaly"
        if "gap" in ql or "demand/supply" in ql or "demand vs supply" in ql:
            return "demand_supply_gap"
        if "busiest" in ql or "peak hour" in ql:
            return "peak_hours"
        if "compare" in ql and any(c.lower() in ql for c in calc.CITIES):
            return "city_comparison"
        if "cancellation" in ql:
            return "cancellation_rate"
        if "revenue" in ql:
            return "revenue_trend"
        if "utilization" in ql or "utilised" in ql or "utilized" in ql:
            return "vehicle_utilization"
        if "unserved" in ql:
            return "unserved_demand"
        if "average" in ql and "distance" in ql:
            return "average_trip_distance"
        if "average" in ql and "duration" in ql:
            return "average_trip_duration"
        if "zone performance" in ql or "top zone" in ql:
            return "zone_performance"
        if "availability" in ql and "trend" in ql:
            return "availability_trend"
        if ("demand" in ql and "trend" in ql) or "increased" in ql:
            return "demand_trend"
        if "demand vs" in ql or "versus availability" in ql:
            return "demand_vs_availability"
        if re.search(r"\bat\s+\d{1,2}\s*(am|pm|:00)?\b", ql) and "unusual" not in ql:
            return "normal_snapshot"
        if "demand" in ql:
            return "demand_trend"
        return "zone_performance"

    def _heuristic_plan(self, q: str) -> dict[str, Any]:
        ql = q.lower()
        city = next((c for c in calc.CITIES if c.lower() in ql), None)
        cities = [c for c in calc.CITIES if c.lower() in ql]
        zone_name = None
        for candidate in (
            "Heathrow",
            "Gatwick",
            "Westminster",
            "Camden",
            "Manchester Airport",
        ):
            if candidate.lower() in ql:
                zone_name = candidate
                break
        hour = None
        m = re.search(r"\b(\d{1,2})\s*(am|pm)?\b", ql)
        if m and "at" in ql:
            hour = int(m.group(1))
            if m.group(2) == "pm" and hour < 12:
                hour += 12
            if m.group(2) == "am" and hour == 12:
                hour = 0
        return {
            "analysis_type": self._heuristic_type(q),
            "city": city,
            "cities": cities[:2] if len(cities) >= 2 else None,
            "zone_name": zone_name,
            "hour": hour,
            "is_anomaly": any(
                w in ql for w in ("unusual", "abnormal", "anomal", "why is")
            ),
        }

    async def execute(
        self, plan: dict[str, Any], session: AsyncSession
    ) -> dict[str, Any]:
        """Run the deterministic computation for a classified plan."""
        t = plan["analysis_type"]
        city = plan.get("city")
        zone = plan.get("zone_name")
        cities = plan.get("cities")
        hour = plan.get("hour")

        if t == "demand_trend":
            return await calc.demand_trend(session, city=city, zone_name=zone)
        if t == "availability_trend":
            return await calc.availability_trend(session, city=city, zone_name=zone)
        if t == "demand_vs_availability":
            return await calc.demand_vs_availability(session, city=city, zone_name=zone)
        if t == "unserved_demand":
            return await calc.unserved_demand(session, city=city)
        if t == "revenue_trend":
            return await calc.revenue_trend(session, city=city)
        if t == "average_trip_distance":
            return await calc.average_trip_metric(
                session, metric="distance", city=city, pickup_zone=zone
            )
        if t == "average_trip_duration":
            return await calc.average_trip_metric(
                session, metric="duration", city=city, pickup_zone=zone
            )
        if t == "cancellation_rate":
            return await calc.cancellation_rate(session, city=city)
        if t == "peak_hours":
            return await calc.peak_hours(session, city=city)
        if t == "zone_performance":
            return await calc.zone_performance(session, city=city)
        if t == "city_comparison":
            return await calc.city_comparison(session, cities=cities)
        if t == "vehicle_utilization":
            return await calc.vehicle_utilization(session, city=city)
        if t == "availability_anomaly":
            return await calc.availability_anomaly(
                session, zone_name=zone, city=city
            )
        if t == "demand_anomaly":
            return await calc.demand_anomaly(session, zone_name=zone, city=city)
        if t == "demand_supply_gap":
            return await calc.demand_supply_gap(session, city=city)
        if t == "normal_snapshot":
            return await calc.normal_snapshot(
                session, city=city, zone_name=zone, hour=hour
            )
        return {
            "analysis_type": "unsupported",
            "summary": "This question is not supported by the Analytics Agent.",
            "metrics": {},
            "observations": [],
            "recommendations": [
                "Try rephrasing as a trend, comparison, peak-hour, or anomaly question."
            ],
            "data": [],
        }

    async def run(
        self, question: str, session: AsyncSession
    ) -> AnalyticsAgentResult:
        plan = self.classify(question)
        result = await self.execute(plan, session)
        return AnalyticsAgentResult(
            analysis_type=str(result.get("analysis_type") or "unsupported"),
            summary=str(result.get("summary") or ""),
            metrics=dict(result.get("metrics") or {}),
            observations=list(result.get("observations") or []),
            recommendations=list(result.get("recommendations") or []),
            data=list(result.get("data") or []),
        )
