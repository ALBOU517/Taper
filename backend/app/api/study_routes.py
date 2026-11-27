"""Study planner endpoints: deadlines, revision blocks, re-planning."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .. import study
from ..auth import current_user
from ..db import Deadline, StudyBlock, User, get_db
from ..engine import study as S

router = APIRouter(prefix="/api/study")
HM = r"^([01]\d|2[0-3]):[0-5]\d$"


class DeadlineIn(BaseModel):
    course: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    kind: str = Field("exam", pattern="^(exam|assignment|quiz)$")
    due_date: date
    due_time: str = Field("23:59", pattern=HM)
    end_time: Optional[str] = Field(None, pattern=HM)
    weight: Optional[float] = Field(None, ge=0, le=100)
    hours: Optional[float] = Field(None, ge=0, le=200)
    location: Optional[str] = Field(None, max_length=120)
    note: Optional[str] = None
    done: bool = False


class ImportIn(BaseModel):
    text: str = Field(max_length=20000)
    replace: bool = False


class BlockIn(BaseModel):
    deadline_id: int
    day: date
    start_time: str = Field(pattern=HM)
    minutes: int = Field(ge=15, le=300)


class BlockPatch(BaseModel):
    status: Optional[str] = Field(None, pattern="^(planned|done|missed)$")
    day: Optional[date] = None
    start_time: Optional[str] = Field(None, pattern=HM)
    minutes: Optional[int] = Field(None, ge=15, le=300)


def _own(db: DB, model, oid: int, user: User):
    x = db.get(model, oid)
    if not x or x.user_id != user.id:
        raise HTTPException(404, "Not found")
    return x


@router.get("")
def overview(user: User = Depends(current_user), db: DB = Depends(get_db)):
    return study.overview(db, user)


@router.get("/blocks")
def blocks(start: date, end: date, user: User = Depends(current_user), db: DB = Depends(get_db)):
    names = {d.id: d for d in db.scalars(select(Deadline).where(Deadline.user_id == user.id))}
    return [study.block_dict(r, names.get(r.deadline_id)) for r in study.blocks_between(db, user, start, end)]


@router.post("/deadlines")
def add_deadline(body: DeadlineIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    d = Deadline(user_id=user.id, **body.model_dump())
    db.add(d)
    db.commit()
    return {"deadline": study.deadline_dict(d), "plan": study.replan(db, user)}


@router.put("/deadlines/{did}")
def edit_deadline(did: int, body: DeadlineIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    d = _own(db, Deadline, did, user)
    for k, v in body.model_dump().items():
        setattr(d, k, v)
    db.commit()
    return {"deadline": study.deadline_dict(d), "plan": study.replan(db, user)}


@router.delete("/deadlines/{did}")
def delete_deadline(did: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    d = _own(db, Deadline, did, user)
    for r in db.scalars(select(StudyBlock).where(StudyBlock.deadline_id == d.id)):
        db.delete(r)
    db.delete(d)
    db.commit()
    return {"plan": study.replan(db, user)}


@router.post("/import")
def import_deadlines(body: ImportIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    """Paste a list (one deadline per line). Lines matching an existing deadline (same course,
    title and date) update it instead of making a duplicate."""
    rows, errors = S.parse_lines(body.text, date.today().year)
    if errors and not rows:
        raise HTTPException(422, "; ".join(errors))
    if body.replace:
        for d in db.scalars(select(Deadline).where(Deadline.user_id == user.id, Deadline.due_date >= date.today())):
            db.delete(d)
        db.flush()
    existing = {(d.course.lower(), d.title.lower(), d.due_date): d
                for d in db.scalars(select(Deadline).where(Deadline.user_id == user.id))}
    added = updated = 0
    for r in rows:
        due = date.fromisoformat(r["due_date"])
        d = existing.get((r["course"].lower(), r["title"].lower(), due))
        if d:
            d.due_time, d.kind = r["due_time"], r["kind"]
            d.weight = r["weight"] if r["weight"] is not None else d.weight
            d.hours = r["hours"] if r["hours"] is not None else d.hours
            updated += 1
        else:
            db.add(Deadline(user_id=user.id, course=r["course"], title=r["title"], kind=r["kind"], due_date=due,
                            due_time=r["due_time"], weight=r["weight"], hours=r["hours"]))
            added += 1
    db.commit()
    return {"added": added, "updated": updated, "errors": errors, "plan": study.replan(db, user)}


@router.post("/plan")
def rebuild(user: User = Depends(current_user), db: DB = Depends(get_db)):
    return study.replan(db, user)


@router.post("/blocks")
def add_block(body: BlockIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    _own(db, Deadline, body.deadline_id, user)
    r = StudyBlock(user_id=user.id, source="manual", **body.model_dump())
    db.add(r)
    db.commit()
    return study.block_dict(r, db.get(Deadline, r.deadline_id))


@router.patch("/blocks/{bid}")
def edit_block(bid: int, body: BlockPatch, user: User = Depends(current_user), db: DB = Depends(get_db)):
    r = _own(db, StudyBlock, bid, user)
    patch = body.model_dump(exclude_none=True)
    if {"day", "start_time", "minutes"} & patch.keys():
        r.source = "manual"                # moved by hand: the planner leaves it where it is
    for k, v in patch.items():
        setattr(r, k, v)
    db.commit()
    return study.block_dict(r, db.get(Deadline, r.deadline_id))


@router.delete("/blocks/{bid}")
def delete_block(bid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    r = _own(db, StudyBlock, bid, user)
    db.delete(r)
    db.commit()
    return {"ok": True}
