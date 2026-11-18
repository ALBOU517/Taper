from __future__ import annotations

import os
import secrets
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .. import services
from ..auth import current_user, hash_password, sign, verify_password
from ..db import Session, User, get_db
from ..engine import dsl, ics, load
from ..engine import periodization as P

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ schemas
class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


class ProfileIn(BaseModel):
    name: Optional[str] = None
    thresholds: Optional[dict] = None
    availability: Optional[dict] = None


class CommitmentIn(BaseModel):
    """Training you already do elsewhere (club practice, climbing, a team sport)."""
    day: int = Field(ge=0, le=6)                           # 0 = Monday
    minutes: int = Field(60, ge=10, le=300)
    name: str = Field("Your training", max_length=40)
    hard: bool = True


class GoalIn(BaseModel):
    event: str = "custom"                                  # a race key, or "custom" for a race-free block
    sports: list[str] = Field(default_factory=list)        # run | bike | swim | gym | walk | mobility
    target_time: Optional[str] = None                      # "1:45:00" for run races
    target_time_s: Optional[int] = None
    body_focus: bool = False
    block_weeks: Optional[int] = Field(None, ge=4, le=52)  # race-free blocks: length instead of a date
    race_date: Optional[date] = None
    start_date: Optional[date] = None
    level: str = "intermediate"
    availability: Optional[dict] = None
    baseline_hours: float = 4
    baseline_ctl: Optional[float] = None
    gym_per_week: int = Field(2, ge=0, le=7)
    recovery_every: int = Field(4, ge=3, le=5)
    name: Optional[str] = None
    equipment: Literal["gym", "home", "none"] = "gym"     # what strength sessions can use
    away_days: list[int] = Field(default_factory=list)     # weekdays without your equipment (travel)
    commitments: list[CommitmentIn] = Field(default_factory=list, max_length=10)
    mobility_per_week: Optional[int] = Field(None, ge=0, le=7)


class SessionIn(BaseModel):
    day: date
    dsl: str
    title: Optional[str] = None


class SessionPatch(BaseModel):
    day: Optional[date] = None
    dsl: Optional[str] = None
    title: Optional[str] = None


class CompleteIn(BaseModel):
    actual_minutes: Optional[float] = None
    actual_distance_m: Optional[float] = None
    rpe: Optional[float] = Field(None, ge=1, le=10)
    avg_hr: Optional[float] = None
    avg_power: Optional[float] = None
    note: Optional[str] = None


class AdaptIn(BaseModel):
    equipment: Optional[Literal["gym", "home", "none"]] = None
    minutes: Optional[int] = Field(None, ge=10, le=300)


class ParseIn(BaseModel):
    text: str


def _user_out(u: User) -> dict:
    from .. import life
    return {"id": u.id, "email": u.email, "name": u.name, "thresholds": services.thresholds(u).__dict__,
            "settings": life.settings(u),
            "availability": services.availability(u), "ics_token": u.ics_token,
            "strava_connected": bool(u.strava), "is_demo": u.is_demo}


def _owned(db: DB, user: User, sid: int) -> Session:
    s = db.get(Session, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "Session not found")
    return s


# ------------------------------------------------------------------ auth
@router.post("/auth/register")
def register(body: RegisterIn, db: DB = Depends(get_db)):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Invalid email")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with that email already exists")
    u = User(email=email, name=body.name, password_hash=hash_password(body.password),
             thresholds={}, availability={str(k): v for k, v in services.DEFAULT_AVAILABILITY.items()},
             ics_token=secrets.token_urlsafe(24))
    db.add(u)
    db.commit()
    return {"token": sign({"uid": u.id}), "user": _user_out(u)}


