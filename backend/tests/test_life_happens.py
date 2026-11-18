"""Features added for real-life constraints: equipment, mobility, outside training, night shifts,
short-on-time sessions. Fixtures are hand-written and labeled; no data is invented for users."""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.engine import calendar as C
from app.engine import dsl, templates
from app.engine import periodization as P
from app.engine.constraints import PlannedSession, group_by_day, violations
from app.engine.load import estimate
from app.main import app

MON = date(2026, 11, 23)


def _s(day, sport, kind, minutes, text, hard=False):
    return PlannedSession(day, sport, kind, kind, text, minutes, commit_hard=hard)


# ------------------------------------------------------------------ DSL & templates
def test_timed_holds_and_mobility_parse():
    w = dsl.parse("Mobility: Side Plank 3x30s | Cat-Cow 2x10")
    assert w.sport == "mobility"
    assert w.steps[0].hold_s == 30 and w.steps[0].sets == 3
    # 3×30 s + 2×10 reps × 4 s, plus short transitions; no long gym rests
    assert 3 <= estimate(w).duration_s / 60 <= 5
    assert dsl.parse("Gym: Balance Board 3x45s").steps[0].hold_s == 45


@pytest.mark.parametrize("minutes", [10, 20, 30])
def test_mobility_fills_its_time(minutes):
    _, text = templates.mobility("mobility", "base", minutes, None, variant=2)
    got = estimate(dsl.parse(text)).duration_s / 60
    assert minutes * 0.7 <= got <= minutes * 1.1


def test_no_equipment_swap_removes_gym_machines():
    out = templates.for_equipment(templates.GYM["build"][1] + " | Lat Pulldown 3x10 @ 60kg", "none")
    for word in ("Back Squat", "Barbell", "Pull-ups", "Lat Pulldown", "kg"):
        assert word not in out
    assert "Split Squat" in out and "@ RPE 8" in out
    assert templates.for_equipment("Gym: Back Squat 3x5", "gym") == "Gym: Back Squat 3x5"


def test_fit_gym_respects_budget_and_deload_is_lighter():
    full = templates.GYM["base"][1]
    short = templates.fit_gym(full, 25)
    assert estimate(dsl.parse(short)).duration_s / 60 <= 25 * 1.1
    light = templates.deload("Gym: Back Squat 5x3 @ RPE 8")
    assert light == "Gym: Back Squat 3x3 @ RPE 6"


# ------------------------------------------------------------------ rules
def test_recovery_day_blocks_hard_and_heavy_work():
    hard = _s(MON, "run", "quality", 50, "Run: Warmup 15min @ Z2 -> 4x(4min @ Z5, Rest 2min)")
    legs = _s(MON, "gym", "strength", 45, "Gym: Back Squat 4x5 @ RPE 8")
    easy = _s(MON, "run", "easy", 40, "Run: Easy 40min @ Z2")
    for s in (hard, legs):
        assert any("night shift" in v for v in violations(s, MON, {}, 90, easy_only=True))
    assert not violations(easy, MON, {}, 90, easy_only=True)


def test_outside_training_rules():
    sparring = _s(MON + timedelta(days=1), "other", "other", 90, "", hard=True)
    legs = _s(MON, "gym", "strength", 45, "Gym: Back Squat 4x5 @ RPE 8")
    assert any("day before" in v for v in violations(legs, MON, group_by_day([sparring]), 90))
    # its time is on top of the free time; but a hard Taper session can't share its day
    same_day = _s(MON + timedelta(days=1), "run", "quality", 50, "Run: Easy 50min @ Z2")
    v = violations(same_day, sparring.day, group_by_day([sparring]), 60)
    assert "two hard sessions on the same day" in v and not any("exceeds" in x for x in v)


def test_mobility_does_not_count_toward_two_per_day():
    a = _s(MON, "run", "easy", 40, "Run: Easy 40min @ Z2")
    b = _s(MON, "gym", "strength", 40, "Gym: Bench Press 3x8 @ RPE 7")
    mob = _s(MON, "mobility", "mobility", 15, "Mobility: Cat-Cow 2x10")
    assert not violations(mob, MON, group_by_day([a, b]), 120)


# ------------------------------------------------------------------ planner
def _goal(**kw):
    base = dict(event="custom", race_date=MON + timedelta(weeks=8), start_date=MON, level="intermediate",
                levels={"run": {"stage": 3, "assessed": True}, "gym": {"stage": 2, "assessed": True}})
    base.update(kw)
    return P.Goal(**base)


def test_race_plan_without_strength_has_no_gym():
    g = P.Goal("10k", MON + timedelta(weeks=10), MON, sports=["run"], levels={"run": {"stage": 3, "assessed": True}})
    assert not [s for s in P.generate(g).sessions if s.sport == "gym"]


