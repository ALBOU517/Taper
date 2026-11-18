"""Level checks, the per-sport ladder state and weekly auto-progression."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from . import life, planning, services
from .db import Session, User
from .engine import ladder as L

EASY_KINDS = ("easy", "recovery", "long")


def state(user: User) -> dict:
    # deep copy: editing nested dicts in place would hide the change from the ORM
    return copy.deepcopy(life.settings(user).get("levels") or {})


def _save(db: DB, user: User, levels: dict) -> None:
    life.merge_settings(user, {"levels": levels})
    db.commit()


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def assess(db: DB, user: User, sport: str, m: dict, session_id: Optional[int] = None,
           today: Optional[date] = None) -> dict:
    """Place the athlete from a level check and re-plan."""
    today = today or date.today()
    b = life.settings(user)["body"]
    age = today.year - int(b["birth_year"]) if b.get("birth_year") else None
    if sport == "run":
        p = L.place_run(float(m["continuous_min"]), m.get("distance_km"), float(m.get("minutes") or 20),
                        m.get("rpe"), m.get("avg_hr"), age)
    elif sport == "swim":
        p = L.place_swim(float(m["continuous_m"]), m.get("total_m"), m.get("total_sec"))
    elif sport == "bike":
        p = L.place_bike(float(m.get("minutes") or 30), m.get("distance_km"), bool(m.get("comfortable", True)),
                         m.get("avg_power"), m.get("rpe"))
    elif sport == "gym":
        p = L.place_gym(float(m["months_lifting"]))
    else:
        raise ValueError("sport must be run, swim, bike or gym")
    if p.thresholds:
        user.thresholds = {**(user.thresholds or {}), **p.thresholds}
    levels = state(user)
    hist = (levels.get(sport) or {}).get("history", [])
    st = L.stage(sport, p.stage)
    hist.append({"date": today.isoformat(), "event": "level check", "stage": p.stage, "detail": "; ".join(p.reasons)})
    levels[sport] = {"stage": p.stage, "progress": 0.0, "assessed": True,
                     "assessed_at": datetime.now(timezone.utc).isoformat(),
                     "evaluated_through": _monday(today).isoformat(), "history": hist[-30:]}
    _save(db, user, levels)
    if session_id:
        s = db.get(Session, session_id)
        if s and s.user_id == user.id:
            km = m.get("distance_km") or ((m.get("total_m") or 0) / 1000) or None
            services.complete(db, user, s, {"actual_minutes": m.get("minutes") or (m.get("total_sec") or 0) / 60 or None,
                                            "actual_distance_m": km * 1000 if km else None, "rpe": m.get("rpe"),
                                            "avg_hr": m.get("avg_hr"), "avg_power": m.get("avg_power")})
    replanned = _replan(db, user)
    return {"sport": sport, "stage": p.stage, "name": st.name, "description": st.description, "reasons": p.reasons,
            "thresholds": p.thresholds, "replanned": replanned}


def set_stage(db: DB, user: User, sport: str, stage: int) -> dict:
    levels = state(user)
    cur = levels.get(sport) or {}
    hist = cur.get("history", []) + [{"date": date.today().isoformat(), "event": "set by you", "stage": stage,
                                      "detail": L.stage(sport, stage).name}]
    levels[sport] = {**cur, "stage": stage, "progress": 0.0, "assessed": True,
                     "evaluated_through": _monday(date.today()).isoformat(), "history": hist[-30:]}
    _save(db, user, levels)
    return {"replanned": _replan(db, user)}


def _replan(db: DB, user: User) -> bool:
    try:
        planning.replan(db, user)
        return True
    except (LookupError, ValueError):
        return False


def week_stats(db: DB, user: User, sport: str, monday: date) -> L.WeekStats:
    rows = list(db.scalars(select(Session).where(Session.user_id == user.id, Session.sport == sport,
                                                 Session.day.between(monday, monday + timedelta(days=6)),
                                                 Session.kind.not_in(("test", "race")))))
    planned = [r for r in rows if r.source != "strava" or r.status != "done"]
    done = [r for r in rows if r.status == "done"]
    rpes = [r.rpe for r in done if r.rpe and r.kind in EASY_KINDS]
    return L.WeekStats(len(planned), len(done), rpes)


def weekly_update(db: DB, user: User, today: Optional[date] = None) -> list[dict]:
    """Evaluate every finished week since the last evaluation; re-plan if a stage changed."""
    today = today or date.today()
    this_monday = _monday(today)
    levels = state(user)
    changes, changed_stage = [], False
    for sport, lv in levels.items():
        if not lv.get("assessed") or sport not in L.LADDERS:
            continue
        cursor = date.fromisoformat(lv.get("evaluated_through") or this_monday.isoformat())
        while cursor < this_monday:
            ws = week_stats(db, user, sport, cursor)
            before = lv["stage"]
            if ws.planned:
                lv["stage"], lv["progress"], why = L.progress(lv["stage"], lv.get("progress", 0.0), sport, ws)
                lv.setdefault("history", []).append({"date": (cursor + timedelta(days=6)).isoformat(),
                                                     "event": "week review", "stage": lv["stage"], "detail": why})
                lv["history"] = lv["history"][-30:]
                changes.append({"sport": sport, "week": cursor.isoformat(), "detail": why,
                                "stage_changed": lv["stage"] != before})
                changed_stage |= lv["stage"] != before
            cursor += timedelta(days=7)
        lv["evaluated_through"] = this_monday.isoformat()
    if changes:
        _save(db, user, levels)
    if changed_stage:
        _replan(db, user)
    return changes


def overview(db: DB, user: User) -> dict:
    levels = state(user)
    plan = services.active_plan(db, user)
    proj = (plan.projection or {}).get("levels", {}) if plan else {}
    sports = list(services.P.event_config(plan.goal["event"], plan.goal.get("sports") or None,
                                          bool(plan.goal.get("body_focus")))["mix"]) if plan else []
    sports = sports or list(L.LADDERS)
    out = {}
    for sp in sports:
        if sp not in L.LADDERS:
            continue
        lv = levels.get(sp) or {}
        st_i = lv.get("stage", proj.get(sp, {}).get("now", 0))
        pending = db.scalar(select(Session).where(Session.user_id == user.id, Session.sport == sp,
                                                  Session.kind == "test", Session.status == "planned")
                            .order_by(Session.day))
        out[sp] = {
            "stage": st_i, "progress": round(lv.get("progress", 0.0), 2), "assessed": bool(lv.get("assessed")),
            "ladder": [{"index": s.index, "name": s.name, "description": s.description, "criteria": s.criteria,
                        "weeks": s.weeks} for s in L.LADDERS[sp]],
            "history": list(reversed(lv.get("history", [])))[:8],
            "projection": proj.get(sp),
            "check": {"title": L.LEVEL_CHECK[sp][0], "instructions": L.LEVEL_CHECK[sp][2]} if sp in L.LEVEL_CHECK else None,
            "pending_check": services.session_dict(pending) if pending else None,
        }
    return {"sports": out}
