"""Glue between the pure engine and the database."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .db import Plan, Session, User
from .engine import dsl, load, rebalancer
from .engine import periodization as P
from .engine.constraints import PlannedSession

DEFAULT_AVAILABILITY = {0: 60, 1: 75, 2: 60, 3: 75, 4: 0, 5: 120, 6: 150}
SPORT_NAMES = {"run": "run", "bike": "ride", "swim": "swim", "gym": "strength session", "walk": "walk",
               "mobility": "mobility session"}


def thresholds(user: User) -> load.Thresholds:
    return load.Thresholds.from_dict(user.thresholds)


def availability(user: User) -> dict[int, int]:
    av = user.availability or DEFAULT_AVAILABILITY
    return {int(k): int(v) for k, v in av.items()}


def active_plan(db: DB, user: User) -> Optional[Plan]:
    return db.scalar(select(Plan).where(Plan.user_id == user.id, Plan.active).order_by(Plan.id.desc()))


def session_dict(s: Session, th: Optional[load.Thresholds] = None) -> dict:
    try:
        w = dsl.parse(s.dsl)
        tree, lines = w.to_dict()["steps"], dsl.describe(w)
        profile = [[b["duration_s"], b["if"]] for b in load.estimate(w, th).breakdown]
    except dsl.DSLError:
        tree, lines, profile = [], [s.dsl], []
    return {
        "id": s.id, "day": s.day.isoformat(), "sport": s.sport, "kind": s.kind, "title": s.title,
        "dsl": s.dsl, "minutes": s.minutes, "tss": s.tss, "distance_m": s.distance_m, "status": s.status,
        "actual_minutes": s.actual_minutes, "actual_tss": s.actual_tss, "actual_distance_m": s.actual_distance_m,
        "rpe": s.rpe, "source": s.source, "note": s.note, "steps": tree, "lines": lines, "profile": profile,
        "start_time": s.start_time, "adapted": bool(s.original_dsl),
    }


# ------------------------------------------------------------------ life happens: adapt one session
def adapt(db: DB, user: User, s: Session, equipment: Optional[str] = None, minutes: Optional[int] = None) -> Session:
    """Rewrite one planned session for today's reality: other equipment, or less time.

    Works from the original session each time, so changes combine and can be undone.
    """
    from .engine import ladder, templates
    if s.status != "planned" or s.kind in ("race", "test"):
        raise ValueError("Only planned training sessions can be adapted")
    if not s.original_dsl:
        s.original_dsl, s.original_title = s.dsl, s.title
    th = thresholds(user)
    text, title = s.original_dsl, s.original_title
    if s.sport == "gym":
        if equipment:
            new = templates.for_equipment(text, equipment)
            if new != text:
                text, title = new, f"{title} ({templates.EQUIP_LABEL[equipment]})"
        if minutes:
            text = templates.fit_gym(text, minutes, th)
    elif minutes:
        if s.sport == "mobility":
            title, text = templates.mobility("mobility", "base", minutes, th, templates.mobility_variant(title))
        elif s.sport == "run" and "Walk @" in text:
            st = ladder.RUN[0] if "1min @ Z2" in text else ladder.RUN[1]
            title, text = ladder.stage_session("run", st, s.kind, minutes)
        else:
            kind = s.kind if s.kind in ("easy", "recovery", "quality", "long") else "easy"
            if kind in ("quality", "long") and minutes < 40:
                kind = "easy"           # not enough time for the key part: an easy session keeps the habit
            title, text = templates.build(s.sport, kind, "build", minutes, th)
    w = dsl.parse(text)
    est = load.estimate(w, th)
    s.dsl, s.title, s.minutes, s.tss, s.distance_m = text, title, round(est.duration_s / 60), est.tss, est.distance_m
    db.commit()
    return s


def restore(db: DB, s: Session) -> Session:
    if s.original_dsl:
        w = dsl.parse(s.original_dsl)
        est = load.estimate(w)
        s.dsl, s.title = s.original_dsl, s.original_title or s.title
        s.minutes, s.tss, s.distance_m = round(est.duration_s / 60), est.tss, est.distance_m
        s.original_dsl = s.original_title = None
        db.commit()
    return s


def to_planned(s: Session) -> PlannedSession:
    return PlannedSession(s.day, s.sport, s.kind, s.title, s.dsl, s.minutes, s.tss, s.distance_m, id=s.id,
                          status=s.status if s.status != "done" else "planned")


# ------------------------------------------------------------------ plan creation
def create_plan(db: DB, user: User, goal_in: dict, today: Optional[date] = None,
                context: Optional[dict] = None) -> Plan:
    """``context``: calendar-derived planning inputs (availability_by_date, exam_weeks, gym_routines)."""
    today = today or date.today()
    goal = goal_from(goal_in, user, today, context)
    plan = P.generate(goal, thresholds(user))
    user.availability = {str(k): v for k, v in goal.availability.items()}
    return _save_plan(db, user, goal, plan)


def goal_from(goal_in: dict, user: User, today: date, context: Optional[dict] = None) -> P.Goal:
    """The planner's Goal from API input — one place, so preview and the saved plan always agree."""
    return P.Goal(
        event=goal_in["event"],
        race_date=date.fromisoformat(str(goal_in["race_date"])),
        start_date=date.fromisoformat(str(goal_in.get("start_date") or today)),
        level=goal_in.get("level", "intermediate"),
        availability={int(k): int(v) for k, v in (goal_in.get("availability") or availability(user)).items()},
        baseline_ctl=goal_in.get("baseline_ctl"),
        baseline_hours=float(goal_in.get("baseline_hours") or 4),
        gym_per_week=int(goal_in.get("gym_per_week", 2)),
        recovery_every=int(goal_in.get("recovery_every", 4)),
        name=goal_in.get("name"),
        sports=list(goal_in.get("sports") or []),
        target_time_s=int(goal_in["target_time_s"]) if goal_in.get("target_time_s") else None,
        body_focus=bool(goal_in.get("body_focus")),
        equipment=goal_in.get("equipment") or "gym",
        away_days={int(x) for x in goal_in.get("away_days") or []},
        commitments=[{"day": int(c["day"]), "minutes": int(c["minutes"]), "name": c.get("name") or "Your training",
                      "hard": bool(c.get("hard", True))} for c in goal_in.get("commitments") or []],
        mobility_per_week=goal_in.get("mobility_per_week"),
        **(context or {}),
    )


