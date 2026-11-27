from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.engine import study as S
from app.main import app

Z = ZoneInfo("America/Toronto")


def _days(start: date, n: int, busy=None, lo="08:00", hi="20:00"):
    out = {}
    for i in range(n):
        d = start + timedelta(days=i)
        a = datetime.combine(d, datetime.strptime(lo, "%H:%M").time(), Z)
        b = datetime.combine(d, datetime.strptime(hi, "%H:%M").time(), Z)
        out[d] = (a, b, [x for x in (busy or []) if x.start.date() == d])
    return out


def test_parse_lines_formats():
    rows, errors = S.parse_lines(
        "2026-12-11 18:30 | Intelligence artificielle | Examen final | 30\n"
        "Dec 13 13h30 | Algo | Examen final | 35 | 20h\n"
        "# comment\n"
        "10 déc | Algo | TP3 | 10\n"
        "Lun 7 déc 23:55 | IA | Questionnaire #5 | 3\n"
        "nonsense\n", 2026)
    assert [r["kind"] for r in rows] == ["exam", "exam", "assignment", "quiz"]
    assert rows[0]["due_date"] == "2026-12-11" and rows[0]["due_time"] == "18:30" and rows[0]["weight"] == 30
    assert rows[1]["due_date"] == "2026-12-13" and rows[1]["due_time"] == "13:30" and rows[1]["hours"] == 20
    assert rows[2]["due_date"] == "2026-12-10" and rows[2]["due_time"] == "23:59"
    assert rows[3]["due_date"] == "2026-12-07" and rows[3]["due_time"] == "23:55"
    assert len(errors) == 1


def test_blocks_end_before_due_and_cover_hours():
    start = date(2026, 12, 1)
    exam = S.Deadline(1, "IA", "Final", "exam", datetime(2026, 12, 4, 9, 0, tzinfo=Z), 8, 30)
    tp = S.Deadline(2, "BD", "TP3", "assignment", datetime(2026, 12, 2, 17, 0, tzinfo=Z), 4, 10)
    blocks = S.plan([exam, tp], _days(start, 4))
    cov = S.coverage([exam, tp], blocks)
    assert cov[2] >= 4 * 60 and cov[1] >= 8 * 60
    for b in blocks:
        d = exam if b.deadline_id == 1 else tp
        gap = (15 if b.start.date() == d.due.date() else 60) if d.kind == "exam" else 0   # exam-day last look
        limit = d.due - timedelta(minutes=gap)
        assert b.end <= limit
    # nothing overlaps and there is a break between blocks
    ordered = sorted(blocks, key=lambda b: b.start)
    for a, b in zip(ordered, ordered[1:], strict=False):
        assert b.start >= a.end + timedelta(minutes=S.BREAK_MIN) or b.start.date() != a.start.date()


def test_busy_time_and_daily_cap_respected():
    start = date(2026, 12, 1)
    busy = [S.Interval(datetime(2026, 12, 1, 9, 0, tzinfo=Z), datetime(2026, 12, 1, 15, 0, tzinfo=Z))]
    exam = S.Deadline(1, "ASD", "Final", "exam", datetime(2026, 12, 5, 13, 30, tzinfo=Z), 40, 35)
    blocks = S.plan([exam], _days(start, 4, busy), S.Options(max_per_day_min=240))
    for b in blocks:
        assert not (b.start < busy[0].end and b.end > busy[0].start)
    per_day = {}
    for b in blocks:
        per_day[b.start.date()] = per_day.get(b.start.date(), 0) + b.minutes
    assert max(per_day.values()) <= 240


def test_tight_deadline_goes_first():
    """When work due soon nearly fills the time before it, it isn't starved by a heavier exam."""
    start = date(2026, 12, 1)
    exam = S.Deadline(1, "ASD", "Final", "exam", datetime(2026, 12, 8, 9, 0, tzinfo=Z), 20, 35)
    tp = S.Deadline(2, "ASD", "TP3", "assignment", datetime(2026, 12, 2, 14, 0, tzinfo=Z), 9, 10)
    blocks = S.plan([exam, tp], _days(start, 7, lo="08:00", hi="17:00"), S.Options(max_per_day_min=360))
    assert S.coverage([exam, tp], blocks)[2] >= 9 * 60 - 45


