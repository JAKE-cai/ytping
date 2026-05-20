"""
Daily schedule runner: enable/disable targets by scope at configured local times.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiosqlite

from .target_scope import batch_set_enabled, resolve_target_ids

logger = logging.getLogger(__name__)

# (schedule_id, YYYY-MM-DD HH:MM) keys already fired
_fired: set[str] = set()
_MAX_FIRED_CACHE = 5000


def _weekday_set(weekdays_csv: str) -> set[int]:
    """CSV of 0-6 where 0=Monday (datetime.weekday()). Empty => every day."""
    s = (weekdays_csv or "").strip()
    if not s:
        return set(range(7))
    out = set()
    for part in s.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            out.add(int(part))
    return out if out else set(range(7))


async def run_schedule_checker(db_path: str) -> None:
    """Poll every 30s; fire schedules when local time matches HH:MM."""
    global _fired
    while True:
        try:
            await _tick(db_path)
        except Exception as exc:
            logger.error("Schedule checker error: %s", exc)
        await asyncio.sleep(30)


async def _tick(db_path: str) -> None:
    now = datetime.now()
    hhmm = now.strftime("%H:%M")
    wd = now.weekday()
    fire_key_base = now.strftime("%Y-%m-%d ") + hhmm

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM target_schedules WHERE enabled=1"
        ) as cur:
            schedules = [dict(r) for r in await cur.fetchall()]

    for sch in schedules:
        if sch.get("time_hhmm") != hhmm:
            continue
        if wd not in _weekday_set(sch.get("weekdays", "")):
            continue
        fk = f"{sch['id']}:{fire_key_base}"
        if fk in _fired:
            continue

        enabled = sch["action"] == "enable"
        ids = await resolve_target_ids(
            db_path,
            sch["scope_type"],
            sch.get("scope_value") or "",
        )
        result = await batch_set_enabled(db_path, ids, enabled)
        _fired.add(fk)
        logger.info(
            "Schedule #%s '%s' fired: %s %d targets",
            sch["id"],
            sch.get("name"),
            sch["action"],
            result["updated"],
        )

    if len(_fired) > _MAX_FIRED_CACHE:
        _fired.clear()
