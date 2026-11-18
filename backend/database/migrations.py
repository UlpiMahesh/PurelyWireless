"""Simple schema-version based migrations (no Alembic in v1).

To change the schema later: bump CURRENT_VERSION and add ``MIGRATIONS[new_version]``,
a function taking an Engine. The database file is copied to Backups\\ first.
"""
from __future__ import annotations

import logging
import shutil
from typing import Callable

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from backend.core.timeutil import utcnow
from backend.models.base import Base
from backend.models import tables  # noqa: F401  (register models)
from backend.models.tables import SchemaVersion

log = logging.getLogger("pw.db")

CURRENT_VERSION = 1
MIGRATIONS: dict[int, Callable[[Engine], None]] = {}   # e.g. {2: migrate_to_2}


class SchemaTooNewError(RuntimeError):
    pass


class SchemaMismatchError(RuntimeError):
    """The file claims to be the current version but its tables do not match this app."""


def verify_structure(engine: Engine, db_file) -> None:
    """The version number alone is not proof: check every expected column really exists."""
    insp = inspect(engine)
    problems = []
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            problems.append(f"missing table {table.name}")
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        missing = [c.name for c in table.columns if c.name not in have]
        if missing:
            problems.append(f"{table.name}: missing column(s) {', '.join(missing)}")
    if problems:
        raise SchemaMismatchError(
            f"The database file {db_file} does not match this version of the app "
            f"({'; '.join(problems)}). It was probably created by a different or older build. "
            "Close the app, rename or move that file, and start again (a new database is created automatically).")


def _current(engine: Engine) -> int | None:
    if not inspect(engine).has_table("schema_version"):
        return None
    with Session(engine) as s:
        rows = s.scalars(select(SchemaVersion.version)).all()
    return max(rows) if rows else None


def ensure_schema(engine: Engine, db_file, backups_dir) -> int:
    version = _current(engine)
    if version is None:
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            s.add(SchemaVersion(version=CURRENT_VERSION, applied_at=utcnow()))
            s.commit()
        log.info("created database schema v%s", CURRENT_VERSION)
        return CURRENT_VERSION

    if version > CURRENT_VERSION:
        raise SchemaTooNewError(
            f"Database schema v{version} was created by a newer version of the app "
            f"(this build supports v{CURRENT_VERSION}). Install the newer version."
        )

    if version < CURRENT_VERSION:
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().strftime("%Y%m%d_%H%M%S")
        backup = backups_dir / f"pre-migration_v{version}_{stamp}.db"
        engine.dispose()
        shutil.copy2(db_file, backup)
        log.info("backed up database to %s before migrating v%s -> v%s", backup, version, CURRENT_VERSION)
        for target in range(version + 1, CURRENT_VERSION + 1):
            MIGRATIONS[target](engine)
            with Session(engine) as s:
                s.add(SchemaVersion(version=target, applied_at=utcnow()))
                s.commit()
            log.info("migrated to schema v%s", target)
        Base.metadata.create_all(engine)   # creates any brand-new tables
        verify_structure(engine, db_file)
        return CURRENT_VERSION

    Base.metadata.create_all(engine)       # no-op for an up-to-date schema
    verify_structure(engine, db_file)
    return version
