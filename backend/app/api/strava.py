"""
Strava OAuth2 + webhook ingestion.

Completed Strava activities automatically tick off the matching planned session
(same day + sport) and update the fitness/fatigue curve.

Strava API terms: we store only *derived* metrics (duration, distance, TSS) on
our own session rows — never the raw payload — and never send Strava data to a
third-party AI service. Disconnecting deletes the stored tokens.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .. import services
from ..auth import current_user, sign, unsign
from ..db import Session, SessionLocal, User, get_db
from ..engine import load

router = APIRouter(prefix="/api/strava")

CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "taper-verify")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")

SPORT_MAP = {
    "Run": "run", "TrailRun": "run", "VirtualRun": "run",
    "Ride": "bike", "VirtualRide": "bike", "GravelRide": "bike", "MountainBikeRide": "bike", "EBikeRide": "bike",
    "Swim": "swim",
    "WeightTraining": "gym", "Workout": "gym", "Crossfit": "gym", "HighIntensityIntervalTraining": "gym",
}


def enabled() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


@router.get("/status")
def status(user: User = Depends(current_user)):
    return {"enabled": enabled(), "connected": bool(user.strava)}


@router.get("/connect")
def connect(user: User = Depends(current_user)):
    if not enabled():
        raise HTTPException(503, "Strava is not configured on this server (set STRAVA_CLIENT_ID/SECRET)")
    state = sign({"uid": user.id, "p": "strava"}, ttl=600)
    url = ("https://www.strava.com/oauth/authorize"
           f"?client_id={CLIENT_ID}&response_type=code&approval_prompt=auto&scope=activity:read"
           f"&redirect_uri={PUBLIC_URL}/api/strava/callback&state={state}")
    return {"url": url}


@router.get("/callback")
def callback(code: str, state: str, db: DB = Depends(get_db)):
    data = unsign(state)
    user = db.get(User, data["uid"])
    if not user or data.get("p") != "strava":
        raise HTTPException(400, "Bad state")
    r = httpx.post("https://www.strava.com/oauth/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": code, "grant_type": "authorization_code"},
        timeout=15)
    r.raise_for_status()
    tok = r.json()
    user.strava = {k: tok[k] for k in ("access_token", "refresh_token", "expires_at")}
    user.strava_athlete_id = tok.get("athlete", {}).get("id")
    db.commit()
    return RedirectResponse(f"{PUBLIC_URL}/#/settings?strava=connected")


@router.delete("")
def disconnect(user: User = Depends(current_user), db: DB = Depends(get_db)):
    if user.strava and enabled():
        try:
            httpx.post("https://www.strava.com/oauth/deauthorize",
                       data={"access_token": user.strava["access_token"]}, timeout=10)
        except httpx.HTTPError:
            pass
    user.strava, user.strava_athlete_id = None, None
    db.commit()
    return {"ok": True}


@router.get("/webhook")
def webhook_verify(request: Request):
    q = request.query_params
    if q.get("hub.verify_token") != VERIFY_TOKEN:
        raise HTTPException(403, "Bad verify token")
    return {"hub.challenge": q.get("hub.challenge")}


@router.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    evt = await request.json()
    if evt.get("object_type") == "activity" and evt.get("aspect_type") in ("create", "update"):
        background.add_task(_ingest, evt["owner_id"], evt["object_id"])
    return {"ok": True}   # Strava requires a 200 within 2 s; work happens in the background


def _token(db: DB, user: User) -> str:
    tok = user.strava or {}
    if tok.get("expires_at", 0) < time.time() + 60:
        r = httpx.post("https://www.strava.com/oauth/token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}, timeout=15)
        r.raise_for_status()
        new = r.json()
        user.strava = {k: new[k] for k in ("access_token", "refresh_token", "expires_at")}
        db.commit()
    return user.strava["access_token"]


def _ingest(athlete_id: int, activity_id: int) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.strava_athlete_id == athlete_id))
        if not user or not user.strava:
            return
        r = httpx.get(f"https://www.strava.com/api/v3/activities/{activity_id}",
                      headers={"Authorization": f"Bearer {_token(db, user)}"}, timeout=15)
        r.raise_for_status()
        apply_activity(db, user, r.json())
    finally:
        db.close()


def apply_activity(db: DB, user: User, act: dict) -> Session:
    """Match a Strava activity to a planned session (or log it as unplanned)."""
    sport = SPORT_MAP.get(act.get("sport_type") or act.get("type"), None)
    if sport is None:
        sport = "gym"
    ext = str(act["id"])
    day = datetime.fromisoformat(act["start_date_local"].replace("Z", "")).date()
    moving = float(act.get("moving_time") or act.get("elapsed_time") or 0)
    dist = float(act.get("distance") or 0)
    tss = load.tss_from_actual(sport, moving, avg_power=act.get("weighted_average_watts") or act.get("average_watts"),
                               avg_hr=act.get("average_heartrate"), distance_m=dist or None,
                               rpe=act.get("perceived_exertion"), th=services.thresholds(user))

    existing = db.scalar(select(Session).where(Session.user_id == user.id, Session.external_id == ext))
    target = existing or db.scalar(
        select(Session).where(Session.user_id == user.id, Session.day == day, Session.sport == sport,
                              Session.status == "planned").order_by(Session.tss.desc()))
    if target is None:
        target = Session(user_id=user.id, day=day, sport=sport, kind="easy", title=act.get("name", "Activity")[:120],
                         dsl=f"{sport.title()}: {round(moving / 60)}min @ Z2", minutes=round(moving / 60),
                         tss=tss, distance_m=dist, source="strava")
        db.add(target)
    target.status, target.actual_minutes, target.actual_tss = "done", round(moving / 60), tss
    target.actual_distance_m, target.external_id = dist, ext
    target.completed_at = datetime.now(timezone.utc)
    db.commit()
    return target
