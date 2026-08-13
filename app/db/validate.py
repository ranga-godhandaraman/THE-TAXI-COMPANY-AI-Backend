"""Human-readable Neon PostgreSQL validation.

Usage (from backend/):
  python -m app.db.validate
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db.models import EXPECTED_INDEXES, TABLE_ORDER
from app.db.session import dispose_engine, get_engine, ping_postgres, redact_db_error

EXPECTED_MIN_ROWS = {
    "zones": 1,
    "drivers": 1,
    "vehicles": 1,
    "fares": 1,
    "trips": 1,
    "bookings": 1,
    "vehicle_events": 1,
    "demand": 1,
}

FK_CHECKS = (
    ("vehicles", "driver_id", "drivers", "driver_id"),
    ("vehicles", "zone_id", "zones", "zone_id"),
    ("trips", "vehicle_id", "vehicles", "vehicle_id"),
    ("trips", "driver_id", "drivers", "driver_id"),
    ("trips", "pickup_zone_id", "zones", "zone_id"),
    ("trips", "dropoff_zone_id", "zones", "zone_id"),
    ("bookings", "trip_id", "trips", "trip_id"),
    ("vehicle_events", "vehicle_id", "vehicles", "vehicle_id"),
    ("vehicle_events", "driver_id", "drivers", "driver_id"),
    ("vehicle_events", "zone_id", "zones", "zone_id"),
    ("demand", "zone_id", "zones", "zone_id"),
)


def _line(label: str, status: str, detail: str = "") -> str:
    base = f"{label:<18} {status:<6}"
    return f"{base} {detail}".rstrip()


async def run_validate() -> int:
    print("DATABASE VALIDATION")
    print("─" * 44)
    failures = 0

    try:
        await ping_postgres()
        print(_line("Connection", "PASS"))
    except Exception as exc:  # noqa: BLE001
        print(_line("Connection", "FAIL", redact_db_error(exc)))
        print("─" * 44)
        print(_line("Overall", "FAIL"))
        await dispose_engine()
        return 1

    engine = get_engine()
    row_counts: dict[str, int] = {}

    async with engine.connect() as conn:
        for table in TABLE_ORDER:
            exists = await conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema='public' AND table_name=:t
                    """
                ),
                {"t": table},
            )
            if exists.scalar() is None:
                print(_line(table, "FAIL", "table missing"))
                failures += 1
                continue

            # column sanity: at least 1 column present
            cols = await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:t
                    """
                ),
                {"t": table},
            )
            col_count = int(cols.scalar_one())
            count_result = await conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
            n = int(count_result.scalar_one())
            row_counts[table] = n
            if n < EXPECTED_MIN_ROWS[table]:
                print(_line(table, "FAIL", f"{n:,} rows (empty)"))
                failures += 1
            elif col_count < 2:
                print(_line(table, "FAIL", "unexpected columns"))
                failures += 1
            else:
                print(_line(table, "PASS", f"{n:,} rows"))

        # PK presence via information_schema
        pk_ok = True
        for table in TABLE_ORDER:
            pk = await conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.table_constraints
                    WHERE table_schema='public'
                      AND table_name=:t
                      AND constraint_type='PRIMARY KEY'
                    """
                ),
                {"t": table},
            )
            if int(pk.scalar_one()) < 1:
                pk_ok = False
                print(_line(f"PK {table}", "FAIL", "missing primary key"))
                failures += 1
        if pk_ok:
            print(_line("Primary Keys", "PASS"))

        # Foreign key orphan checks
        fk_ok = True
        for child, child_col, parent, parent_col in FK_CHECKS:
            orphan = await conn.execute(
                text(
                    f"""
                    SELECT COUNT(*) FROM {child} c
                    LEFT JOIN {parent} p ON c.{child_col} = p.{parent_col}
                    WHERE p.{parent_col} IS NULL
                    """
                )
            )
            n = int(orphan.scalar_one())
            if n:
                fk_ok = False
                failures += 1
                print(_line("Foreign Keys", "FAIL", f"{child}.{child_col} → {n} orphans"))
        if fk_ok:
            print(_line("Foreign Keys", "PASS"))

        # Indexes
        missing_idx: list[str] = []
        for name in EXPECTED_INDEXES:
            found = await conn.execute(
                text(
                    """
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname='public' AND indexname=:n
                    """
                ),
                {"n": name},
            )
            if found.scalar() is None:
                missing_idx.append(name)
        if missing_idx:
            print(_line("Indexes", "FAIL", f"missing {len(missing_idx)}"))
            failures += 1
        else:
            print(_line("Indexes", "PASS"))

    print("─" * 44)
    overall = "PASS" if failures == 0 else "FAIL"
    print(_line("Overall", overall))
    await dispose_engine()
    return 0 if failures == 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(run_validate()))


if __name__ == "__main__":
    main()