@router.post("/auth/login")
def login(body: LoginIn, db: DB = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Wrong email or password")
    return {"token": sign({"uid": u.id}), "user": _user_out(u)}


DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"


@router.post("/auth/demo/{key}")
def demo_login(key: str, db: DB = Depends(get_db)):
    from ..seed import DEMOS, ensure_demo
    if not DEMO_MODE or key not in DEMOS:
        raise HTTPException(404, "Unknown demo profile")
    u = ensure_demo(db, key)
    return {"token": sign({"uid": u.id}, ttl=60 * 60 * 6), "user": _user_out(u)}


@router.get("/demo/profiles")
def demo_profiles():
    from ..seed import DEMOS
    if not DEMO_MODE:
        return []
    return [{"key": k, "name": v["name"], "blurb": v["blurb"]} for k, v in DEMOS.items()]


# ------------------------------------------------------------------ profile
@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_out(user)


@router.put("/me")
def update_me(body: ProfileIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    if body.name:
        user.name = body.name
    if body.thresholds is not None:
        user.thresholds = {**(user.thresholds or {}), **body.thresholds}
    if body.availability is not None:
        user.availability = {str(k): int(v) for k, v in body.availability.items()}
    db.commit()
    return _user_out(user)


@router.post("/me/ics-token")
def rotate_ics(user: User = Depends(current_user), db: DB = Depends(get_db)):
    user.ics_token = secrets.token_urlsafe(24)
    db.commit()
    return {"ics_token": user.ics_token}


# ------------------------------------------------------------------ plans
@router.get("/events")
def events():
    """Races, with the sports they need, so the wizard only offers races that match the athlete's picks."""
    return [{"key": k, "name": v["name"], "sports": [s for s in v["mix"] if s != "gym"], "taper_weeks": v["taper"],
             "target_time": k in P.RACE_DISTANCE_KM}
            for k, v in P.EVENTS.items() if k != "general"]


def _normalise_goal(body: "GoalIn") -> "GoalIn":
    allowed = {"run", "bike", "swim", "gym", "walk", "mobility"}
    if any(not 0 <= d <= 6 for d in body.away_days):
        raise HTTPException(422, "away_days are weekdays 0 (Monday) to 6 (Sunday)")
    if any(s not in allowed for s in body.sports):
        raise HTTPException(422, f"sports must be among {', '.join(sorted(allowed))}")
    if body.event != "custom" and body.event not in P.EVENTS:
        raise HTTPException(422, "Unknown race")
    if body.event == "custom" and not body.sports:
        raise HTTPException(422, "Pick at least one sport")
    if not body.race_date:
        if body.event != "custom":
            raise HTTPException(422, "A race needs a date")
        body.race_date = (body.start_date or date.today()) + timedelta(weeks=body.block_weeks or 12)
    if body.target_time and not body.target_time_s:
        try:
            parts = [int(x) for x in body.target_time.split(":")]
        except ValueError as e:
            raise HTTPException(422, "Target time looks like 1:45:00 or 52:30") from e
        secs = 0
        for x in parts:
            secs = secs * 60 + x
        body.target_time_s = secs if len(parts) > 1 else secs * 60
    return body


@router.post("/plans")
def create_plan(body: GoalIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    body = _normalise_goal(body)
    from .. import life
    from ..planning import plan_context
    start = body.start_date or date.today()
    try:
        plan = services.create_plan(db, user, body.model_dump(mode="json"), context=plan_context(
            db, user, start, body.race_date))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    life.assign_start_times(db, user, start, body.race_date)
    return {"id": plan.id, "goal": plan.goal, "weeks": plan.weeks, "projection": plan.projection}


@router.post("/plans/preview")
def preview_plan(body: GoalIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    """Dry-run the generator without saving — powers the live goal wizard."""
    body = _normalise_goal(body)
    try:
        goal = services.goal_from(body.model_dump(mode="json"), user, date.today(), _ctx(db, user, body))
        plan = P.generate(goal, services.thresholds(user))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    d = plan.to_dict()
    first = [s for s in plan.sessions if s.day < plan.weeks[0].start + timedelta(days=7 * (2 if len(plan.weeks) > 1 else 1))]
    return {"weeks": d["weeks"], "projection": d["projection"], "sessions": len(d["sessions"]),
            "easy_days": len(goal.easy_dates), "commitments": goal.commitments, "equipment": goal.equipment,
            "sample": [{"day": s.day.isoformat(), "sport": s.sport, "title": s.title, "minutes": s.minutes}
                       for s in first]}


def _ctx(db: DB, user: User, body: GoalIn) -> dict:
    from ..planning import plan_context
    return plan_context(db, user, body.start_date or date.today(), body.race_date)


@router.get("/plans/active")
def get_active(user: User = Depends(current_user), db: DB = Depends(get_db)):
    plan = services.active_plan(db, user)
    if not plan:
        return None
    return {"id": plan.id, "goal": plan.goal, "weeks": plan.weeks, "projection": plan.projection}


# ------------------------------------------------------------------ sessions
@router.get("/sessions")
def list_sessions(start: date, end: date, user: User = Depends(current_user), db: DB = Depends(get_db)):
    if (end - start).days > 400:
        raise HTTPException(422, "Range too large")
    rows = db.scalars(select(Session).where(Session.user_id == user.id, Session.day.between(start, end))
                      .order_by(Session.day, Session.id))
    th = services.thresholds(user)
    return [services.session_dict(r, th) for r in rows]


@router.post("/sessions")
def add_session(body: SessionIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    try:
        w = dsl.parse(body.dsl)
    except dsl.DSLError as e:
        raise HTTPException(422, str(e)) from e
    est = load.estimate(w, services.thresholds(user))
    plan = services.active_plan(db, user)
    s = Session(user_id=user.id, plan_id=plan.id if plan else None, day=body.day, sport=w.sport,
                kind="strength" if w.sport == "gym" else ("quality" if est.intensity_factor >= 0.8 else "easy"),
                title=body.title or f"Custom {w.sport}", dsl=w.source, minutes=round(est.duration_s / 60),
                tss=est.tss, distance_m=est.distance_m, source="custom")
    db.add(s)
    db.commit()
    return services.session_dict(s)


@router.patch("/sessions/{sid}")
def edit_session(sid: int, body: SessionPatch, user: User = Depends(current_user), db: DB = Depends(get_db)):
    s = _owned(db, user, sid)
    if body.dsl:
        try:
            w = dsl.parse(body.dsl)
        except dsl.DSLError as e:
            raise HTTPException(422, str(e)) from e
        est = load.estimate(w, services.thresholds(user))
        s.dsl, s.sport, s.minutes, s.tss, s.distance_m = w.source, w.sport, round(est.duration_s / 60), est.tss, est.distance_m
    if body.day:
        s.day = body.day
    if body.title:
        s.title = body.title
    db.commit()
    return services.session_dict(s)


@router.delete("/sessions/{sid}")
def delete_session(sid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    db.delete(_owned(db, user, sid))
    db.commit()
    return {"ok": True}


@router.post("/sessions/{sid}/complete")
def complete_session(sid: int, body: CompleteIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    s = services.complete(db, user, _owned(db, user, sid), body.model_dump())
    return services.session_dict(s)


@router.post("/sessions/{sid}/miss")
def miss_session(sid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    s = _owned(db, user, sid)
    if s.status != "planned":
        raise HTTPException(409, "Only planned sessions can be marked missed")
    return {"changes": services.miss(db, user, s)}


@router.post("/sessions/{sid}/adapt")
def adapt_session(sid: int, body: AdaptIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    """Life happens: no equipment today, or only N minutes. Undo with /restore."""
    if not body.equipment and not body.minutes:
        raise HTTPException(422, "Give equipment or minutes")
    try:
        s = services.adapt(db, user, _owned(db, user, sid), body.equipment, body.minutes)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return services.session_dict(s, services.thresholds(user))


@router.post("/sessions/{sid}/restore")
def restore_session(sid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    return services.session_dict(services.restore(db, _owned(db, user, sid)))


@router.post("/sessions/{sid}/reset")
def reset_session(sid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    s = _owned(db, user, sid)
    s.status, s.actual_minutes, s.actual_tss, s.actual_distance_m, s.rpe = "planned", None, None, None, None
    db.commit()
    return services.session_dict(s)


# ------------------------------------------------------------------ dsl / analytics
@router.post("/dsl/parse")
def parse_dsl(body: ParseIn, user: User = Depends(current_user)):
    try:
        w = dsl.parse(body.text)
    except dsl.DSLError as e:
        return {"ok": False, "error": str(e)}
    est = load.estimate(w, services.thresholds(user))
    return {"ok": True, "workout": w.to_dict(), "lines": dsl.describe(w),
            "estimate": {"minutes": round(est.duration_s / 60), "distance_m": est.distance_m, "tss": est.tss,
                         "if": est.intensity_factor, "breakdown": est.breakdown}}


@router.get("/load")
def load_curve(user: User = Depends(current_user), db: DB = Depends(get_db)):
    return services.load_series(db, user)


@router.get("/today")
def today(user: User = Depends(current_user), db: DB = Depends(get_db)):
    from .. import levels
    changes = levels.weekly_update(db, user)          # weekly level review runs lazily on first visit
    return {**services.today_summary(db, user), "level_changes": changes}


@router.get("/week")
def week(day: Optional[date] = None, user: User = Depends(current_user), db: DB = Depends(get_db)):
    return services.week_progress(db, user, day or date.today())


# ------------------------------------------------------------------ calendar feed
@router.get("/calendar/{token}.ics")
def calendar(token: str, db: DB = Depends(get_db)):
    u = db.scalar(select(User).where(User.ics_token == token))
    if not u:
        raise HTTPException(404, "Unknown calendar")
    start = date.today() - timedelta(days=14)
    rows = db.scalars(select(Session).where(Session.user_id == u.id, Session.day >= start).order_by(Session.day))
    items = [{"id": r.id, "day": r.day, "sport": r.sport, "title": r.title, "dsl": r.dsl, "minutes": r.minutes,
              "tss": r.tss, "status": r.status, "start_time": r.start_time} for r in rows]
    return Response(ics.build_calendar(items, name=f"Taper — {u.name}"), media_type="text/calendar",
                    headers={"Content-Disposition": 'inline; filename="taper.ics"'})
