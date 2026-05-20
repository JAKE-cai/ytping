import re
from typing import Literal, Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..database import DB_PATH

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
ScopeType = Literal["all", "group", "tag", "ids"]
ActionType = Literal["enable", "disable"]


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    scope_type: ScopeType
    scope_value: str = Field("", max_length=512)
    action: ActionType
    time_hhmm: str = Field(..., description="Local time HH:MM (24h)")
    weekdays: str = Field(
        "0,1,2,3,4,5,6",
        description="Weekdays 0=Mon..6=Sun, comma-separated",
    )
    enabled: bool = True

    @field_validator("time_hhmm")
    @classmethod
    def _time(cls, v: str) -> str:
        v = v.strip()
        if not _TIME_RE.match(v):
            raise ValueError("时间格式须为 HH:MM（24 小时制）")
        return v

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: str) -> str:
        parts = [p.strip() for p in (v or "").split(",") if p.strip()]
        for p in parts:
            if not p.isdigit() or int(p) < 0 or int(p) > 6:
                raise ValueError("weekdays 须为 0-6（0=周一）的逗号分隔列表")
        return ",".join(parts) if parts else "0,1,2,3,4,5,6"


class ScheduleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    scope_type: Optional[ScopeType] = None
    scope_value: Optional[str] = Field(None, max_length=512)
    action: Optional[ActionType] = None
    time_hhmm: Optional[str] = None
    weekdays: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("time_hhmm")
    @classmethod
    def _time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _TIME_RE.match(v):
            raise ValueError("时间格式须为 HH:MM（24 小时制）")
        return v


@router.get("")
async def list_schedules():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM target_schedules ORDER BY time_hhmm, id"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["enabled"] = bool(r["enabled"])
    return rows


@router.post("", status_code=201)
async def create_schedule(body: ScheduleCreate):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO target_schedules
                (name, scope_type, scope_value, action, time_hhmm, weekdays, enabled)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                body.name,
                body.scope_type,
                body.scope_value,
                body.action,
                body.time_hhmm,
                body.weekdays,
                1 if body.enabled else 0,
            ),
        )
        await db.commit()
        sid = cur.lastrowid
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM target_schedules WHERE id=?", (sid,)
        ) as cur2:
            row = dict(await cur2.fetchone())
    row["enabled"] = bool(row["enabled"])
    return row


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: int, body: ScheduleUpdate):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM target_schedules WHERE id=?", (schedule_id,)
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Schedule not found")

        updates: dict = {}
        if body.name is not None:
            updates["name"] = body.name
        if body.scope_type is not None:
            updates["scope_type"] = body.scope_type
        if body.scope_value is not None:
            updates["scope_value"] = body.scope_value
        if body.action is not None:
            updates["action"] = body.action
        if body.time_hhmm is not None:
            updates["time_hhmm"] = body.time_hhmm
        if body.weekdays is not None:
            updates["weekdays"] = body.weekdays
        if body.enabled is not None:
            updates["enabled"] = 1 if body.enabled else 0

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            await db.execute(
                f"UPDATE target_schedules SET {set_clause} WHERE id=?",
                (*updates.values(), schedule_id),
            )
            await db.commit()

        async with db.execute(
            "SELECT * FROM target_schedules WHERE id=?", (schedule_id,)
        ) as cur:
            row = dict(await cur.fetchone())
    row["enabled"] = bool(row["enabled"])
    return row


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM target_schedules WHERE id=?", (schedule_id,)
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Schedule not found")
        await db.execute("DELETE FROM target_schedules WHERE id=?", (schedule_id,))
        await db.commit()
    return {"ok": True}
