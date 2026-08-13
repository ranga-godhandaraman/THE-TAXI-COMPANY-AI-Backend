"""CSV → Neon PostgreSQL bulk importer.

Usage (from backend/):
  python -m app.db.import_data
  python -m app.db.import_data --force   # truncate + reload
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

from app.config import PROJECT_ROOT, get_settings
from app.db.session import redact_db_error, require_neon_settings

DATASET_DIR = PROJECT_ROOT / "uk_taxi_dataset"

REQUIRED_FILES = {
    "zones": "zones.csv",
    "drivers": "drivers.csv",
    "vehicles": "vehicles.csv",
    "fares": "fares.csv",
    "trips": "trips.csv",
    "bookings": "bookings.csv",
    "vehicle_events": "vehicle_events.csv",
    "demand": "demand.csv",
}

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "zones": ("zone_id", "city", "zone_name", "latitude", "longitude", "zone_type"),
    "drivers": (
        "driver_id",
        "driver_name",
        "city",
        "licence_type",
        "licence_status",
        "rating",
        "years_experience",
        "wheelchair_training",
    ),
    "vehicles": (
        "vehicle_id",
        "registration",
        "driver_id",
        "city",
        "vehicle_type",
        "make",
        "model",
        "fuel_type",
        "seats",
        "wheelchair_accessible",
        "zone_id",
        "current_lat",
        "current_lon",
        "status",
        "last_status_update",
    ),
    "fares": (
        "city",
        "vehicle_type",
        "base_fare_gbp",
        "per_mile_gbp",
        "per_minute_gbp",
    ),
    "trips": (
        "trip_id",
        "vehicle_id",
        "driver_id",
        "city",
        "pickup_time",
        "distance_miles",
        "duration_minutes",
        "dropoff_time",
        "pickup_zone_id",
        "pickup_zone",
        "dropoff_zone_id",
        "dropoff_zone",
        "pickup_lat",
        "pickup_lon",
        "dropoff_lat",
        "dropoff_lon",
        "passenger_count",
        "fare_gbp",
        "payment_type",
        "status",
    ),
    "bookings": (
        "booking_id",
        "trip_id",
        "city",
        "booking_time",
        "requested_pickup_time",
        "booking_channel",
        "booking_status",
        "passenger_count",
    ),
    "vehicle_events": (
        "event_id",
        "vehicle_id",
        "driver_id",
        "timestamp",
        "city",
        "zone_id",
        "latitude",
        "longitude",
        "status",
    ),
    "demand": (
        "timestamp",
        "zone_id",
        "city",
        "zone",
        "demand_requests",
        "available_vehicles",
        "unserved_requests",
        "demand_index",
    ),
}

LOAD_ORDER = (
    "zones",
    "drivers",
    "vehicles",
    "fares",
    "trips",
    "bookings",
    "vehicle_events",
    "demand",
)

TRUNCATE_ORDER = tuple(reversed(LOAD_ORDER))

BATCH_SIZE = 5_000


class ValidationError(Exception):
    pass


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes", "y", "t"}:
        return True
    if v in {"false", "0", "no", "n", "f"}:
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def _parse_ts(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"invalid timestamp: {value!r}")


def _parse_int(value: str) -> int:
    return int(value.strip())


def _parse_float(value: str) -> float:
    return float(value.strip())


def _parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value!r}") from exc


def _csv_path(name: str) -> Path:
    return DATASET_DIR / REQUIRED_FILES[name]


def ensure_files_exist() -> None:
    missing = [f for f in REQUIRED_FILES.values() if not (DATASET_DIR / f).exists()]
    if missing:
        raise ValidationError(
            f"Missing CSV files under {DATASET_DIR}: {', '.join(missing)}"
        )


def ensure_columns(name: str) -> None:
    path = _csv_path(name)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValidationError(f"{path.name}: empty or missing header")
        missing = [c for c in REQUIRED_COLUMNS[name] if c not in reader.fieldnames]
        if missing:
            raise ValidationError(f"{path.name}: missing columns {missing}")


def iter_csv(name: str) -> Iterator[dict[str, str]]:
    path = _csv_path(name)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=2):
            if any((v or "").strip() for v in row.values()):
                yield row
            else:
                raise ValidationError(f"{path.name}: empty row at line {i}")


def validate_dataset() -> dict[str, set[Any]]:
    """Validate types, PK uniqueness, and FK integrity before insertion."""
    print("Validating CSV files…")
    ensure_files_exist()
    for name in LOAD_ORDER:
        ensure_columns(name)

    refs: dict[str, set[Any]] = {}
    errors: list[str] = []

    # zones
    zone_ids: set[str] = set()
    for i, row in enumerate(iter_csv("zones"), start=2):
        try:
            zid = row["zone_id"].strip()
            if not zid:
                raise ValueError("empty zone_id")
            if zid in zone_ids:
                raise ValueError(f"duplicate zone_id {zid}")
            zone_ids.add(zid)
            _parse_float(row["latitude"])
            _parse_float(row["longitude"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"zones.csv:{i}: {exc}")
    refs["zone_ids"] = zone_ids
    print(f"  zones: {len(zone_ids):,} rows validated")

    # drivers
    driver_ids: set[str] = set()
    for i, row in enumerate(iter_csv("drivers"), start=2):
        try:
            did = row["driver_id"].strip()
            if did in driver_ids:
                raise ValueError(f"duplicate driver_id {did}")
            driver_ids.add(did)
            _parse_decimal(row["rating"])
            _parse_int(row["years_experience"])
            _parse_bool(row["wheelchair_training"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"drivers.csv:{i}: {exc}")
    refs["driver_ids"] = driver_ids
    print(f"  drivers: {len(driver_ids):,} rows validated")

    # vehicles
    vehicle_ids: set[str] = set()
    for i, row in enumerate(iter_csv("vehicles"), start=2):
        try:
            vid = row["vehicle_id"].strip()
            if vid in vehicle_ids:
                raise ValueError(f"duplicate vehicle_id {vid}")
            vehicle_ids.add(vid)
            did = row["driver_id"].strip()
            zid = row["zone_id"].strip()
            if did not in driver_ids:
                raise ValueError(f"FK driver_id not found: {did}")
            if zid not in zone_ids:
                raise ValueError(f"FK zone_id not found: {zid}")
            _parse_int(row["seats"])
            _parse_bool(row["wheelchair_accessible"])
            _parse_float(row["current_lat"])
            _parse_float(row["current_lon"])
            _parse_ts(row["last_status_update"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"vehicles.csv:{i}: {exc}")
            if len(errors) > 50:
                break
    refs["vehicle_ids"] = vehicle_ids
    print(f"  vehicles: {len(vehicle_ids):,} rows validated")

    # fares
    fare_keys: set[tuple[str, str]] = set()
    for i, row in enumerate(iter_csv("fares"), start=2):
        try:
            key = (row["city"].strip(), row["vehicle_type"].strip())
            if key in fare_keys:
                raise ValueError(f"duplicate fare key {key}")
            fare_keys.add(key)
            _parse_decimal(row["base_fare_gbp"])
            _parse_decimal(row["per_mile_gbp"])
            _parse_decimal(row["per_minute_gbp"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fares.csv:{i}: {exc}")
    refs["fare_keys"] = fare_keys
    print(f"  fares: {len(fare_keys):,} rows validated")

    # trips
    trip_ids: set[str] = set()
    for i, row in enumerate(iter_csv("trips"), start=2):
        try:
            tid = row["trip_id"].strip()
            if tid in trip_ids:
                raise ValueError(f"duplicate trip_id {tid}")
            trip_ids.add(tid)
            if row["vehicle_id"].strip() not in vehicle_ids:
                raise ValueError(f"FK vehicle_id not found: {row['vehicle_id']}")
            if row["driver_id"].strip() not in driver_ids:
                raise ValueError(f"FK driver_id not found: {row['driver_id']}")
            if row["pickup_zone_id"].strip() not in zone_ids:
                raise ValueError(f"FK pickup_zone_id not found: {row['pickup_zone_id']}")
            if row["dropoff_zone_id"].strip() not in zone_ids:
                raise ValueError(f"FK dropoff_zone_id not found: {row['dropoff_zone_id']}")
            _parse_ts(row["pickup_time"])
            _parse_ts(row["dropoff_time"])
            _parse_decimal(row["distance_miles"])
            _parse_int(row["duration_minutes"])
            _parse_float(row["pickup_lat"])
            _parse_float(row["pickup_lon"])
            _parse_float(row["dropoff_lat"])
            _parse_float(row["dropoff_lon"])
            _parse_int(row["passenger_count"])
            _parse_decimal(row["fare_gbp"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"trips.csv:{i}: {exc}")
            if len(errors) > 50:
                break
    refs["trip_ids"] = trip_ids
    print(f"  trips: {len(trip_ids):,} rows validated")

    # bookings
    booking_ids: set[str] = set()
    for i, row in enumerate(iter_csv("bookings"), start=2):
        try:
            bid = row["booking_id"].strip()
            if bid in booking_ids:
                raise ValueError(f"duplicate booking_id {bid}")
            booking_ids.add(bid)
            if row["trip_id"].strip() not in trip_ids:
                raise ValueError(f"FK trip_id not found: {row['trip_id']}")
            _parse_ts(row["booking_time"])
            _parse_ts(row["requested_pickup_time"])
            _parse_int(row["passenger_count"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"bookings.csv:{i}: {exc}")
            if len(errors) > 50:
                break
    refs["booking_ids"] = booking_ids
    print(f"  bookings: {len(booking_ids):,} rows validated")

    # vehicle_events
    event_ids: set[str] = set()
    for i, row in enumerate(iter_csv("vehicle_events"), start=2):
        try:
            eid = row["event_id"].strip()
            if eid in event_ids:
                raise ValueError(f"duplicate event_id {eid}")
            event_ids.add(eid)
            if row["vehicle_id"].strip() not in vehicle_ids:
                raise ValueError(f"FK vehicle_id not found: {row['vehicle_id']}")
            if row["driver_id"].strip() not in driver_ids:
                raise ValueError(f"FK driver_id not found: {row['driver_id']}")
            if row["zone_id"].strip() not in zone_ids:
                raise ValueError(f"FK zone_id not found: {row['zone_id']}")
            _parse_ts(row["timestamp"])
            _parse_float(row["latitude"])
            _parse_float(row["longitude"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"vehicle_events.csv:{i}: {exc}")
            if len(errors) > 50:
                break
    refs["event_ids"] = event_ids
    print(f"  vehicle_events: {len(event_ids):,} rows validated")

    # demand
    demand_keys: set[tuple[datetime, str]] = set()
    for i, row in enumerate(iter_csv("demand"), start=2):
        try:
            ts = _parse_ts(row["timestamp"])
            zid = row["zone_id"].strip()
            key = (ts, zid)
            if key in demand_keys:
                raise ValueError(f"duplicate demand key {key}")
            demand_keys.add(key)
            if zid not in zone_ids:
                raise ValueError(f"FK zone_id not found: {zid}")
            _parse_int(row["demand_requests"])
            _parse_int(row["available_vehicles"])
            _parse_int(row["unserved_requests"])
            _parse_decimal(row["demand_index"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"demand.csv:{i}: {exc}")
            if len(errors) > 50:
                break
    refs["demand_keys"] = demand_keys
    print(f"  demand: {len(demand_keys):,} rows validated")

    if errors:
        preview = "\n".join(errors[:20])
        raise ValidationError(
            f"{len(errors)} validation error(s). First errors:\n{preview}"
        )

    print("CSV validation passed.")
    return refs


Transform = Callable[[dict[str, str]], tuple[Any, ...]]


def _transform_zones(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["zone_id"].strip(),
        row["city"].strip(),
        row["zone_name"].strip(),
        _parse_float(row["latitude"]),
        _parse_float(row["longitude"]),
        row["zone_type"].strip(),
    )


def _transform_drivers(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["driver_id"].strip(),
        row["driver_name"].strip(),
        row["city"].strip(),
        row["licence_type"].strip(),
        row["licence_status"].strip(),
        _parse_decimal(row["rating"]),
        _parse_int(row["years_experience"]),
        _parse_bool(row["wheelchair_training"]),
    )


def _transform_vehicles(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["vehicle_id"].strip(),
        row["registration"].strip(),
        row["driver_id"].strip(),
        row["city"].strip(),
        row["vehicle_type"].strip(),
        row["make"].strip(),
        row["model"].strip(),
        row["fuel_type"].strip(),
        _parse_int(row["seats"]),
        _parse_bool(row["wheelchair_accessible"]),
        row["zone_id"].strip(),
        _parse_float(row["current_lat"]),
        _parse_float(row["current_lon"]),
        row["status"].strip(),
        _parse_ts(row["last_status_update"]),
    )


def _transform_fares(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["city"].strip(),
        row["vehicle_type"].strip(),
        _parse_decimal(row["base_fare_gbp"]),
        _parse_decimal(row["per_mile_gbp"]),
        _parse_decimal(row["per_minute_gbp"]),
    )


def _transform_trips(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["trip_id"].strip(),
        row["vehicle_id"].strip(),
        row["driver_id"].strip(),
        row["city"].strip(),
        _parse_ts(row["pickup_time"]),
        _parse_decimal(row["distance_miles"]),
        _parse_int(row["duration_minutes"]),
        _parse_ts(row["dropoff_time"]),
        row["pickup_zone_id"].strip(),
        row["pickup_zone"].strip(),
        row["dropoff_zone_id"].strip(),
        row["dropoff_zone"].strip(),
        _parse_float(row["pickup_lat"]),
        _parse_float(row["pickup_lon"]),
        _parse_float(row["dropoff_lat"]),
        _parse_float(row["dropoff_lon"]),
        _parse_int(row["passenger_count"]),
        _parse_decimal(row["fare_gbp"]),
        row["payment_type"].strip(),
        row["status"].strip(),
    )


def _transform_bookings(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["booking_id"].strip(),
        row["trip_id"].strip(),
        row["city"].strip(),
        _parse_ts(row["booking_time"]),
        _parse_ts(row["requested_pickup_time"]),
        row["booking_channel"].strip(),
        row["booking_status"].strip(),
        _parse_int(row["passenger_count"]),
    )


def _transform_events(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        row["event_id"].strip(),
        row["vehicle_id"].strip(),
        row["driver_id"].strip(),
        _parse_ts(row["timestamp"]),
        row["city"].strip(),
        row["zone_id"].strip(),
        _parse_float(row["latitude"]),
        _parse_float(row["longitude"]),
        row["status"].strip(),
    )


def _transform_demand(row: dict[str, str]) -> tuple[Any, ...]:
    return (
        _parse_ts(row["timestamp"]),
        row["zone_id"].strip(),
        row["city"].strip(),
        row["zone"].strip(),
        _parse_int(row["demand_requests"]),
        _parse_int(row["available_vehicles"]),
        _parse_int(row["unserved_requests"]),
        _parse_decimal(row["demand_index"]),
    )


TRANSFORMS: dict[str, Transform] = {
    "zones": _transform_zones,
    "drivers": _transform_drivers,
    "vehicles": _transform_vehicles,
    "fares": _transform_fares,
    "trips": _transform_trips,
    "bookings": _transform_bookings,
    "vehicle_events": _transform_events,
    "demand": _transform_demand,
}

COLUMNS: dict[str, tuple[str, ...]] = {
    "zones": REQUIRED_COLUMNS["zones"],
    "drivers": REQUIRED_COLUMNS["drivers"],
    "vehicles": REQUIRED_COLUMNS["vehicles"],
    "fares": REQUIRED_COLUMNS["fares"],
    "trips": REQUIRED_COLUMNS["trips"],
    "bookings": REQUIRED_COLUMNS["bookings"],
    "vehicle_events": REQUIRED_COLUMNS["vehicle_events"],
    "demand": REQUIRED_COLUMNS["demand"],
}


def connect() -> PgConnection:
    settings = require_neon_settings(get_settings())
    try:
        return psycopg2.connect(settings.sync_database_url, connect_timeout=30)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to connect to Neon: {redact_db_error(exc)}") from None


def table_counts(conn: PgConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in LOAD_ORDER:
            cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 — fixed identifiers
            counts[table] = int(cur.fetchone()[0])
    return counts


def truncate_all(conn: PgConnection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE "
            + ", ".join(TRUNCATE_ORDER)
            + " RESTART IDENTITY CASCADE"
        )
    conn.commit()


def bulk_insert(
    conn: PgConnection,
    table: str,
    rows: Iterable[tuple[Any, ...]],
) -> tuple[int, int, list[str]]:
    """Insert rows in batches. Returns (inserted, rejected, errors)."""
    cols = COLUMNS[table]
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    inserted = 0
    rejected = 0
    errors: list[str] = []
    batch: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal inserted, rejected, batch
        if not batch:
            return
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur, sql, batch, page_size=BATCH_SIZE
                )
            conn.commit()
            inserted += len(batch)
        except Exception:  # noqa: BLE001
            conn.rollback()
            # Isolate bad records without failing the whole load silently
            for row in batch:
                try:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, sql, [row])
                    conn.commit()
                    inserted += 1
                except Exception as row_exc:  # noqa: BLE001
                    conn.rollback()
                    rejected += 1
                    errors.append(f"{table}: {redact_db_error(row_exc)}")
        batch = []

    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            flush()
    flush()
    return inserted, rejected, errors


def load_table(conn: PgConnection, table: str) -> tuple[int, int, list[str]]:
    transform = TRANSFORMS[table]
    print(f"Loading {table}...")
    rows = (transform(r) for r in iter_csv(table))
    inserted, rejected, errors = bulk_insert(conn, table, rows)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        count = int(cur.fetchone()[0])
    print(f"Loaded {count:,} rows.")
    return count, rejected, errors


def run_import(force: bool = False) -> int:
    started = time.perf_counter()
    require_neon_settings()
    try:
        validate_dataset()
    except ValidationError as exc:
        print(f"Validation failed:\n{exc}", file=sys.stderr)
        return 1

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    summary_inserted: dict[str, int] = {}
    total_rejected = 0
    all_errors: list[str] = []

    try:
        counts = table_counts(conn)
        nonempty = {t: c for t, c in counts.items() if c > 0}
        if nonempty and not force:
            print(
                "Database already contains data: "
                + ", ".join(f"{t}={c:,}" for t, c in nonempty.items())
            )
            print("Re-run with --force to truncate and reload (destructive).")
            print("Import skipped (safe no-op).")
            return 0

        if force and nonempty:
            print("Truncating existing tables (--force)…")
            truncate_all(conn)

        for table in LOAD_ORDER:
            count, rejected, errors = load_table(conn, table)
            summary_inserted[table] = count
            total_rejected += rejected
            all_errors.extend(errors)
            if rejected:
                print(f"  WARNING: {rejected} rejected rows for {table}")

        elapsed = time.perf_counter() - started
        print("\nIMPORT SUMMARY")
        print("─" * 40)
        for table, count in summary_inserted.items():
            print(f"  {table:<16} {count:>10,} rows")
        print(f"  {'rejected':<16} {total_rejected:>10,}")
        print(f"  {'elapsed_sec':<16} {elapsed:>10.1f}")
        if all_errors:
            print("Errors:")
            for err in all_errors[:20]:
                print(f"  - {err}")
            return 1
        print("Import complete.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Import failed: {redact_db_error(exc)}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import UK taxi CSVs into Neon")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Truncate all taxi tables and reload from CSV",
    )
    args = parser.parse_args()
    raise SystemExit(run_import(force=args.force))


if __name__ == "__main__":
    main()
