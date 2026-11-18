"""Builds the planner's inputs from everything Taper knows about the athlete, and re-plans."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session as DB

from . import life, services
from .db import Plan, User
from .engine import calendar as C


def plan_context(db: DB, user: User, start: date, race: date) -> dict:
    ctx: dict = {"gym_routines": life.settings(user)["gym_routines"],
                 "levels": {k: {"stage": v["stage"], "progress": v.get("progress", 0.0), "assessed": v.get("assessed")}
                            for k, v in (life.settings(user).get("levels") or {}).items()}}
    if life.has_calendar(db, user):
        av, exams = life.availability(db, user, start - timedelta(days=start.weekday()), race)
        ctx["availability_by_date"] = {d: (a.total_min, a.longest_min) for d, a in av.items()}
        ctx["exam_weeks"] = C.exam_weeks(exams)
        busy, _ = life.busy_blocks(db, user, start - timedelta(days=1), race)
        ctx["easy_dates"] = C.recovery_days(busy, life.settings(user)["schedule"]["tz"])
    return ctx


def replan(db: DB, user: User, today: date | None = None) -> Plan:
    plan = services.active_plan(db, user)
    if not plan:
        raise LookupError("No active plan")
    today = today or date.today()
    race = date.fromisoformat(plan.goal["race_date"])
    if race <= today:
        raise ValueError("The goal date has passed — set a new goal")
    ctl = services.load_series(db, user, today)["today"]
    goal = {**plan.goal, "start_date": today.isoformat(), "baseline_ctl": ctl["ctl"] if ctl else None}
    for k in ("exam_weeks", "gym_routines", "levels", "event_name", "easy_dates"):
        goal.pop(k, None)
    new = services.create_plan(db, user, goal, today, plan_context(db, user, today, race))
    life.assign_start_times(db, user, today, race)
    return new
