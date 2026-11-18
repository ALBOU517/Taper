"""Pre-populated demo athletes with realistic history so the dashboard is alive on first open."""
from __future__ import annotations

import random
import secrets
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from . import services
from .auth import hash_password
from .db import Session, User

DEMOS = {
    "half-beginner": dict(
        name="Maya · Half-Marathon Beginner",
        blurb="First half marathon, 4 runs + 2 gym sessions a week. 6 weeks in, 18 to go.",
        goal=dict(event="half_marathon", level="beginner", baseline_hours=3, gym_per_week=2),
        weeks_ago=6, weeks_ahead=18,
        thresholds=dict(run_threshold_pace=330),
        availability={0: 60, 1: 75, 2: 0, 3: 75, 4: 45, 5: 120, 6: 140},
    ),
    "olympic-build": dict(
        name="Léo · Olympic Triathlon Build",
        blurb="Age-group triathlete in the Build phase. Swim, bike, run + 1 gym.",
        goal=dict(event="olympic_tri", level="intermediate", baseline_hours=6, gym_per_week=1),
        weeks_ago=12, weeks_ahead=9,
        thresholds=dict(run_threshold_pace=265, swim_css=105, bike_ftp=245, bike_threshold_kmh=34),
        availability={0: 75, 1: 90, 2: 75, 3: 90, 4: 0, 5: 210, 6: 240},
    ),
    "ironman": dict(
        name="Sam · Ironman",
        blurb="Full-distance Ironman in 14 weeks. High volume, double days.",
        goal=dict(event="ironman", level="advanced", baseline_hours=11, gym_per_week=1),
        weeks_ago=16, weeks_ahead=14,
        thresholds=dict(run_threshold_pace=250, swim_css=95, bike_ftp=285, bike_threshold_kmh=36),
        availability={0: 90, 1: 150, 2: 120, 3: 150, 4: 60, 5: 330, 6: 300},
    ),
}


def ensure_demo(db: DB, key: str, today: date | None = None) -> User:
    today = today or date.today()
    cfg = DEMOS[key]
    email = f"demo+{key}@taper.app"
    u = db.scalar(select(User).where(User.email == email))
    if u and services.active_plan(db, u):
        return u
    if not u:
        u = User(email=email, name=cfg["name"].split(" · ")[0], password_hash=hash_password(secrets.token_hex(8)),
                 thresholds=cfg["thresholds"], availability={str(k): v for k, v in cfg["availability"].items()},
                 ics_token=secrets.token_urlsafe(24), is_demo=True)
        db.add(u)
        db.flush()

    monday = today - timedelta(days=today.weekday())
    start = monday - timedelta(weeks=cfg["weeks_ago"])
    race = monday + timedelta(weeks=cfg["weeks_ahead"], days=6)
    services.create_plan(db, u, {**cfg["goal"], "start_date": start.isoformat(), "race_date": race.isoformat(),
                                 "availability": cfg["availability"]}, today=start)

    rng = random.Random(key)
    for s in db.scalars(select(Session).where(Session.user_id == u.id, Session.day < today)):
        if rng.random() < 0.1 and s.kind != "race":
            s.status = "missed"
            continue
        f = rng.uniform(0.85, 1.12)
        s.status, s.actual_minutes = "done", round(s.minutes * f)
        s.actual_tss, s.actual_distance_m = round(s.tss * f, 1), round(s.distance_m * f)
        s.rpe = rng.choice([5, 6, 6, 7, 7, 8]) if s.kind != "easy" else rng.choice([3, 4, 4, 5])
    db.commit()
    return u
