"""CSV → Neon import for synthetic vehicle / pricing datasets.

Usage (from backend/):
  python -m app.db.import_pricing_data
  python -m app.db.import_pricing_data --force   # truncate pricing tables + reload
  python -m app.db.import_pricing_data --validate-only
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection
from sqlalchemy import create_engine

from app.config import PROJECT_ROOT
from app.db.models import Base, PRICING_TABLE_ORDER
from app.db.session import redact_db_error, require_neon_settings

DATASET_DIR = PROJECT_ROOT / "uk_taxi_dataset"

REQUIRED_FILES = {
    "fare_rules": "fare_rules.csv",
    "vehicle_classes": "vehicle_classes.csv",
    "vehicle_catalog": "vehicle_catalog.csv",
    "city_modifiers": "city_modifiers.csv",
    "peak_rules": "peak_rules.csv",
    "surge_rules": "surge_rules.csv",
    "vehicle_selection_rules": "vehicle_selection_rules.csv",
    "pricing_config": "pricing_config.csv",
    "pricing_test_cases": "pricing_test_cases.csv",
}

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "fare_rules": (
        "pricing_tier",
        "base_fare_gbp",
        "included_distance_miles",
        "per_mile_gbp",
        "per_minute_gbp",
    ),
    "vehicle_classes": (
        "vehicle_class_id",
        "display_name",
        "min_passengers",
        "max_passengers",
        "luggage_capacity",
        "pricing_tier",
    ),
    "vehicle_catalog": (
        "vehicle_id",
        "vehicle_class_id",
        "display_name",
        "make",
        "model",
        "city",
        "passenger_capacity",
        "luggage_capacity",
        "wheelchair_accessible",
    ),
    "city_modifiers": ("city", "city_multiplier"),
    "peak_rules": ("rule_id", "start_hour", "end_hour", "multiplier"),
    "surge_rules": (
        "state",
        "multiplier",
        "supply_ratio_threshold",
        "min_surge",
        "max_surge",
    ),
    "vehicle_selection_rules": (
        "min_passengers",
        "max_passengers",
        "vehicle_class_id",
        "display_name",
    ),
    "pricing_config": ("key", "value", "description"),
    "pricing_test_cases": (
        "test_id",
        "pickup",
        "destination",
        "passengers",
        "expected_vehicle_class",
    ),
}

# Child → parent truncate order (reverse of FK-safe load order)
TRUNCATE_ORDER = tuple(reversed(PRICING_TABLE_ORDER))

BATCH_SIZE = 2_000


class ValidationError(Exception):
    pass


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes", "y", "t"}:
        return True
    if v in {"false", "0", "no", "n", "f"}:
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def _parse_int(value: str) -> int:
    return int(value.strip())


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


def validate_pricing_dataset() -> dict[str, Any]:
    """Validate columns, PKs, FKs. Raises ValidationError if any row is invalid."""
    print("Validating pricing CSV files…")
    ensure_files_exist()
    for name in REQUIRED_FILES:
        ensure_columns(name)

    errors: list[str] = []
    refs: dict[str, Any] = {}

    # fare_rules
    tiers: set[str] = set()
    for i, row in enumerate(iter_csv("fare_rules"), start=2):
        try:
            tier = row["pricing_tier"].strip()
            if not tier:
                raise ValueError("empty pricing_tier")
            if tier in tiers:
                raise ValueError(f"duplicate pricing_tier {tier}")
            tiers.add(tier)
            _parse_decimal(row["base_fare_gbp"])
            _parse_decimal(row["included_distance_miles"])
            _parse_decimal(row["per_mile_gbp"])
            _parse_decimal(row["per_minute_gbp"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fare_rules.csv:{i}: {exc}")
    refs["pricing_tiers"] = tiers
    print(f"  fare_rules: {len(tiers):,} rows validated")

    # vehicle_classes
    class_ids: set[str] = set()
    class_tier: dict[str, str] = {}
    for i, row in enumerate(iter_csv("vehicle_classes"), start=2):
        try:
            cid = row["vehicle_class_id"].strip()
            if not cid:
                raise ValueError("empty vehicle_class_id")
            if cid in class_ids:
                raise ValueError(f"duplicate vehicle_class_id {cid}")
            class_ids.add(cid)
            tier = row["pricing_tier"].strip()
            if tier not in tiers:
                raise ValueError(f"FK pricing_tier not found: {tier}")
            class_tier[cid] = tier
            mn = _parse_int(row["min_passengers"])
            mx = _parse_int(row["max_passengers"])
            if mn < 1 or mx < mn:
                raise ValueError(f"invalid passenger range {mn}-{mx}")
            _parse_int(row["luggage_capacity"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"vehicle_classes.csv:{i}: {exc}")
    refs["class_ids"] = class_ids
    refs["class_tier"] = class_tier
    print(f"  vehicle_classes: {len(class_ids):,} rows validated")

    # vehicle_catalog
    vehicle_ids: set[str] = set()
    for i, row in enumerate(iter_csv("vehicle_catalog"), start=2):
        try:
            vid = row["vehicle_id"].strip()
            if not vid:
                raise ValueError("empty vehicle_id")
            if vid in vehicle_ids:
                raise ValueError(f"duplicate vehicle_id {vid}")
            vehicle_ids.add(vid)
            cid = row["vehicle_class_id"].strip()
            if cid not in class_ids:
                raise ValueError(f"FK vehicle_class_id not found: {cid}")
            _parse_int(row["passenger_capacity"])
            _parse_int(row["luggage_capacity"])
            _parse_bool(row["wheelchair_accessible"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"vehicle_catalog.csv:{i}: {exc}")
            if len(errors) > 80:
                break
    refs["vehicle_ids"] = vehicle_ids
    print(f"  vehicle_catalog: {len(vehicle_ids):,} rows validated")

    # city_modifiers
    cities: set[str] = set()
    for i, row in enumerate(iter_csv("city_modifiers"), start=2):
        try:
            city = row["city"].strip()
            if not city:
                raise ValueError("empty city")
            if city in cities:
                raise ValueError(f"duplicate city {city}")
            cities.add(city)
            _parse_decimal(row["city_multiplier"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"city_modifiers.csv:{i}: {exc}")
    print(f"  city_modifiers: {len(cities):,} rows validated")

    # peak_rules
    peak_ids: set[str] = set()
    for i, row in enumerate(iter_csv("peak_rules"), start=2):
        try:
            rid = row["rule_id"].strip()
            if rid in peak_ids:
                raise ValueError(f"duplicate rule_id {rid}")
            peak_ids.add(rid)
            start = _parse_int(row["start_hour"])
            end = _parse_int(row["end_hour"])
            if not (0 <= start <= 23 and 0 <= end <= 23):
                raise ValueError(f"hours out of range: {start}-{end}")
            _parse_decimal(row["multiplier"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"peak_rules.csv:{i}: {exc}")
    print(f"  peak_rules: {len(peak_ids):,} rows validated")

    # surge_rules
    states: set[str] = set()
    for i, row in enumerate(iter_csv("surge_rules"), start=2):
        try:
            state = row["state"].strip()
            if state in states:
                raise ValueError(f"duplicate state {state}")
            states.add(state)
            _parse_decimal(row["multiplier"])
            _parse_decimal(row["supply_ratio_threshold"])
            _parse_decimal(row["min_surge"])
            _parse_decimal(row["max_surge"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"surge_rules.csv:{i}: {exc}")
    print(f"  surge_rules: {len(states):,} rows validated")

    # vehicle_selection_rules
    sel_keys: set[tuple[int, int, str]] = set()
    for i, row in enumerate(iter_csv("vehicle_selection_rules"), start=2):
        try:
            mn = _parse_int(row["min_passengers"])
            mx = _parse_int(row["max_passengers"])
            cid = row["vehicle_class_id"].strip()
            key = (mn, mx, cid)
            if key in sel_keys:
                raise ValueError(f"duplicate selection key {key}")
            sel_keys.add(key)
            if cid not in class_ids:
                raise ValueError(f"FK vehicle_class_id not found: {cid}")
            if mx < mn:
                raise ValueError(f"invalid passenger range {mn}-{mx}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"vehicle_selection_rules.csv:{i}: {exc}")
    print(f"  vehicle_selection_rules: {len(sel_keys):,} rows validated")

    # pricing_config
    keys: set[str] = set()
    for i, row in enumerate(iter_csv("pricing_config"), start=2):
        try:
            key = row["key"].strip()
            if not key:
                raise ValueError("empty key")
            if key in keys:
                raise ValueError(f"duplicate key {key}")
            keys.add(key)
            if not row["value"].strip():
                raise ValueError("empty value")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pricing_config.csv:{i}: {exc}")
    print(f"  pricing_config: {len(keys):,} rows validated")

    # pricing_test_cases
    test_ids: set[str] = set()
    for i, row in enumerate(iter_csv("pricing_test_cases"), start=2):
        try:
            tid = row["test_id"].strip()
            if tid in test_ids:
                raise ValueError(f"duplicate test_id {tid}")
            test_ids.add(tid)
            _parse_int(row["passengers"])
            expected = row["expected_vehicle_class"].strip()
            if expected not in class_ids:
                raise ValueError(f"expected_vehicle_class not in classes: {expected}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pricing_test_cases.csv:{i}: {exc}")
    print(f"  pricing_test_cases: {len(test_ids):,} rows validated")

    if errors:
        shown = "\n".join(errors[:40])
        more = f"\n… and {len(errors) - 40} more" if len(errors) > 40 else ""
        raise ValidationError(f"Validation failed ({len(errors)} errors):\n{shown}{more}")

    print("Validation              OK")
    return refs


def ensure_pricing_schema() -> None:
    """CREATE TABLE IF NOT EXISTS for pricing tables (never DROP)."""
    settings = require_neon_settings()
    engine = create_engine(settings.sync_database_url)
    try:
        tables = [Base.metadata.tables[name] for name in PRICING_TABLE_ORDER]
        Base.metadata.create_all(engine, tables=tables)
    finally:
        engine.dispose()
    print("Schema                  OK (pricing tables ensured)")


def connect() -> PgConnection:
    settings = require_neon_settings()
    return psycopg2.connect(settings.sync_database_url, connect_timeout=30)


def truncate_pricing(conn: PgConnection) -> None:
    with conn.cursor() as cur:
        for table in TRUNCATE_ORDER:
            cur.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
    conn.commit()
    print("Truncate pricing tables OK")


def _copy_rows(conn: PgConnection, table: str, columns: list[str], rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            psycopg2.extras.execute_values(
                cur,
                f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES %s',
                batch,
                page_size=BATCH_SIZE,
            )
    return len(rows)


def load_all(conn: PgConnection, refs: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    class_tier: dict[str, str] = refs["class_tier"]

    # fare_rules
    rows = []
    for row in iter_csv("fare_rules"):
        rows.append(
            (
                row["pricing_tier"].strip(),
                _parse_decimal(row["base_fare_gbp"]),
                _parse_decimal(row["included_distance_miles"]),
                _parse_decimal(row["per_mile_gbp"]),
                _parse_decimal(row["per_minute_gbp"]),
            )
        )
    counts["fare_rules"] = _copy_rows(
        conn,
        "fare_rules",
        [
            "pricing_tier",
            "base_fare_gbp",
            "included_distance_miles",
            "per_mile_gbp",
            "per_minute_gbp",
        ],
        rows,
    )

    # vehicle_classes
    rows = []
    for row in iter_csv("vehicle_classes"):
        cid = row["vehicle_class_id"].strip()
        rows.append(
            (
                cid,
                row["display_name"].strip(),
                _parse_int(row["min_passengers"]),
                _parse_int(row["max_passengers"]),
                _parse_int(row["luggage_capacity"]),
                row["pricing_tier"].strip(),
                cid.upper() == "ACCESSIBLE",
            )
        )
    counts["vehicle_classes"] = _copy_rows(
        conn,
        "vehicle_classes",
        [
            "vehicle_class_id",
            "display_name",
            "min_passengers",
            "max_passengers",
            "luggage_capacity",
            "pricing_tier",
            "wheelchair_accessible",
        ],
        rows,
    )

    # vehicle_catalog
    rows = []
    for row in iter_csv("vehicle_catalog"):
        cid = row["vehicle_class_id"].strip()
        rows.append(
            (
                row["vehicle_id"].strip(),
                cid,
                row["display_name"].strip(),
                row["make"].strip(),
                row["model"].strip(),
                row["city"].strip(),
                _parse_int(row["passenger_capacity"]),
                _parse_int(row["luggage_capacity"]),
                _parse_bool(row["wheelchair_accessible"]),
                class_tier[cid],
            )
        )
    counts["vehicle_catalog"] = _copy_rows(
        conn,
        "vehicle_catalog",
        [
            "vehicle_id",
            "vehicle_class_id",
            "display_name",
            "make",
            "model",
            "city",
            "passenger_capacity",
            "luggage_capacity",
            "wheelchair_accessible",
            "pricing_tier",
        ],
        rows,
    )

    # city_modifiers
    rows = [
        (row["city"].strip(), _parse_decimal(row["city_multiplier"]))
        for row in iter_csv("city_modifiers")
    ]
    counts["city_modifiers"] = _copy_rows(
        conn, "city_modifiers", ["city", "city_multiplier"], rows
    )

    # peak_rules
    rows = [
        (
            row["rule_id"].strip(),
            _parse_int(row["start_hour"]),
            _parse_int(row["end_hour"]),
            _parse_decimal(row["multiplier"]),
        )
        for row in iter_csv("peak_rules")
    ]
    counts["peak_rules"] = _copy_rows(
        conn,
        "peak_rules",
        ["rule_id", "start_hour", "end_hour", "multiplier"],
        rows,
    )

    # surge_rules
    rows = [
        (
            row["state"].strip(),
            _parse_decimal(row["multiplier"]),
            _parse_decimal(row["supply_ratio_threshold"]),
            _parse_decimal(row["min_surge"]),
            _parse_decimal(row["max_surge"]),
        )
        for row in iter_csv("surge_rules")
    ]
    counts["surge_rules"] = _copy_rows(
        conn,
        "surge_rules",
        [
            "state",
            "multiplier",
            "supply_ratio_threshold",
            "min_surge",
            "max_surge",
        ],
        rows,
    )

    # vehicle_selection_rules (priority = CSV order)
    rows = []
    for priority, row in enumerate(iter_csv("vehicle_selection_rules"), start=1):
        rows.append(
            (
                _parse_int(row["min_passengers"]),
                _parse_int(row["max_passengers"]),
                row["vehicle_class_id"].strip(),
                row["display_name"].strip(),
                priority,
            )
        )
    counts["vehicle_selection_rules"] = _copy_rows(
        conn,
        "vehicle_selection_rules",
        [
            "min_passengers",
            "max_passengers",
            "vehicle_class_id",
            "display_name",
            "priority",
        ],
        rows,
    )

    # pricing_config
    rows = [
        (row["key"].strip(), row["value"].strip(), row["description"].strip())
        for row in iter_csv("pricing_config")
    ]
    counts["pricing_config"] = _copy_rows(
        conn, "pricing_config", ["key", "value", "description"], rows
    )

    # pricing_test_cases
    rows = [
        (
            row["test_id"].strip(),
            row["pickup"].strip(),
            row["destination"].strip(),
            _parse_int(row["passengers"]),
            row["expected_vehicle_class"].strip(),
        )
        for row in iter_csv("pricing_test_cases")
    ]
    counts["pricing_test_cases"] = _copy_rows(
        conn,
        "pricing_test_cases",
        [
            "test_id",
            "pickup",
            "destination",
            "passengers",
            "expected_vehicle_class",
        ],
        rows,
    )

    conn.commit()
    return counts


def table_has_rows(conn: PgConnection, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(f'SELECT EXISTS (SELECT 1 FROM "{table}" LIMIT 1)')
        return bool(cur.fetchone()[0])


def run_import(*, force: bool, validate_only: bool) -> int:
    started = time.perf_counter()
    try:
        refs = validate_pricing_dataset()
        if validate_only:
            print("Validate-only complete.")
            return 0

        ensure_pricing_schema()
        conn = connect()
        try:
            if any(table_has_rows(conn, t) for t in PRICING_TABLE_ORDER):
                if not force:
                    print(
                        "Pricing tables already contain data. "
                        "Re-run with --force to truncate and reload.",
                        file=sys.stderr,
                    )
                    return 1
                truncate_pricing(conn)

            print("Loading pricing data…")
            counts = load_all(conn, refs)
            print("Row counts:")
            for name in PRICING_TABLE_ORDER:
                print(f"  {name}: {counts.get(name, 0):,}")
            elapsed = time.perf_counter() - started
            print(f"Import complete in {elapsed:.1f}s")
            return 0
        finally:
            conn.close()
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Import failed: {redact_db_error(exc)}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Import synthetic pricing datasets into Neon")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Truncate pricing tables and reload (does not DROP tables)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate CSV files without writing to the database",
    )
    args = parser.parse_args()
    raise SystemExit(run_import(force=args.force, validate_only=args.validate_only))


if __name__ == "__main__":
    main()