def test_training_slot_kept():
    start = date(2026, 12, 1)
    exam = S.Deadline(1, "IA", "Final", "exam", datetime(2026, 12, 2, 18, 0, tzinfo=Z), 30, 30)
    days = _days(start, 1, lo="08:00", hi="20:00")
    blocks = S.plan([exam], days, S.Options(max_per_day_min=720, keep_for_training={start: 120}))
    taken = sorted((b.start, b.end) for b in blocks)
    lo, hi, _ = days[start]
    gaps, cur = [], lo
    for a, b in taken:
        gaps.append((a - cur).total_seconds() / 60)
        cur = b
    gaps.append((hi - cur).total_seconds() / 60)
    assert max(gaps) >= 120


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/api/auth/register", json={"email": "study@example.com", "name": "Ali", "password": "supersecret"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:w@t
DTSTART;TZID=America/Toronto:{d}T080000
DTEND;TZID=America/Toronto:{d}T150000
RRULE:FREQ=DAILY;COUNT=20
SUMMARY:RAMQ
END:VEVENT
END:VCALENDAR
"""


def test_study_api_flow(client, auth):
    from app.db import CalendarSource, SessionLocal, User
    today = date.today()
    db = SessionLocal()
    me = client.get("/api/me", headers=auth).json()
    u = db.scalar(__import__("sqlalchemy").select(User).where(User.email == me["email"]))
    db.add(CalendarSource(user_id=u.id, name="t", url="https://example.com/x.ics", ics=ICS.format(d=f"{today:%Y%m%d}")))
    db.commit()
    db.close()

    d1, d2 = today + timedelta(days=4), today + timedelta(days=6)
    text = f"{d1.isoformat()} 18:30 | Intelligence artificielle | Examen final | 30 | 6h\n" \
           f"{d2.isoformat()} 23:59 | Bases de données | TP3 | 10"
    r = client.post("/api/study/import", headers=auth, json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 2 and r.json()["plan"]["blocks"] > 0
    # importing again updates, no duplicates
    assert client.post("/api/study/import", headers=auth, json={"text": text}).json()["added"] == 0

    o = client.get("/api/study", headers=auth).json()
    assert [d["course"] for d in o["deadlines"]][:2] == ["Intelligence artificielle", "Bases de données"]
    ia = o["deadlines"][0]
    assert ia["planned_min"] >= 5 * 60
    # blocks never overlap work (08:00-15:00 + travel buffer)
    for b in o["blocks"]:
        if b["day"] > today.isoformat():
            assert not ("07:45" < b["end_time"] and b["start_time"] < "15:15"), b

    # ticking a block off from the daily checklist
    b = next(x for x in o["blocks"] if x["day"] >= today.isoformat())
    r = client.post(f"/api/checklist/{b['day']}", headers=auth, json={"key": f"study:{b['id']}", "done": True})
    assert r.status_code == 200, r.text
    items = client.get(f"/api/checklist?d={b['day']}", headers=auth).json()["items"]
    assert any(i["key"] == f"study:{b['id']}" and i["done"] for i in items)

    # done time stays after a rebuild and counts toward the goal
    client.post("/api/study/plan", headers=auth)
    o2 = client.get("/api/study", headers=auth).json()
    assert next(d for d in o2["deadlines"] if d["id"] == ia["id"])["done_min"] == b["minutes"]

    # training avoids revision blocks
    s = client.post("/api/sessions", headers=auth, json={"day": b["day"], "dsl": "Run: Easy 40min @ Z2"}).json()
    from app import life
    db = SessionLocal()
    u = db.get(User, u.id)
    life.assign_start_times(db, u, date.fromisoformat(b["day"]), date.fromisoformat(b["day"]))
    db.close()
    rows = client.get("/api/sessions", headers=auth, params={"start": b["day"], "end": b["day"]}).json()
    run = next(x for x in rows if x["id"] == s["id"])
    if run.get("start_time"):
        blocks_today = [x for x in o2["blocks"] if x["day"] == b["day"]]
        h, m = map(int, run["start_time"].split(":"))
        end = f"{(h * 60 + m + 40) // 60:02d}:{(h * 60 + m + 40) % 60:02d}"
        assert all(not (run["start_time"] < x["end_time"] and x["start_time"] < end) for x in blocks_today)

    # ICS feed carries the blocks
    feed = client.get(f"/api/calendar/{me['ics_token']}.ics").text
    assert "taper-study-" in feed and "CATEGORIES:STUDY" in feed

    # deleting a deadline removes its blocks
    client.delete(f"/api/study/deadlines/{ia['id']}", headers=auth)
    o3 = client.get("/api/study", headers=auth).json()
    assert all(x["deadline_id"] != ia["id"] for x in o3["blocks"])

    # backup round trip keeps deadlines
    dump = client.get("/api/export", headers=auth).json()
    assert dump["deadlines"] and "study_blocks" in dump


def test_study_during_capped_hours():
    from app.study import _keywords
    assert _keywords("RAMQ:4, bus") == [("ramq", 4.0), ("bus", None)]
    assert _keywords("RAMQ: 3.5h") == [("ramq", 3.5)]
