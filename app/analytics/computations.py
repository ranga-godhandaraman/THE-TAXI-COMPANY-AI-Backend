"""Deterministic analytics computations over Neon PostgreSQL (+ pandas).

All numeric outputs originate from SQL and/or transparent Python statistics.
No ML models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CITIES = ("London", "Manchester", "Birmingham", "Leeds", "Liverpool")


def _json_num(value: Any) -> float | int | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return round(float(value), 4)
    if isinstance(value, (int,)):
        return int(value)
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


async def _read_sql(session: AsyncSession, sql: str, params: dict | None = None) -> pd.DataFrame:
    # Align with SQL agent: bound heavy analytics aggregates on Neon.
    await session.execute(text("SET LOCAL statement_timeout = '15000'"))
    result = await session.execute(text(sql), params or {})
    rows = result.fetchall()
    columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


async def resolve_zone(
    session: AsyncSession, zone_name: str, city: str | None = None
) -> dict[str, str] | None:
    sql = """
        SELECT zone_id, zone_name, city
        FROM zones
        WHERE LOWER(zone_name) = LOWER(:zone_name)
    """
    params: dict[str, Any] = {"zone_name": zone_name}
    if city:
        sql += " AND LOWER(city) = LOWER(:city)"
        params["city"] = city
    sql += " LIMIT 1"
    df = await _read_sql(session, sql, params)
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "zone_id": str(row["zone_id"]),
        "zone_name": str(row["zone_name"]),
        "city": str(row["city"]),
    }


def _zscore_series(values: pd.Series) -> pd.Series:
    mean = values.mean()
    std = values.std(ddof=0)
    if std is None or std == 0 or np.isnan(std):
        return pd.Series([0.0] * len(values), index=values.index)
    return (values - mean) / std


def _rolling_baseline(values: pd.Series, window: int = 24) -> tuple[pd.Series, pd.Series]:
    """Rolling mean / std with min_periods to keep early points."""
    w = max(3, min(window, max(3, len(values) // 4 or 3)))
    roll_mean = values.rolling(window=w, min_periods=max(2, w // 3)).mean()
    roll_std = values.rolling(window=w, min_periods=max(2, w // 3)).std(ddof=0)
    # fallback for leading NaNs
    roll_mean = roll_mean.fillna(values.expanding(min_periods=1).mean())
    roll_std = roll_std.fillna(values.expanding(min_periods=2).std(ddof=0)).fillna(0.0)
    return roll_mean, roll_std


async def demand_trend(
    session: AsyncSession,
    *,
    city: str | None = None,
    zone_name: str | None = None,
    limit: int = 168,
) -> dict[str, Any]:
    """Hourly demand trend (default last 168 hours of series for scope)."""
    filters = []
    params: dict[str, Any] = {"limit": limit}
    if city:
        filters.append("city = :city")
        params["city"] = city
    if zone_name:
        filters.append("zone = :zone")
        params["zone"] = zone_name
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    sql = f"""
        SELECT timestamp, city, zone, zone_id,
               demand_requests, available_vehicles, unserved_requests, demand_index
        FROM demand
        {where}
        ORDER BY timestamp DESC
        LIMIT :limit
    """
    df = await _read_sql(session, sql, params)
    if df.empty:
        return _empty("demand_trend", "No demand rows found for the requested scope.")

    df = df.sort_values("timestamp")
    data = [
        {
            "timestamp": _ts(r.timestamp),
            "city": r.city,
            "zone": r.zone,
            "demand_requests": int(r.demand_requests),
            "available_vehicles": int(r.available_vehicles),
            "unserved_requests": int(r.unserved_requests),
            "demand_index": _json_num(r.demand_index),
        }
        for r in df.itertuples(index=False)
    ]
    metrics = {
        "points": int(len(df)),
        "avg_demand_requests": _json_num(df["demand_requests"].mean()),
        "max_demand_requests": int(df["demand_requests"].max()),
        "min_demand_requests": int(df["demand_requests"].min()),
        "city": city,
        "zone": zone_name,
    }
    observations = [
        f"Average demand_requests = {metrics['avg_demand_requests']} over {metrics['points']} hourly points.",
        f"Peak demand_requests = {metrics['max_demand_requests']}.",
    ]
    # recent vs earlier half
    mid = len(df) // 2
    if mid >= 3:
        early = df.iloc[:mid]["demand_requests"].mean()
        late = df.iloc[mid:]["demand_requests"].mean()
        change_pct = ((late - early) / early * 100.0) if early else None
        metrics["early_avg_demand"] = _json_num(early)
        metrics["recent_avg_demand"] = _json_num(late)
        metrics["recent_vs_early_pct"] = _json_num(change_pct)
        if change_pct is not None:
            direction = "increased" if change_pct > 0 else "decreased"
            observations.append(
                f"Recent-half average demand {direction} by {abs(change_pct):.1f}% vs earlier half."
            )
    return {
        "analysis_type": "demand_trend",
        "summary": (
            f"Demand trend for {zone_name or city or 'all cities'}: "
            f"avg {metrics['avg_demand_requests']} requests/hour "
            f"(n={metrics['points']})."
        ),
        "metrics": metrics,
        "observations": observations,
        "recommendations": [
            "Review peak hours if recent demand is rising.",
            "Cross-check with availability_trend for supply coverage.",
        ],
        "data": data,
    }


async def availability_trend(
    session: AsyncSession,
    *,
    city: str | None = None,
    zone_name: str | None = None,
    limit: int = 168,
) -> dict[str, Any]:
    filters = []
    params: dict[str, Any] = {"limit": limit}
    if city:
        filters.append("city = :city")
        params["city"] = city
    if zone_name:
        filters.append("zone = :zone")
        params["zone"] = zone_name
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    sql = f"""
        SELECT timestamp, city, zone, available_vehicles, demand_requests
        FROM demand
        {where}
        ORDER BY timestamp DESC
        LIMIT :limit
    """
    df = await _read_sql(session, sql, params)
    if df.empty:
        return _empty("availability_trend", "No availability rows found.")

    df = df.sort_values("timestamp")
    data = [
        {
            "timestamp": _ts(r.timestamp),
            "city": r.city,
            "zone": r.zone,
            "available": int(r.available_vehicles),
            "demand_requests": int(r.demand_requests),
        }
        for r in df.itertuples(index=False)
    ]
    metrics = {
        "points": int(len(df)),
        "avg_available": _json_num(df["available_vehicles"].mean()),
        "min_available": int(df["available_vehicles"].min()),
        "max_available": int(df["available_vehicles"].max()),
        "city": city,
        "zone": zone_name,
    }
    return {
        "analysis_type": "availability_trend",
        "summary": (
            f"Availability trend for {zone_name or city or 'all cities'}: "
            f"avg {metrics['avg_available']} vehicles "
            f"(min {metrics['min_available']}, max {metrics['max_available']})."
        ),
        "metrics": metrics,
        "observations": [
            f"Mean available_vehicles = {metrics['avg_available']}.",
            f"Lowest observed availability = {metrics['min_available']}.",
        ],
        "recommendations": [
            "Investigate hours where available_vehicles approach the minimum.",
        ],
        "data": data,
    }


async def demand_vs_availability(
    session: AsyncSession,
    *,
    city: str | None = None,
    zone_name: str | None = None,
    limit: int = 168,
) -> dict[str, Any]:
    result = await demand_trend(session, city=city, zone_name=zone_name, limit=limit)
    if not result["data"]:
        result["analysis_type"] = "demand_vs_availability"
        return result

    df = pd.DataFrame(result["data"])
    df["gap"] = df["demand_requests"] - df["available_vehicles"]
    data = [
        {
            "timestamp": r.timestamp,
            "demand": int(r.demand_requests),
            "available": int(r.available_vehicles),
            "gap": int(r.gap),
        }
        for r in df.itertuples(index=False)
    ]
    metrics = {
        **{k: result["metrics"][k] for k in ("city", "zone", "points") if k in result["metrics"]},
        "avg_demand": _json_num(df["demand_requests"].mean()),
        "avg_available": _json_num(df["available_vehicles"].mean()),
        "avg_gap": _json_num(df["gap"].mean()),
        "hours_demand_exceeds_supply": int((df["gap"] > 0).sum()),
    }
    return {
        "analysis_type": "demand_vs_availability",
        "summary": (
            f"Demand vs availability for {zone_name or city or 'all cities'}: "
            f"avg gap (demand−available) = {metrics['avg_gap']} "
            f"across {metrics['points']} hours."
        ),
        "metrics": metrics,
        "observations": [
            f"Hours where demand > available: {metrics['hours_demand_exceeds_supply']}.",
            f"Average demand = {metrics['avg_demand']}, average available = {metrics['avg_available']}.",
        ],
        "recommendations": [
            "Prioritise repositioning into zones with sustained positive gap.",
        ],
        "data": data,
    }


async def unserved_demand(
    session: AsyncSession,
    *,
    city: str | None = None,
    limit_zones: int = 15,
) -> dict[str, Any]:
    filters = ["TRUE"]
    params: dict[str, Any] = {"limit_zones": limit_zones}
    if city:
        filters.append("city = :city")
        params["city"] = city
    sql = f"""
        SELECT zone_id, zone, city,
               SUM(unserved_requests) AS total_unserved,
               SUM(demand_requests) AS total_demand,
               AVG(unserved_requests::float) AS avg_unserved
        FROM demand
        WHERE {' AND '.join(filters)}
        GROUP BY zone_id, zone, city
        ORDER BY total_unserved DESC
        LIMIT :limit_zones
    """
    df = await _read_sql(session, sql, params)
    data = [
        {
            "zone_id": r.zone_id,
            "zone": r.zone,
            "city": r.city,
            "total_unserved": int(r.total_unserved),
            "total_demand": int(r.total_demand),
            "avg_unserved": _json_num(r.avg_unserved),
        }
        for r in df.itertuples(index=False)
    ]
    metrics = {
        "city": city,
        "zones_returned": int(len(df)),
        "top_zone": data[0]["zone"] if data else None,
        "top_unserved": data[0]["total_unserved"] if data else 0,
    }
    return {
        "analysis_type": "unserved_demand",
        "summary": (
            f"Highest unserved demand zone: {metrics['top_zone']} "
            f"({metrics['top_unserved']} unserved requests)."
            if data
            else "No unserved demand rows found."
        ),
        "metrics": metrics,
        "observations": [
            f"{row['zone']} ({row['city']}): {row['total_unserved']} unserved"
            for row in data[:5]
        ],
        "recommendations": [
            "Increase supply or adjust dispatch priority in top unserved zones.",
        ],
        "data": data,
    }


async def revenue_trend(
    session: AsyncSession,
    *,
    city: str | None = None,
) -> dict[str, Any]:
    filters = ["TRUE"]
    params: dict[str, Any] = {}
    if city:
        filters.append("city = :city")
        params["city"] = city
    sql = f"""
        SELECT DATE_TRUNC('day', pickup_time) AS day,
               SUM(fare_gbp) AS revenue_gbp,
               COUNT(*) AS trips,
               AVG(fare_gbp) AS avg_fare
        FROM trips
        WHERE {' AND '.join(filters)}
        GROUP BY 1
        ORDER BY 1
    """
    df = await _read_sql(session, sql, params)
    data = [
        {
            "timestamp": _ts(r.day),
            "revenue_gbp": _json_num(r.revenue_gbp),
            "trips": int(r.trips),
            "avg_fare": _json_num(r.avg_fare),
        }
        for r in df.itertuples(index=False)
    ]
    metrics = {
        "city": city,
        "days": int(len(df)),
        "total_revenue_gbp": _json_num(df["revenue_gbp"].sum()) if not df.empty else 0,
        "avg_daily_revenue_gbp": _json_num(df["revenue_gbp"].mean()) if not df.empty else 0,
    }
    return {
        "analysis_type": "revenue_trend",
        "summary": (
            f"Revenue trend for {city or 'all cities'}: "
            f"total £{metrics['total_revenue_gbp']} over {metrics['days']} days "
            f"(avg £{metrics['avg_daily_revenue_gbp']}/day)."
        ),
        "metrics": metrics,
        "observations": [
            f"Total revenue = £{metrics['total_revenue_gbp']}.",
            f"Average daily revenue = £{metrics['avg_daily_revenue_gbp']}.",
        ],
        "recommendations": [
            "Compare peak revenue days with peak_hours and demand_trend outputs.",
        ],
        "data": data,
    }


async def average_trip_metric(
    session: AsyncSession,
    *,
    metric: str,
    city: str | None = None,
    pickup_zone: str | None = None,
) -> dict[str, Any]:
    col = "distance_miles" if metric == "distance" else "duration_minutes"
    analysis_type = (
        "average_trip_distance" if metric == "distance" else "average_trip_duration"
    )
    filters = ["TRUE"]
    params: dict[str, Any] = {}
    if city:
        filters.append("city = :city")
        params["city"] = city
    if pickup_zone:
        filters.append("pickup_zone = :pickup_zone")
        params["pickup_zone"] = pickup_zone
    sql = f"""
        SELECT AVG({col}) AS avg_value, COUNT(*) AS trip_count,
               MIN({col}) AS min_value, MAX({col}) AS max_value
        FROM trips
        WHERE {' AND '.join(filters)}
    """
    df = await _read_sql(session, sql, params)
    row = df.iloc[0]
    metrics = {
        "city": city,
        "pickup_zone": pickup_zone,
        "metric": col,
        "average": _json_num(row["avg_value"]),
        "trip_count": int(row["trip_count"] or 0),
        "min": _json_num(row["min_value"]),
        "max": _json_num(row["max_value"]),
    }
    unit = "miles" if metric == "distance" else "minutes"
    return {
        "analysis_type": analysis_type,
        "summary": (
            f"Average trip {metric} = {metrics['average']} {unit} "
            f"(n={metrics['trip_count']}"
            f"{', ' + (pickup_zone or city) if (pickup_zone or city) else ''})."
        ),
        "metrics": metrics,
        "observations": [
            f"Sample size = {metrics['trip_count']} trips.",
            f"Range = {metrics['min']} to {metrics['max']} {unit}.",
        ],
        "recommendations": [],
        "data": [
            {
                "metric": col,
                "average": metrics["average"],
                "trip_count": metrics["trip_count"],
                "city": city,
                "pickup_zone": pickup_zone,
            }
        ],
    }


async def cancellation_rate(
    session: AsyncSession, *, city: str | None = None
) -> dict[str, Any]:
    filters = ["TRUE"]
    params: dict[str, Any] = {}
    if city:
        filters.append("city = :city")
        params["city"] = city
    sql = f"""
        SELECT
          COUNT(*) AS total_bookings,
          COUNT(*) FILTER (WHERE booking_status = 'CANCELLED') AS cancelled,
          ROUND(
            100.0 * COUNT(*) FILTER (WHERE booking_status = 'CANCELLED')
            / NULLIF(COUNT(*), 0), 2
          ) AS cancellation_rate_pct
        FROM bookings
        WHERE {' AND '.join(filters)}
    """
    df = await _read_sql(session, sql, params)
    row = df.iloc[0]
    metrics = {
        "city": city,
        "total_bookings": int(row["total_bookings"] or 0),
        "cancelled": int(row["cancelled"] or 0),
        "cancellation_rate_pct": _json_num(row["cancellation_rate_pct"]),
    }
    # by channel for chart
    sql2 = f"""
        SELECT booking_channel,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE booking_status = 'CANCELLED') AS cancelled
        FROM bookings
        WHERE {' AND '.join(filters)}
        GROUP BY booking_channel
        ORDER BY total DESC
    """
    ch = await _read_sql(session, sql2, params)
    data = [
        {
            "booking_channel": r.booking_channel,
            "total": int(r.total),
            "cancelled": int(r.cancelled),
            "cancellation_rate_pct": _json_num(
                (100.0 * r.cancelled / r.total) if r.total else None
            ),
        }
        for r in ch.itertuples(index=False)
    ]
    return {
        "analysis_type": "cancellation_rate",
        "summary": (
            f"Cancellation rate for {city or 'all cities'} = "
            f"{metrics['cancellation_rate_pct']}% "
            f"({metrics['cancelled']}/{metrics['total_bookings']} bookings)."
        ),
        "metrics": metrics,
        "observations": [
            f"Overall cancellation rate = {metrics['cancellation_rate_pct']}%.",
        ],
        "recommendations": [
            "Inspect channels with above-average cancellation_rate_pct.",
        ],
        "data": data,
    }


async def peak_hours(
    session: AsyncSession, *, city: str | None = None
) -> dict[str, Any]:
    filters = ["TRUE"]
    params: dict[str, Any] = {}
    if city:
        filters.append("city = :city")
        params["city"] = city
    sql = f"""
        SELECT EXTRACT(HOUR FROM timestamp)::int AS hour,
               AVG(demand_requests) AS avg_demand,
               AVG(available_vehicles) AS avg_available,
               SUM(demand_requests) AS total_demand
        FROM demand
        WHERE {' AND '.join(filters)}
        GROUP BY 1
        ORDER BY 1
    """
    df = await _read_sql(session, sql, params)
    data = [
        {
            "hour": int(r.hour),
            "avg_demand": _json_num(r.avg_demand),
            "avg_available": _json_num(r.avg_available),
            "total_demand": int(r.total_demand),
        }
        for r in df.itertuples(index=False)
    ]
    top = df.sort_values("avg_demand", ascending=False).head(3)
    metrics = {
        "city": city,
        "busiest_hours": [int(h) for h in top["hour"].tolist()],
        "peak_avg_demand": _json_num(top.iloc[0]["avg_demand"]) if not top.empty else None,
    }
    return {
        "analysis_type": "peak_hours",
        "summary": (
            f"Busiest hours for {city or 'all cities'}: "
            f"{', '.join(str(h)+':00' for h in metrics['busiest_hours'])} "
            f"(peak avg demand {metrics['peak_avg_demand']})."
        ),
        "metrics": metrics,
        "observations": [
            f"Hour {int(r.hour)}:00 avg demand = {_json_num(r.avg_demand)}"
            for r in top.itertuples(index=False)
        ],
        "recommendations": [
            "Align shift coverage and airport staging with peak hours.",
        ],
        "data": data,
    }


async def zone_performance(
    session: AsyncSession, *, city: str | None = None, limit: int = 15
) -> dict[str, Any]:
    filters = ["TRUE"]
    params: dict[str, Any] = {"limit": limit}
    if city:
        filters.append("d.city = :city")
        params["city"] = city
    sql = f"""
        SELECT d.zone_id, d.zone, d.city,
               SUM(d.demand_requests) AS total_demand,
               AVG(d.available_vehicles) AS avg_available,
               SUM(d.unserved_requests) AS total_unserved,
               AVG(d.demand_index) AS avg_demand_index
        FROM demand d
        WHERE {' AND '.join(filters)}
        GROUP BY d.zone_id, d.zone, d.city
        ORDER BY total_demand DESC
        LIMIT :limit
    """
    df = await _read_sql(session, sql, params)
    data = [
        {
            "zone_id": r.zone_id,
            "zone": r.zone,
            "city": r.city,
            "total_demand": int(r.total_demand),
            "avg_available": _json_num(r.avg_available),
            "total_unserved": int(r.total_unserved),
            "avg_demand_index": _json_num(r.avg_demand_index),
            "gap": int(r.total_demand) - int(round(float(r.avg_available or 0))),
        }
        for r in df.itertuples(index=False)
    ]
    return {
        "analysis_type": "zone_performance",
        "summary": (
            f"Top zone by demand: {data[0]['zone']} ({data[0]['city']}) "
            f"with {data[0]['total_demand']} requests."
            if data
            else "No zone performance data."
        ),
        "metrics": {
            "city": city,
            "zones": len(data),
            "top_zone": data[0]["zone"] if data else None,
        },
        "observations": [
            f"{d['zone']}: demand={d['total_demand']}, unserved={d['total_unserved']}"
            for d in data[:5]
        ],
        "recommendations": [
            "Focus operations on high-demand / high-unserved zones first.",
        ],
        "data": data,
    }


async def city_comparison(
    session: AsyncSession, *, cities: list[str] | None = None
) -> dict[str, Any]:
    requested = cities or list(CITIES)
    city_list = [c for c in requested if c in CITIES] or list(CITIES)
    # Allowlisted literals only — never interpolate raw user strings
    in_list = ", ".join(f"'{c}'" for c in city_list)
    sql = f"""
        SELECT city,
               SUM(demand_requests) AS total_demand,
               AVG(available_vehicles) AS avg_available,
               SUM(unserved_requests) AS total_unserved,
               AVG(demand_index) AS avg_demand_index
        FROM demand
        WHERE city IN ({in_list})
        GROUP BY city
        ORDER BY total_demand DESC
    """
    df = await _read_sql(session, sql)
    sql2 = f"""
        SELECT city,
               COUNT(*) AS trips,
               AVG(distance_miles) AS avg_distance,
               AVG(duration_minutes) AS avg_duration,
               SUM(fare_gbp) AS revenue_gbp
        FROM trips
        WHERE city IN ({in_list})
        GROUP BY city
    """
    trips = await _read_sql(session, sql2)
    trips_map = {r.city: r for r in trips.itertuples(index=False)}
    data = []
    for r in df.itertuples(index=False):
        t = trips_map.get(r.city)
        data.append(
            {
                "city": r.city,
                "total_demand": int(r.total_demand),
                "avg_available": _json_num(r.avg_available),
                "total_unserved": int(r.total_unserved),
                "avg_demand_index": _json_num(r.avg_demand_index),
                "trips": int(t.trips) if t else 0,
                "avg_distance": _json_num(t.avg_distance) if t else None,
                "avg_duration": _json_num(t.avg_duration) if t else None,
                "revenue_gbp": _json_num(t.revenue_gbp) if t else None,
            }
        )
    return {
        "analysis_type": "city_comparison",
        "summary": (
            f"City demand ranking: "
            + ", ".join(f"{d['city']}={d['total_demand']}" for d in data[:5])
            if data
            else "No city comparison data."
        ),
        "metrics": {
            "cities": [d["city"] for d in data],
            "highest_demand_city": data[0]["city"] if data else None,
        },
        "observations": [
            f"{d['city']}: demand={d['total_demand']}, revenue=£{d['revenue_gbp']}"
            for d in data
        ],
        "recommendations": [
            "Use city-level gaps to prioritise fleet allocation between cities.",
        ],
        "data": data,
    }


async def vehicle_utilization(
    session: AsyncSession, *, city: str | None = None
) -> dict[str, Any]:
    filters = ["TRUE"]
    params: dict[str, Any] = {}
    if city:
        filters.append("city = :city")
        params["city"] = city
    sql = f"""
        SELECT status, COUNT(*) AS count
        FROM vehicles
        WHERE {' AND '.join(filters)}
        GROUP BY status
        ORDER BY count DESC
    """
    df = await _read_sql(session, sql, params)
    total = int(df["count"].sum()) if not df.empty else 0
    data = [
        {
            "status": r.status,
            "count": int(r.count),
            "pct": _json_num(100.0 * r.count / total) if total else 0,
        }
        for r in df.itertuples(index=False)
    ]
    busy_like = {"BUSY", "EN_ROUTE_PICKUP"}
    utilized = int(df.loc[df["status"].isin(busy_like), "count"].sum()) if not df.empty else 0
    available = int(df.loc[df["status"] == "AVAILABLE", "count"].sum()) if not df.empty else 0
    metrics = {
        "city": city,
        "fleet_size": total,
        "utilized": utilized,
        "available": available,
        "utilization_pct": _json_num(100.0 * utilized / total) if total else 0,
    }
    return {
        "analysis_type": "vehicle_utilization",
        "summary": (
            f"Vehicle utilization for {city or 'all cities'} = "
            f"{metrics['utilization_pct']}% "
            f"({utilized}/{total} busy or en-route)."
        ),
        "metrics": metrics,
        "observations": [f"{d['status']}: {d['count']} ({d['pct']}%)" for d in data],
        "recommendations": [
            "If utilization is high with rising unserved demand, consider surge coverage.",
        ],
        "data": data,
    }


async def availability_anomaly(
    session: AsyncSession,
    *,
    zone_name: str | None = None,
    city: str | None = None,
    z_threshold: float = 1.5,
    limit: int = 500,
) -> dict[str, Any]:
    """Detect low-availability anomalies via rolling mean and z-score."""
    filters = []
    params: dict[str, Any] = {"limit": limit}
    if zone_name:
        filters.append("zone = :zone")
        params["zone"] = zone_name
    if city:
        filters.append("city = :city")
        params["city"] = city
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    sql = f"""
        SELECT timestamp, city, zone, available_vehicles, demand_requests, unserved_requests
        FROM demand
        {where}
        ORDER BY timestamp
        LIMIT :limit
    """
    # Prefer full series for a zone; if too large, still order by time then take recent window
    if zone_name:
        sql = f"""
            SELECT timestamp, city, zone, available_vehicles, demand_requests, unserved_requests
            FROM demand
            {where}
            ORDER BY timestamp
        """
        params.pop("limit", None)
    df = await _read_sql(session, sql, params)
    if df.empty:
        return _empty("availability_anomaly", "No rows available for anomaly detection.")

    df = df.sort_values("timestamp").reset_index(drop=True)
    series = df["available_vehicles"].astype(float)
    expected, roll_std = _rolling_baseline(series, window=24)
    z = (series - expected) / roll_std.replace(0, np.nan)
    z = z.fillna(0.0)
    deviation_pct = np.where(expected != 0, (series - expected) / expected * 100.0, 0.0)

    df["expected"] = expected
    df["z_score"] = z
    df["deviation_pct"] = deviation_pct
    anomalies = df[df["z_score"] <= -z_threshold].copy()

    data = [
        {
            "timestamp": _ts(r.timestamp),
            "available": int(r.available_vehicles),
            "expected": _json_num(r.expected),
            "deviation_pct": _json_num(r.deviation_pct),
            "z_score": _json_num(r.z_score),
            "demand_requests": int(r.demand_requests),
            "unserved_requests": int(r.unserved_requests),
        }
        for r in df.tail(168).itertuples(index=False)
    ]
    anomaly_points = [
        {
            "timestamp": _ts(r.timestamp),
            "available": int(r.available_vehicles),
            "expected": _json_num(r.expected),
            "deviation_pct": _json_num(r.deviation_pct),
            "z_score": _json_num(r.z_score),
        }
        for r in anomalies.tail(50).itertuples(index=False)
    ]
    latest = df.iloc[-1]
    metrics = {
        "city": city or (str(df.iloc[0]["city"]) if not df.empty else None),
        "zone": zone_name or (str(df.iloc[0]["zone"]) if not df.empty else None),
        "points": int(len(df)),
        "anomaly_count": int(len(anomalies)),
        "z_threshold": z_threshold,
        "method": "rolling_mean_24h + z_score",
        "latest_available": int(latest["available_vehicles"]),
        "latest_expected": _json_num(latest["expected"]),
        "latest_deviation_pct": _json_num(latest["deviation_pct"]),
        "latest_z_score": _json_num(latest["z_score"]),
        "avg_demand_at_anomalies": _json_num(anomalies["demand_requests"].mean())
        if not anomalies.empty
        else None,
    }
    scope = metrics["zone"] or metrics["city"] or "selected scope"
    if metrics["anomaly_count"] == 0:
        summary = (
            f"No low-availability anomalies detected for {scope} "
            f"(z-score threshold −{z_threshold}). "
            f"Latest available={metrics['latest_available']} vs expected={metrics['latest_expected']}."
        )
        observations = [
            f"Analysed {metrics['points']} hourly points using {metrics['method']}.",
            f"Latest deviation = {metrics['latest_deviation_pct']}%.",
        ]
        recommendations = ["Continue monitoring; no immediate supply shortfall flagged."]
    else:
        summary = (
            f"Availability is low vs baseline for {scope} in {metrics['anomaly_count']} hour(s). "
            f"Latest available={metrics['latest_available']} vs expected={metrics['latest_expected']} "
            f"({metrics['latest_deviation_pct']}% deviation, z={metrics['latest_z_score']})."
        )
        observations = [
            f"{metrics['anomaly_count']} points with z-score ≤ −{z_threshold}.",
            f"Average demand during anomalies = {metrics['avg_demand_at_anomalies']}.",
            f"Latest deviation_pct = {metrics['latest_deviation_pct']}%.",
        ]
        recommendations = [
            "Stage additional vehicles into the zone during recurrent low-availability hours.",
            "Compare against unserved_demand for the same zone.",
        ]

    return {
        "analysis_type": "availability_anomaly",
        "summary": summary,
        "metrics": {**metrics, "anomaly_points_returned": len(anomaly_points)},
        "observations": observations,
        "recommendations": recommendations,
        "data": data,
        # keep anomalies embedded for UI if needed via metrics + filtered data
    }


async def demand_anomaly(
    session: AsyncSession,
    *,
    zone_name: str | None = None,
    city: str | None = None,
    z_threshold: float = 1.5,
) -> dict[str, Any]:
    filters = []
    params: dict[str, Any] = {}
    if zone_name:
        filters.append("zone = :zone")
        params["zone"] = zone_name
    if city:
        filters.append("city = :city")
        params["city"] = city
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    sql = f"""
        SELECT timestamp, city, zone, demand_requests, available_vehicles, demand_index
        FROM demand
        {where}
        ORDER BY timestamp
    """
    df = await _read_sql(session, sql, params)
    if df.empty:
        return _empty("demand_anomaly", "No demand rows for anomaly detection.")

    series = df["demand_requests"].astype(float)
    expected, roll_std = _rolling_baseline(series, window=24)
    z = (series - expected) / roll_std.replace(0, np.nan)
    z = z.fillna(0.0)
    deviation_pct = np.where(expected != 0, (series - expected) / expected * 100.0, 0.0)
    df = df.copy()
    df["expected"] = expected
    df["z_score"] = z
    df["deviation_pct"] = deviation_pct
    high = df[df["z_score"] >= z_threshold]

    # zone-level unusual demand ranking
    zone_sql = """
        SELECT zone, city,
               AVG(demand_requests) AS avg_demand,
               STDDEV_POP(demand_requests) AS std_demand,
               MAX(demand_requests) AS max_demand
        FROM demand
        GROUP BY zone, city
    """
    zones = await _read_sql(session, zone_sql)
    if not zones.empty and zones["std_demand"].notna().any():
        zones = zones.copy()
        zones["z_like"] = np.where(
            zones["std_demand"].fillna(0) > 0,
            (zones["max_demand"] - zones["avg_demand"]) / zones["std_demand"],
            0.0,
        )
        top_zones = zones.sort_values("z_like", ascending=False).head(10)
    else:
        top_zones = zones.head(0)

    data = [
        {
            "timestamp": _ts(r.timestamp),
            "demand": int(r.demand_requests),
            "expected": _json_num(r.expected),
            "deviation_pct": _json_num(r.deviation_pct),
            "z_score": _json_num(r.z_score),
        }
        for r in df.tail(168).itertuples(index=False)
    ]
    zone_rank = [
        {
            "zone": r.zone,
            "city": r.city,
            "avg_demand": _json_num(r.avg_demand),
            "max_demand": int(r.max_demand),
            "peak_z_like": _json_num(r.z_like),
        }
        for r in top_zones.itertuples(index=False)
    ]
    metrics = {
        "city": city,
        "zone": zone_name,
        "high_anomaly_count": int(len(high)),
        "z_threshold": z_threshold,
        "method": "rolling_mean_24h + z_score",
        "top_unusual_zone": zone_rank[0]["zone"] if zone_rank else None,
    }
    return {
        "analysis_type": "demand_anomaly",
        "summary": (
            f"Found {metrics['high_anomaly_count']} high-demand anomaly hour(s) "
            f"for {zone_name or city or 'selected scope'} "
            f"(z ≥ {z_threshold}). "
            + (
                f"Zone with largest peak spike pattern: {metrics['top_unusual_zone']}."
                if metrics["top_unusual_zone"]
                else ""
            )
        ),
        "metrics": metrics,
        "observations": [
            f"{z['zone']} ({z['city']}): peak_z_like={z['peak_z_like']}, max={z['max_demand']}"
            for z in zone_rank[:5]
        ],
        "recommendations": [
            "Prepare surge coverage for zones with repeated high z-score demand spikes.",
        ],
        "data": data if zone_name or city else zone_rank,
    }


async def demand_supply_gap(
    session: AsyncSession, *, city: str | None = None, limit: int = 15
) -> dict[str, Any]:
    filters = ["TRUE"]
    params: dict[str, Any] = {"limit": limit}
    if city:
        filters.append("city = :city")
        params["city"] = city
    sql = f"""
        SELECT zone_id, zone, city,
               SUM(demand_requests) AS total_demand,
               SUM(available_vehicles) AS total_available_vehicle_hours,
               SUM(unserved_requests) AS total_unserved,
               SUM(demand_requests) - SUM(available_vehicles) AS demand_supply_gap
        FROM demand
        WHERE {' AND '.join(filters)}
        GROUP BY zone_id, zone, city
        ORDER BY demand_supply_gap DESC
        LIMIT :limit
    """
    df = await _read_sql(session, sql, params)
    data = [
        {
            "zone_id": r.zone_id,
            "zone": r.zone,
            "city": r.city,
            "total_demand": int(r.total_demand),
            "total_available_vehicle_hours": int(r.total_available_vehicle_hours),
            "total_unserved": int(r.total_unserved),
            "demand_supply_gap": int(r.demand_supply_gap),
        }
        for r in df.itertuples(index=False)
    ]
    return {
        "analysis_type": "demand_supply_gap",
        "summary": (
            f"Largest demand/supply gap: {data[0]['zone']} ({data[0]['city']}) "
            f"gap={data[0]['demand_supply_gap']}."
            if data
            else "No gap data."
        ),
        "metrics": {
            "city": city,
            "top_zone": data[0]["zone"] if data else None,
            "top_gap": data[0]["demand_supply_gap"] if data else None,
        },
        "observations": [
            f"{d['zone']}: gap={d['demand_supply_gap']}, unserved={d['total_unserved']}"
            for d in data[:5]
        ],
        "recommendations": [
            "Reallocate idle fleet toward zones with the largest positive gaps.",
        ],
        "data": data,
    }


async def normal_snapshot(
    session: AsyncSession,
    *,
    city: str | None = None,
    zone_name: str | None = None,
    hour: int | None = None,
) -> dict[str, Any]:
    """Normal (non-anomaly) snapshot query, e.g. London demand at 8 AM."""
    filters = []
    params: dict[str, Any] = {}
    if city:
        filters.append("city = :city")
        params["city"] = city
    if zone_name:
        filters.append("zone = :zone")
        params["zone"] = zone_name
    if hour is not None:
        filters.append("EXTRACT(HOUR FROM timestamp)::int = :hour")
        params["hour"] = hour
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    sql = f"""
        SELECT AVG(demand_requests) AS avg_demand,
               AVG(available_vehicles) AS avg_available,
               AVG(unserved_requests) AS avg_unserved,
               COUNT(*) AS points
        FROM demand
        {where}
    """
    df = await _read_sql(session, sql, params)
    row = df.iloc[0]
    metrics = {
        "city": city,
        "zone": zone_name,
        "hour": hour,
        "avg_demand": _json_num(row["avg_demand"]),
        "avg_available": _json_num(row["avg_available"]),
        "avg_unserved": _json_num(row["avg_unserved"]),
        "points": int(row["points"] or 0),
    }
    scope = ", ".join(
        p
        for p in [
            city,
            zone_name,
            f"{hour:02d}:00" if hour is not None else None,
        ]
        if p is not None
    )
    return {
        "analysis_type": "normal_snapshot",
        "summary": (
            f"Snapshot for {scope or 'all data'}: "
            f"avg demand={metrics['avg_demand']}, "
            f"avg available={metrics['avg_available']} "
            f"(n={metrics['points']})."
        ),
        "metrics": metrics,
        "observations": [
            f"Average demand_requests = {metrics['avg_demand']}.",
            f"Average available_vehicles = {metrics['avg_available']}.",
        ],
        "recommendations": [],
        "data": [metrics],
    }


def _empty(analysis_type: str, message: str) -> dict[str, Any]:
    return {
        "analysis_type": analysis_type,
        "summary": message,
        "metrics": {},
        "observations": [message],
        "recommendations": [],
        "data": [],
    }
