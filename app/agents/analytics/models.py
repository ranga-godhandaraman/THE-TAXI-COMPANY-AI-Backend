"""Analytics agent result models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AnalysisType = Literal[
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
    "unsupported",
]


class AnalyticsAgentRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)


class AnalyticsAgentResult(BaseModel):
    analysis_type: str
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)


class AnalyticsAgentResponse(BaseModel):
    question: str
    analysis_type: str
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
