from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.strava import apply_activity
from app.db import SessionLocal, User
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/api/auth/register", json={"email": "ali@example.com", "name": "Ali", "password": "supersecret"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_flow(client, auth):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=auth).json()["name"] == "Ali"
    assert client.post("/api/auth/login", json={"email": "ali@example.com", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ALI@example.com", "password": "supersecret"}).status_code == 200
    assert client.post("/api/auth/register", json={"email": "ali@example.com", "name": "x", "password": "supersecret"}).status_code == 409


def test_plan_lifecycle(client, auth):
    race = date.today() + timedelta(weeks=16)
    r = client.post("/api/plans", headers=auth, json={"event": "half_marathon", "race_date": race.isoformat(),
                                                      "level": "beginner", "baseline_hours": 3})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["weeks"][-1]["phase"] == "race"
    sessions = client.get("/api/sessions", headers=auth,
                          params={"start": date.today().isoformat(), "end": race.isoformat()}).json()
    assert any(s["kind"] == "race" for s in sessions)
    assert all(s["lines"] for s in sessions)


def test_complete_and_load(client, auth):
    day = date.today()
    s = client.post("/api/sessions", headers=auth, json={"day": day.isoformat(), "dsl": "Run: Easy 40min @ Z2"}).json()
    r = client.post(f"/api/sessions/{s['id']}/complete", headers=auth, json={"rpe": 4, "actual_minutes": 42})
    assert r.json()["status"] == "done" and r.json()["actual_tss"] > 0
    curve = client.get("/api/load", headers=auth).json()
    assert curve["points"] and curve["readiness"]["status"]
    today = client.get("/api/today", headers=auth).json()
    assert today["messages"] and "run" in today["week"]["sports"]


def test_miss_triggers_rebalance(client, auth):
    start = date.today() + timedelta(days=7 - date.today().weekday())   # next Monday
    rows = client.get("/api/sessions", headers=auth,
                      params={"start": start.isoformat(), "end": (start + timedelta(6)).isoformat()}).json()
    target = next(s for s in rows if s["kind"] in ("quality", "long", "easy"))
    r = client.post(f"/api/sessions/{target['id']}/miss", headers=auth)
    assert r.status_code == 200 and r.json()["changes"]
    assert client.post(f"/api/sessions/{target['id']}/miss", headers=auth).status_code == 409


def test_dsl_endpoint(client, auth):
    ok = client.post("/api/dsl/parse", headers=auth, json={"text": "Swim: 400m Easy -> 8x100m @ 1:45/100m (20s rest)"}).json()
    assert ok["ok"] and ok["estimate"]["distance_m"] == 1200
    bad = client.post("/api/dsl/parse", headers=auth, json={"text": "Run: 5x(1km @ Z9)"}).json()
    assert not bad["ok"] and "Unknown target" in bad["error"]


def test_ics_feed(client, auth):
    tok = client.get("/api/me", headers=auth).json()["ics_token"]
    r = client.get(f"/api/calendar/{tok}.ics")
    assert r.status_code == 200 and r.text.startswith("BEGIN:VCALENDAR") and "VEVENT" in r.text
    assert client.get("/api/calendar/nope.ics").status_code == 404


def test_users_cannot_touch_each_others_sessions(client, auth):
    other = client.post("/api/auth/register", json={"email": "b@example.com", "name": "B", "password": "supersecret"}).json()
    h2 = {"Authorization": f"Bearer {other['token']}"}
    mine = client.get("/api/sessions", headers=auth, params={"start": date.today().isoformat(),
                                                             "end": (date.today() + timedelta(30)).isoformat()}).json()
    assert client.post(f"/api/sessions/{mine[0]['id']}/complete", headers=h2, json={}).status_code == 404


def test_demo_profiles(client):
    profiles = client.get("/api/demo/profiles").json()
    assert {p["key"] for p in profiles} == {"half-beginner", "olympic-build", "ironman"}
    r = client.post("/api/auth/demo/olympic-build")
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    curve = client.get("/api/load", headers=h).json()
    assert curve["today"]["ctl"] > 30


def test_strava_activity_matches_planned_session(client, auth):
    db = SessionLocal()
    user = db.query(User).filter_by(email="ali@example.com").one()
    day = date.today() + timedelta(days=1)
    planned = client.post("/api/sessions", headers=auth, json={"day": day.isoformat(), "dsl": "Bike: Endurance 60min @ Z2"}).json()
    act = {"id": 987654, "sport_type": "Ride", "start_date_local": f"{day.isoformat()}T07:30:00Z",
           "moving_time": 3700, "distance": 30000, "average_watts": 150, "name": "Morning Ride"}
    s = apply_activity(db, user, act)
    assert s.id == planned["id"] and s.status == "done" and s.actual_tss > 0
    again = apply_activity(db, user, act)            # idempotent on webhook retries
    assert again.id == s.id
    db.close()