def _save_plan(db: DB, user: User, goal: P.Goal, plan: P.Plan) -> Plan:
    # retire the old plan's future sessions; keep history
    for old in db.scalars(select(Plan).where(Plan.user_id == user.id, Plan.active)):
        old.active = False
        for s in db.scalars(select(Session).where(Session.plan_id == old.id, Session.day >= goal.start_date,
                                                  Session.status == "planned")):
            db.delete(s)

    pd = plan.to_dict()
    row = Plan(user_id=user.id, goal=pd["goal"], weeks=pd["weeks"], projection=pd["projection"], active=True)
    row.goal["event_name"] = plan.projection.get("event_name") or P.EVENTS[goal.event]["name"]
    db.add(row)
    db.flush()
    for s in plan.sessions:
        db.add(Session(user_id=user.id, plan_id=row.id, day=s.day, sport=s.sport, kind=s.kind, title=s.title,
                       dsl=s.dsl, minutes=s.minutes, tss=s.tss, distance_m=s.distance_m))
    db.commit()
    return row


# ------------------------------------------------------------------ completion
def complete(db: DB, user: User, s: Session, data: dict) -> Session:
    th = thresholds(user)
    minutes = data.get("actual_minutes") or s.minutes
    dist = data.get("actual_distance_m") if data.get("actual_distance_m") is not None else s.distance_m
    signals = any(data.get(k) for k in ("rpe", "avg_hr", "avg_power", "actual_distance_m", "actual_minutes"))
    if signals:
        tss = load.tss_from_actual(s.sport, minutes * 60, rpe=data.get("rpe"), avg_power=data.get("avg_power"),
                                   avg_hr=data.get("avg_hr"),
                                   distance_m=data.get("actual_distance_m"), th=th)
        if not any(data.get(k) for k in ("rpe", "avg_hr", "avg_power", "actual_distance_m")):
            tss = s.tss * minutes / max(s.minutes, 1)       # only time changed → scale plan
    else:
        tss = s.tss
    s.status, s.actual_minutes, s.actual_tss, s.actual_distance_m = "done", minutes, round(tss, 1), dist
    s.rpe = data.get("rpe")
    s.note = data.get("note") or s.note
    s.completed_at = datetime.now(timezone.utc)
    db.commit()
    return s


