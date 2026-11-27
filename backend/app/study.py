"""
Study side of Taper: deadlines, revision blocks, and keeping them in step with the calendar
and the training plan. The placement itself lives in ``engine.study``.

Order of operations when the plan is rebuilt:
1. future auto blocks are cleared (done, missed and hand-placed blocks stay);
2. revision blocks are placed in the calendar's free time, keeping one slot per day big enough
   for that day's training;
3. training sessions get their start times again, now treating revision blocks as busy.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from . import life
from .db import Deadline, Session, StudyBlock, User
from .engine import calendar as C
from .engine import study as S

KIND_LABEL = {"exam": "Exam", "assignment": "Assignment", "quiz": "Quiz"}


def _hm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def tz(user: User) -> ZoneInfo:
    return ZoneInfo(life.settings(user)["schedule"]["tz"])


def due_at(d: Deadline, zone: ZoneInfo) -> datetime:
    return datetime.combine(d.due_date, _hm(d.due_time or "23:59"), zone)


def hours_needed(d: Deadline) -> float:
    return d.hours if d.hours is not None else S.default_hours(d.kind, d.weight or 0)


def blocks_between(db: DB, user: User, a: date, b: date, with_missed: bool = False) -> list[StudyBlock]:
    q = select(StudyBlock).where(StudyBlock.user_id == user.id, StudyBlock.day.between(a, b))
    if not with_missed:
        q = q.where(StudyBlock.status != "missed")
    return list(db.scalars(q.order_by(StudyBlock.day, StudyBlock.start_time)))


def busy_from_blocks(db: DB, user: User, a: date, b: date) -> list[C.Busy]:
    """Revision blocks as calendar busy time, so training is placed around them."""
    zone, out = tz(user), []
    titles = {d.id: f"Study: {d.course}" for d in db.scalars(select(Deadline).where(Deadline.user_id == user.id))}
    for r in blocks_between(db, user, a, b):
        s = datetime.combine(r.day, _hm(r.start_time), zone)
        out.append(C.Busy(s, s + timedelta(minutes=r.minutes), titles.get(r.deadline_id, "Study"), "study"))
    return out


# ------------------------------------------------------------------ views
def deadline_dict(d: Deadline, done_min: float = 0, planned_min: float = 0, today: Optional[date] = None) -> dict:
    today = today or date.today()
    need = hours_needed(d)
    return {"id": d.id, "course": d.course, "title": d.title, "kind": d.kind, "due_date": d.due_date.isoformat(),
            "due_time": d.due_time, "end_time": d.end_time, "weight": d.weight, "hours": d.hours,
            "hours_needed": need, "estimated": d.hours is None, "location": d.location, "note": d.note,
            "done": d.done, "days_left": (d.due_date - today).days,
            "done_min": round(done_min), "planned_min": round(planned_min),
            "short_min": max(0, round(need * 60 - done_min - planned_min))}


def block_dict(r: StudyBlock, d: Optional[Deadline]) -> dict:
    return {"id": r.id, "deadline_id": r.deadline_id, "day": r.day.isoformat(), "start_time": r.start_time,
            "minutes": r.minutes, "status": r.status, "source": r.source,
            "end_time": (datetime.combine(r.day, _hm(r.start_time)) + timedelta(minutes=r.minutes)).strftime("%H:%M"),
            "course": d.course if d else "", "title": f"{d.course} — {d.title}" if d else "Study",
            "kind": d.kind if d else None}


def overview(db: DB, user: User, today: Optional[date] = None) -> dict:
    today = today or date.today()
    deadlines = list(db.scalars(select(Deadline).where(Deadline.user_id == user.id)
                                .order_by(Deadline.due_date, Deadline.due_time)))
    by_id = {d.id: d for d in deadlines}
    done_min: dict[int, float] = {}
    planned_min: dict[int, float] = {}
    for r in db.scalars(select(StudyBlock).where(StudyBlock.user_id == user.id)):
        if r.status == "done":
            done_min[r.deadline_id] = done_min.get(r.deadline_id, 0) + r.minutes
        elif r.status == "planned" and r.day >= today:
            planned_min[r.deadline_id] = planned_min.get(r.deadline_id, 0) + r.minutes
    upcoming = [d for d in deadlines if d.due_date >= today and not d.done]
    past = [d for d in deadlines if d not in upcoming]
    horizon = max([d.due_date for d in upcoming], default=today)
    blocks = blocks_between(db, user, today, horizon)
    return {
        "today": today.isoformat(),
        "deadlines": [deadline_dict(d, done_min.get(d.id, 0), planned_min.get(d.id, 0), today) for d in upcoming],
        "past": [deadline_dict(d, done_min.get(d.id, 0), 0, today) for d in past[-20:]],
        "blocks": [block_dict(r, by_id.get(r.deadline_id)) for r in blocks],
        "settings": life.settings(user)["study"],
    }


# ------------------------------------------------------------------ planning
def _keywords(s: str) -> list[tuple[str, Optional[float]]]:
    """'RAMQ:4, bus' -> [('ramq', 4.0), ('bus', None)]: study during those events,
    optionally only the first N hours of each one."""
    out = []
    for k in (s or "").split(","):
        name, _, hours = k.strip().partition(":")
        if name.strip():
            try:
                h = float(hours.replace("h", "").replace(",", ".")) if hours.strip() else None
            except ValueError:
                h = None
            out.append((name.strip().lower(), h))
    return out


def replan(db: DB, user: User, now: Optional[datetime] = None) -> dict:
    """Rebuild the revision blocks from now on, then re-time training around them."""
    zone = tz(user)
    now = (now or datetime.now(zone)).astimezone(zone)
    today = now.date()
    st = life.settings(user)["study"]
    sch = life.settings(user)["schedule"]

    deadlines = [d for d in db.scalars(select(Deadline).where(Deadline.user_id == user.id, Deadline.done.is_(False)))
                 if due_at(d, zone) > now]
    # clear what the planner owns: future auto blocks that haven't been done or missed
    for r in db.scalars(select(StudyBlock).where(StudyBlock.user_id == user.id, StudyBlock.source == "auto",
                                                 StudyBlock.status == "planned", StudyBlock.day >= today)):
        if datetime.combine(r.day, _hm(r.start_time), zone) >= now or r.day > today:
            db.delete(r)
    db.flush()
    if not deadlines:
        db.commit()
        return {"blocks": 0, "short": []}

    horizon = max(d.due_date for d in deadlines)
    done = {d.id: 0.0 for d in deadlines}
    kept: list[StudyBlock] = []
    for r in db.scalars(select(StudyBlock).where(StudyBlock.user_id == user.id,
                                                 StudyBlock.deadline_id.in_(list(done)))):
        if r.status == "done":
            done[r.deadline_id] += r.minutes
        elif r.status == "planned":
            kept.append(r)                      # hand-placed, or earlier today: still counts
            done[r.deadline_id] += r.minutes
    items = [S.Deadline(d.id, d.course, d.title, d.kind, due_at(d, zone),
                        max(0.0, hours_needed(d) - done[d.id] / 60), d.weight or 0) for d in deadlines]

    # calendar busy time (events you can study during don't count), padded for travel around
    # classes, work and exams; plus revision blocks that stay
    busy, _ = life.busy_blocks(db, user, today, horizon, include_study=False)
    ok = _keywords(st.get("ok_during"))
    pad = timedelta(minutes=int(sch.get("buffer_min") or 0))
    raw: list[S.Interval] = []
    for b in busy:
        if b.all_day:
            continue
        match = next((h for k, h in ok if k in b.title.lower()), False)
        if match is not False:
            if match is not None and b.end - b.start > timedelta(hours=match):
                raw.append(S.Interval(b.start + timedelta(hours=match), b.end))   # rest of the shift stays busy
            continue
        long_event = b.end - b.start >= timedelta(hours=2) and b.start.date() == b.end.date()   # not overnight sleep
        p = pad if b.kind in ("class", "work", "exam") or long_event else timedelta(0)
        raw.append(S.Interval(b.start - p, b.end + p))
    for r in kept:
        s = datetime.combine(r.day, _hm(r.start_time), zone)
        raw.append(S.Interval(s, s + timedelta(minutes=r.minutes + S.BREAK_MIN)))

    # keep one slot per day for that day's training (longest session + travel both ways)
    keep: dict[date, int] = {}
    for s in db.scalars(select(Session).where(Session.user_id == user.id, Session.day.between(today, horizon),
                                              Session.status == "planned")):
        keep[s.day] = max(keep.get(s.day, 0), s.minutes + 2 * int(sch.get("buffer_min") or 0))

    days = {}
    d = today
    while d <= horizon:
        lo = datetime.combine(d, _hm(st["day_start"]), zone)
        hi = datetime.combine(d, _hm(st["day_end"]), zone)
        days[d] = (lo, hi, [x for x in raw if x.end > lo and x.start < hi])
        d += timedelta(days=1)

    opts = S.Options(block_min=int(st["block_min"]), max_per_day_min=int(float(st["max_per_day_h"]) * 60),
                     exam_gap_min=int(st["exam_gap_min"]), keep_for_training=keep)
    placed = S.plan(items, days, opts, now)
    for b in placed:
        db.add(StudyBlock(user_id=user.id, deadline_id=b.deadline_id, day=b.start.date(),
                          start_time=b.start.strftime("%H:%M"), minutes=b.minutes))
    db.commit()

    # training goes around the revision blocks now
    life.assign_start_times(db, user, today, horizon)

    cov = S.coverage(items, placed)
    short = [{"id": i.id, "course": i.course, "title": i.title, "short_min": round(i.hours * 60 - cov.get(i.id, 0))}
             for i in items if i.hours * 60 - cov.get(i.id, 0) >= 30]
    return {"blocks": len(placed), "minutes": sum(b.minutes for b in placed), "short": short}


def safe_replan(db: DB, user: User) -> None:
    """Re-plan after a change elsewhere (calendar refresh, new training plan). Never fails the caller."""
    try:
        if db.scalar(select(Deadline.id).where(Deadline.user_id == user.id, Deadline.done.is_(False))):
            replan(db, user)
    except Exception:  # noqa: BLE001
        db.rollback()


def set_block_status(db: DB, user: User, bid: int, status: str) -> StudyBlock:
    r = db.get(StudyBlock, bid)
    if not r or r.user_id != user.id:
        raise LookupError("Block not found")
    if status not in ("planned", "done", "missed"):
        raise ValueError("status must be planned, done or missed")
    r.status = status
    db.commit()
    return r
