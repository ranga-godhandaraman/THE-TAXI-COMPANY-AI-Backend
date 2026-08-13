"""Deterministic schema initialization for Neon PostgreSQL.

Usage (from backend/):
  python -m app.db.init
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db.models import Base, EXPECTED_INDEXES, TABLE_ORDER
from app.db.session import dispose_engine, get_engine, ping_postgres, redact_db_error


async def create_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def validate_schema_exists() -> list[str]:
    """Return missing table names (empty if all present)."""
    engine = get_engine()
    missing: list[str] = []
    async with engine.connect() as conn:
        for table in TABLE_ORDER:
            result = await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :table
                    """
                ),
                {"table": table},
            )
            if result.scalar() is None:
                missing.append(table)
    return missing


async def validate_indexes_exist() -> list[str]:
    engine = get_engine()
    missing: list[str] = []
    async with engine.connect() as conn:
        for index_name in EXPECTED_INDEXES:
            result = await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_indexes
                    WHERE schemaname = 'public' AND indexname = :name
                    """
                ),
                {"name": index_name},
            )
            if result.scalar() is None:
                missing.append(index_name)
    return missing


async def run_init() -> int:
    print("Initializing Neon PostgreSQL schema…")
    try:
        await ping_postgres()
        print("Connection          OK")
        await create_schema()
        print("CREATE TABLE        OK (IF NOT EXISTS via SQLAlchemy metadata)")
        missing_tables = await validate_schema_exists()
        if missing_tables:
            print(f"Schema validation   FAIL — missing tables: {missing_tables}")
            return 1
        print(f"Tables              OK ({', '.join(TABLE_ORDER)})")
        missing_indexes = await validate_indexes_exist()
        if missing_indexes:
            print(f"Indexes             FAIL — missing: {missing_indexes}")
            return 1
        print(f"Indexes             OK ({len(EXPECTED_INDEXES)} expected)")
        print("Initialization complete.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Initialization failed: {redact_db_error(exc)}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()


def main() -> None:
    raise SystemExit(asyncio.run(run_init()))


if __name__ == "__main__":
    main()