def week_of(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def miss(db: DB, user: User, s: Session, today: Optional[date] = None) -> list[dict]:
    """Mark a session missed and re-solve the rest of the week."""
    today = today or date.today()
    ws, we = week_of(s.day)
    rows = list(db.scalars(select(Session).where(Session.user_id == user.id, Session.day.between(ws, we))))
    planned = [to_planned(r) for r in rows]
    by_id = {p.id: p for p in planned}
    target = by_id[s.id]
    plan = active_plan(db, user)
    race_day = date.fromisoformat(plan.goal["race_date"]) if plan and plan.goal.get("event") not in P.NO_RACE else None
    changes = rebalancer.rebalance(target, planned, max(today, s.day), availability(user), race_day, thresholds(user))

    s.status = "missed"
    rows_by_id = {r.id: r for r in rows}
    out = []
    for c in changes:
        row = rows_by_id.get(c.session_id)
        if c.action == "moved" and row:
            # missed stays as history; a fresh copy is scheduled on the new day
            db.add(Session(user_id=user.id, plan_id=row.plan_id, day=c.new_day, sport=row.sport, kind=row.kind,
                           title=row.title, dsl=row.dsl, minutes=row.minutes, tss=row.tss,
                           distance_m=row.distance_m, source="rebalance", note=f"Moved from {row.day:%A}"))
        elif c.action == "dropped" and row:
            db.delete(row)
        elif c.action == "extended" and row:
            row.dsl, row.minutes, row.tss = c.new_dsl, c.new_minutes, c.new_tss
            row.source, row.note = "rebalance", "Extended to absorb missed volume"
        out.append({"action": c.action, "session_id": c.session_id, "detail": c.detail,
                    "new_day": c.new_day.isoformat() if c.new_day else None})
    db.commit()
    return out


# ------------------------------------------------------------------ analytics
def load_series(db: DB, user: User, today: Optional[date] = None) -> dict:
    today = today or date.today()
    plan = active_plan(db, user)
    rows = list(db.scalars(select(Session).where(Session.user_id == user.id)))
    if not rows:
        return {"points": [], "readiness": load.readiness(0, 0, 0), "today": None}
    start = min(r.day for r in rows) - timedelta(days=1)
    end = max(max(r.day for r in rows), today)
    ctl0 = plan.projection.get("start_ctl", 0) if plan else 0
    daily: dict[date, float] = {}
    for r in rows:
        if r.status == "done":
            t = r.actual_tss or 0
        elif r.status == "planned" and r.day >= today:
            t = r.tss
        else:
            t = 0
        daily[r.day] = daily.get(r.day, 0) + t
    pts = load.banister(daily, start, end, ctl0, ctl0, projected_from=today)
    idx = next((i for i, p in enumerate(pts) if p.day == today), len(pts) - 1)
    cur = pts[idx]
    ramp = cur.ctl - pts[max(0, idx - 7)].ctl
    return {
        "points": [{"day": p.day.isoformat(), "tss": p.tss, "ctl": p.ctl, "atl": p.atl, "tsb": p.tsb,
                    "projected": p.projected} for p in pts],
        "today": {"ctl": cur.ctl, "atl": cur.atl, "tsb": cur.tsb, "ramp_7d": round(ramp, 1)},
        "readiness": load.readiness(cur.tsb, cur.ctl, ramp),
    }


def week_progress(db: DB, user: User, d: date) -> dict:
    ws, we = week_of(d)
    rows = list(db.scalars(select(Session).where(Session.user_id == user.id, Session.day.between(ws, we))))
    sports: dict[str, dict] = {}
    for r in rows:
        if r.status == "missed":
            continue
        e = sports.setdefault(r.sport, {"planned_min": 0, "done_min": 0, "planned_km": 0.0, "done_km": 0.0,
                                        "planned_tss": 0.0, "done_tss": 0.0, "sessions": 0, "done_sessions": 0})
        e["planned_min"] += r.minutes
        e["planned_km"] += r.distance_m / 1000
        e["planned_tss"] += r.tss
        e["sessions"] += 1
        if r.status == "done":
            e["done_min"] += r.actual_minutes or r.minutes
            e["done_km"] += (r.actual_distance_m if r.actual_distance_m is not None else r.distance_m) / 1000
            e["done_tss"] += r.actual_tss or 0
            e["done_sessions"] += 1
    for e in sports.values():
        for k in ("planned_km", "done_km", "planned_tss", "done_tss"):
            e[k] = round(e[k], 1)
    return {"week_start": ws.isoformat(), "sports": sports}


def today_summary(db: DB, user: User, today: Optional[date] = None) -> dict:
    today = today or date.today()
    rows = list(db.scalars(select(Session).where(Session.user_id == user.id, Session.day == today)))
    prog = week_progress(db, user, today)
    messages = []
    for r in rows:
        if r.status != "planned":
            continue
        p = prog["sports"].get(r.sport, {})
        if r.sport in ("run", "bike", "swim") and p.get("planned_km"):
            remaining = max(0.0, p["planned_km"] - p["done_km"])
            def unit(km: float, swim: bool = r.sport == "swim") -> str:
                return f"{km * 1000:.0f} m" if swim else f"{km:.1f} km"
            messages.append(f"Today's goal: {unit(r.distance_m / 1000)} {r.title.lower()} — "
                            f"{unit(remaining)} remaining of your {unit(p['planned_km'])} weekly {r.sport} target")
        else:
            messages.append(f"Today's goal: {r.title} ({r.minutes} min) — "
                            f"{p.get('done_sessions', 0)}/{p.get('sessions', 0)} {SPORT_NAMES.get(r.sport, 'session')}s done this week")
    if not rows:
        messages.append("Rest day. Sleep, eat well, and let the adaptations happen.")
    plan = active_plan(db, user)
    countdown = None
    if plan and plan.goal.get("event") not in P.NO_RACE:
        countdown = (date.fromisoformat(plan.goal["race_date"]) - today).days
    return {"date": today.isoformat(), "sessions": [session_dict(r, thresholds(user)) for r in rows],
            "messages": messages, "week": prog, "days_to_race": countdown}
