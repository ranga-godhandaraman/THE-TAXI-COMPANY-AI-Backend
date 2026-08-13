"""Pydantic schemas for synthetic vehicle / pricing data."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VehicleClassOut(ORMModel):
    vehicle_class_id: str
    display_name: str
    min_passengers: int
    max_passengers: int
    luggage_capacity: int
    pricing_tier: str
    wheelchair_accessible: bool


class VehicleCatalogOut(ORMModel):
    vehicle_id: str
    vehicle_class_id: str
    display_name: str
    make: str
    model: str
    city: str
    passenger_capacity: int
    luggage_capacity: int
    wheelchair_accessible: bool
    pricing_tier: str


class FareRuleOut(ORMModel):
    pricing_tier: str
    base_fare_gbp: Decimal
    included_distance_miles: Decimal
    per_mile_gbp: Decimal
    per_minute_gbp: Decimal


class CityModifierOut(ORMModel):
    city: str
    city_multiplier: Decimal


class PeakRuleOut(ORMModel):
    rule_id: str
    start_hour: int
    end_hour: int
    multiplier: Decimal


class SurgeRuleOut(ORMModel):
    state: str
    multiplier: Decimal
    supply_ratio_threshold: Decimal
    min_surge: Decimal
    max_surge: Decimal


class VehicleSelectionRuleOut(ORMModel):
    min_passengers: int
    max_passengers: int
    vehicle_class_id: str
    display_name: str
    priority: int


class PricingConfigOut(ORMModel):
    key: str
    value: str
    description: str


class PricingTestCaseOut(ORMModel):
    test_id: str
    pickup: str
    destination: str
    passengers: int
    expected_vehicle_class: str
