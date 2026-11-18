"""Permanent file storage: Files\\<Automation>\\<Year>\\<MM-Month>\\<YYYY-MM-DD>\\

No Market level (one output often covers many markets); markets are tags in SQLite.
Reports are never deleted automatically.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, asc, desc, func, select, text
from sqlalchemy.orm import Session

from backend.models.tables import GeneratedFile

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_name(name: str, fallback: str = "file") -> str:
    cleaned = _BAD.sub("_", name).strip().strip(".")
    return (cleaned or fallback)[:100]


def target_dir(files_root: Path, folder: str, when: datetime) -> Path:
    return (files_root / sanitize_name(folder, "Other") / f"{when:%Y}"
            / f"{when.month:02d}-{MONTHS[when.month - 1]}" / f"{when:%Y-%m-%d}")


def place_file(files_root: Path, folder: str, src: Path, display_name: str | None,
               task_id: int, when: datetime) -> Path:
    """Move ``src`` into the permanent tree with a unique, descriptive name."""
    dest_dir = target_dir(files_root, folder, when)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_name(display_name or src.stem)
    ext = src.suffix.lower()
    base = f"{stem}_{when:%Y%m%d_%H%M%S}_T{task_id}"
    dest = dest_dir / f"{base}{ext}"
    n = 2
    while dest.exists():
        dest = dest_dir / f"{base}_{n}{ext}"
        n += 1
    shutil.move(str(src), str(dest))
    return dest


class UnsafeFileError(ValueError):
    pass


def resolve_inside(scratch: Path, raw: str) -> Path:
    """An adapter may only register files that live inside the task scratch folder."""
    p = Path(raw)
    if not p.is_absolute():
        p = scratch / p
    rp, rs = p.resolve(), scratch.resolve()
    if not rp.is_relative_to(rs):
        raise UnsafeFileError(f"File is outside the task folder and was not registered: {raw}")
    if not rp.is_file():
        raise UnsafeFileError(f"Output file does not exist: {raw}")
    return rp


def list_files(
    s: Session, *, automation_id: str | None = None, market: str | None = None,
    q: str | None = None, ext: str | None = None, date_from: str | None = None,
    date_to: str | None = None, task_id: int | None = None, sort: str = "created",
    order: str = "desc", limit: int = 100, offset: int = 0,
) -> tuple[list[GeneratedFile], int]:
    conds: list[Any] = []
    if automation_id:
        conds.append(GeneratedFile.automation_id == automation_id)
    if task_id is not None:
        conds.append(GeneratedFile.task_id == task_id)
    if ext:
        conds.append(GeneratedFile.ext == ext.lower().lstrip("."))
    if q:
        conds.append(GeneratedFile.name.ilike(f"%{q}%"))
    if market:
        conds.append(text("EXISTS (SELECT 1 FROM json_each(generated_files.markets) "
                          "WHERE lower(json_each.value) = lower(:mk))").bindparams(mk=market))
    if date_from:
        conds.append(GeneratedFile.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        conds.append(GeneratedFile.created_at < datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59))
    where = and_(*conds) if conds else None
    col = {"created": GeneratedFile.created_at, "name": GeneratedFile.name,
           "size": GeneratedFile.size}.get(sort, GeneratedFile.created_at)
    stmt = select(GeneratedFile)
    cnt = select(func.count()).select_from(GeneratedFile)
    if where is not None:
        stmt, cnt = stmt.where(where), cnt.where(where)
    stmt = stmt.order_by((asc if order == "asc" else desc)(col), desc(GeneratedFile.id)).limit(limit).offset(offset)
    return list(s.scalars(stmt)), int(s.scalar(cnt) or 0)