def test_away_days_get_no_equipment_sessions():
    plan = P.generate(_goal(sports=["run", "gym"], gym_per_week=3, away_days={1, 2, 3},
                            availability={k: 60 for k in range(7)}))
    away = [s for s in plan.sessions if s.sport == "gym" and s.day.weekday() in {1, 2, 3}]
    assert away and all("no equipment" in s.title and "Back Squat" not in s.dsl for s in away)


def test_heavy_legs_never_before_hard_outside_training():
    plan = P.generate(_goal(sports=["gym"], gym_per_week=3, availability={1: 45, 3: 45, 5: 90},
                            commitments=[{"day": d, "minutes": 90, "name": "Kickboxing", "hard": True} for d in (0, 2, 4)]))
    gyms = [s for s in plan.sessions if s.sport == "gym"]
    assert gyms and not [s for s in gyms if s.heavy_legs and s.day.weekday() in (1, 3)]
    assert not any(n.startswith("Skipped") for w in plan.weeks for n in w.notes)


def test_night_shift_days_stay_easy():
    easy = {MON + timedelta(days=d) for d in (1, 2, 8, 9)}
    plan = P.generate(_goal(sports=["run", "gym"], availability={k: 90 for k in range(7)}, easy_dates=easy))
    on = [s for s in plan.sessions if s.day in easy]
    assert on and not [s for s in on if s.hard or s.heavy_legs]


def test_strength_led_deload_keeps_frequency():
    plan = P.generate(_goal(sports=["gym"], gym_per_week=4, race_date=MON + timedelta(weeks=12),
                            availability={0: 90, 2: 90, 4: 90, 6: 120}))
    rec = [w for w in plan.weeks if w.recovery]
    assert rec
    for w in rec:
        week = [s for s in plan.sessions if w.start <= s.day < w.start + timedelta(7)]
        assert len(week) == 4 and all(s.title.startswith("Deload") for s in week)


def test_gran_fondo_is_a_bike_race():
    assert P.EVENTS["gran_fondo"]["race"] == ["Bike: Race 160km @ Z2"]
    dsl.parse(P.EVENTS["gran_fondo"]["race"][0])


# ------------------------------------------------------------------ calendar
NIGHT_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
UID:night-1@google.com
DTSTART;TZID=America/Toronto:20261124T190000
DTEND;TZID=America/Toronto:20261125T070000
SUMMARY:Night shift
END:VEVENT
BEGIN:VEVENT
UID:day-1@google.com
DTSTART;TZID=America/Toronto:20261127T070000
DTEND;TZID=America/Toronto:20261127T190000
SUMMARY:Day shift
END:VEVENT
END:VCALENDAR
"""


def test_recovery_days_detected_after_night_shifts_only():
    busy = C.parse_ics(NIGHT_ICS, date(2026, 11, 23), date(2026, 11, 30), "America/Toronto")
    assert C.recovery_days(busy, "America/Toronto") == {date(2026, 11, 25)}


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/api/auth/register", json={"email": f"life-{uuid.uuid4().hex[:8]}@example.com", "name": "Test", "password": "supersecret"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_api_plan_with_new_options_and_adapt(client, auth):
    assert any(e["key"] == "gran_fondo" and e["sports"] == ["bike"] for e in client.get("/api/events").json())
    r = client.post("/api/plans", headers=auth, json={
        "event": "custom", "sports": ["gym", "mobility", "walk"], "block_weeks": 8, "level": "beginner",
        "equipment": "home", "gym_per_week": 2, "availability": {str(k): 45 for k in range(7)},
        "commitments": [{"day": 5, "minutes": 60, "name": "Soccer", "hard": True}]})
    assert r.status_code == 200, r.text
    assert r.json()["goal"]["commitments"][0]["name"] == "Soccer"
    start = date.today().isoformat()
    end = (date.today() + timedelta(weeks=8)).isoformat()
    ss = client.get("/api/sessions", headers=auth, params={"start": start, "end": end}).json()
    assert {s["sport"] for s in ss} == {"gym", "mobility", "walk"}
    gym = next(s for s in ss if s["sport"] == "gym")
    a = client.post(f"/api/sessions/{gym['id']}/adapt", headers=auth, json={"equipment": "none", "minutes": 20}).json()
    assert a["adapted"] and a["minutes"] <= 22 and "Dumbbell" not in a["dsl"]
    back = client.post(f"/api/sessions/{gym['id']}/restore", headers=auth).json()
    assert back["dsl"] == gym["dsl"] and not back["adapted"]
    assert client.get("/api/today", headers=auth).status_code == 200      # Today works with every sport
    walk = next(s for s in ss if s["sport"] == "walk")
    short = client.post(f"/api/sessions/{walk['id']}/adapt", headers=auth, json={"minutes": 15}).json()
    assert 13 <= short["minutes"] <= 17
    bad = client.post("/api/plans", headers=auth, json={"event": "custom", "sports": ["ski"]})
    assert bad.status_code == 422
