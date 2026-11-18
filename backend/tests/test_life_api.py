"""End-to-end tests for nutrition, food log, weight, checklist and calendar endpoints.

External services (food databases, calendar host) are replaced by stubs; the
stubs return documented response shapes (see test_foods.py).
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import foods as F
from app import life
from app.main import app

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
UID:c1@google.com
DTSTART;TZID=America/Toronto:20261102T080000
DTEND;TZID=America/Toronto:20261102T200000
RRULE:FREQ=WEEKLY;BYDAY=TU
SUMMARY:IFT-2008 Cours + labo
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def h(client):
    r = client.post("/api/auth/register", json={"email": "life@example.com", "name": "Ali", "password": "supersecret"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_nutrition_requires_real_body_data(client, h):
    r = client.get("/api/nutrition/day", headers=h).json()
    assert set(r["setup_needed"]) == {"sex", "birth_year", "height_cm", "weight"}


def test_settings_validation(client, h):
    assert client.put("/api/settings", headers=h, json={"body": {"sex": "x"}}).status_code == 422
    assert client.put("/api/settings", headers=h, json={"gym_routines": [{"name": "Push", "dsl": "Run: 10min"}]}).status_code == 422
    r = client.put("/api/settings", headers=h, json={
        "body": {"sex": "male", "birth_year": 2002, "height_cm": 178},
        "nutrition": {"mode": "recomp", "protein_g_per_kg": 2.25},
        "gym_routines": [{"name": "Push", "dsl": "Gym: Bench Press 4x8 @ RPE 8", "minutes": 90}]})
    assert r.status_code == 200 and r.json()["schedule"]["tz"] == "America/Toronto"


def test_weight_and_full_nutrition_day(client, h):
    for i in range(10):
        client.put("/api/weights", headers=h, json={"day": (date.today() - timedelta(i)).isoformat(), "kg": 80 + i * 0.05})
    w = client.get("/api/weights", headers=h).json()
    assert len(w["entries"]) == 10 and w["trend"]
    client.post("/api/plans", headers=h, json={"event": "half_marathon", "race_date": (date.today() + timedelta(weeks=20)).isoformat(),
                                               "level": "beginner", "baseline_hours": 4, "gym_per_week": 1})
    n = client.get("/api/nutrition/day", headers=h).json()
    t = n["target"]
    assert t["protein_g"] == round(2.25 * n["expenditure"]["weight_kg"])
    assert abs(4 * t["protein_g"] + 4 * t["carbs_g"] + 9 * t["fat_g"] - t["kcal"]) <= 10
    assert n["tomorrow"]["target"]["kcal"] > 0 and len(n["week"]) == 7
    assert n["expenditure"]["confidence"] == 0          # not enough logged days yet → prior only


def test_quick_add_and_custom_food_log(client, h):
    d = date.today().isoformat()
    assert client.post("/api/log", headers=h, json={"day": d, "meal": "lunch", "name": "Poutine (resto)"}).status_code == 422
    client.post("/api/log", headers=h, json={"day": d, "meal": "lunch", "name": "Poutine (resto)", "kcal": 740, "protein": 25})
    f = client.post("/api/foods", headers=h, json={"name": "Whey isolate", "basis_g": 30, "kcal": 110, "protein": 25,
                                                   "carbs": 1, "fat": 0.5, "serving_g": 30, "serving_label": "1 scoop"}).json()
    assert f["per100"]["protein"] == pytest.approx(83.33, abs=0.01)
    e = client.post("/api/log", headers=h, json={"day": d, "meal": "snack", "food_id": f["id"], "grams": 30}).json()
    assert e["kcal"] == 110 and e["protein"] == 25
    log = client.get("/api/log", headers=h).json()
    assert log["totals"]["kcal"] == 850 and log["totals"]["protein"] == 50
    assert client.get("/api/foods/recent", headers=h).json()[0]["name"] == "Whey isolate"
    assert client.post("/api/log/copy", headers=h, json={"from_day": d, "to_day": (date.today() + timedelta(1)).isoformat(),
                                                         "meal": "snack"}).json()["copied"] == 1


def test_search_merges_sources_and_reports_failures(client, h, monkeypatch):
    async def off(client, q, limit=15):
        return [F.parse_off_product({"code": "3017624010701", "product_name": "Nutella", "brands": "Ferrero",
                                     "nutriments": {"energy-kcal_100g": 539, "proteins_100g": 6.3,
                                                    "carbohydrates_100g": 57.5, "fat_100g": 30.9}})]

    async def usda(client, q, limit=15):
        raise RuntimeError("USDA rate limit reached — set your own free USDA_API_KEY")

    async def cnf_index(client):
        return [(4, "Pomme, crue", "Apple, raw"), (5, "Nutella-like spread", None)]

    monkeypatch.setattr(F, "off_search", off)
    monkeypatch.setattr(F, "usda_search", usda)
    monkeypatch.setattr(F, "cnf_index", cnf_index)
    r = client.get("/api/foods/search", headers=h, params={"q": "nutella"}).json()
    assert r["sources"]["off"] == "ok" and r["sources"]["cnf"] == "ok" and "USDA" in r["sources"]["usda"]
    assert r["results"]["off"][0]["per100"]["kcal"] == 539 and r["results"]["off"][0]["id"]
    assert r["results"]["cnf"][0]["source_id"] == "5"


def test_checklist_auto_items_habits_and_score(client, h):
    hb = client.post("/api/habits", headers=h, json={"label": "Read 20 pages"}).json()
    c = client.get("/api/checklist", headers=h).json()
    keys = {i["key"] for i in c["items"]}
    assert {"protein", "kcal", "food_complete", "weigh", f"habit:{hb['id']}"} <= keys
    assert next(i for i in c["items"] if i["key"] == "weigh")["done"]
    assert len(c["history"]) == 14
    d = date.today().isoformat()
    after = client.post(f"/api/checklist/{d}", headers=h, json={"key": f"habit:{hb['id']}", "done": True}).json()
    assert after["done"] == c["done"] + 1
    assert client.post(f"/api/checklist/{d}", headers=h, json={"key": "protein", "done": True}).status_code == 422


def test_calendar_import_shapes_the_plan(client, h, monkeypatch):
    async def fake_fetch(src):
        src.ics, src.last_error = ICS, None
    monkeypatch.setattr(life, "fetch_calendar", fake_fetch)
    monkeypatch.setattr(life, "validate_calendar_url", lambda u: u)
    assert client.post("/api/calendars", headers=h, json={"url": "https://calendar.google.com/calendar/ical/x/private-abc/basic.ics"}).status_code == 200
    src = client.get("/api/calendars", headers=h).json()[0]
    assert "private" not in src["url_hint"] and src["ok"]
    wk = client.get("/api/calendar/week", headers=h).json()
    tue = next(x for x in wk["days"] if date.fromisoformat(x["date"]).weekday() == 1)
    # 08:00-20:00 class padded by 15 min → only 06:30-07:45 and 20:15-21:30 remain
    assert [x[0] for x in tue["windows"]] == ["06:30", "20:15"] and tue["longest_min"] == 75
    assert client.post("/api/plans/replan", headers=h).status_code == 200
    start = date.today() + timedelta(days=7 - date.today().weekday())
    rows = client.get("/api/sessions", headers=h, params={"start": start.isoformat(),
                                                          "end": (start + timedelta(27)).isoformat()}).json()
    assert rows and not any(date.fromisoformat(s["day"]).weekday() == 1 and s["minutes"] > 75 for s in rows)
    timed = [s for s in rows if s.get("start_time")]
    assert timed


def test_calendar_url_validation():
    with pytest.raises(ValueError):
        life.validate_calendar_url("http://calendar.google.com/x.ics")
    with pytest.raises(ValueError):
        life.validate_calendar_url("https://127.0.0.1/cal.ics")


def _custom(client, h, name, kcal, p, c, f, basis=100):
    return client.post("/api/foods", headers=h, json={"name": name, "basis_g": basis, "kcal": kcal, "protein": p,
                                                      "carbs": c, "fat": f}).json()


def test_recipe_from_ingredients_with_cooked_weight(client, h):
    rice = _custom(client, h, "Riz basmati sec", 360, 8, 79, 1)          # values from the user's own package
    chicken = _custom(client, h, "Poitrine de poulet crue", 120, 23, 0, 2.5)
    sauce = _custom(client, h, "Sauce teriyaki (sachet)", 90, 1.5, 19, 0.5, basis=50)
    body = {"name": "Bol poulet teriyaki", "servings": 4, "cooked_weight_g": 1600,
            "ingredients": [{"food_id": rice["id"], "grams": 300}, {"food_id": chicken["id"], "grams": 600},
                            {"food_id": sauce["id"], "grams": 100}]}
    prev = client.post("/api/recipes/preview", headers=h, json=body).json()
    total_kcal = 360 * 3 + 120 * 6 + 180
    assert prev["per100"]["kcal"] == pytest.approx(total_kcal / 16, rel=1e-3)
    assert prev["servings"][0] == {"label": "1 serving", "grams": 400.0}
    r = client.post("/api/recipes", headers=h, json=body).json()
    e = client.post("/api/log", headers=h, json={"day": date.today().isoformat(), "meal": "dinner",
                                                 "food_id": r["id"], "grams": 400}).json()
    assert e["kcal"] == round(total_kcal / 4)
    assert e["protein"] == pytest.approx((24 + 138 + 3) / 4, abs=0.1)
    assert any(f["id"] == r["id"] for f in client.get("/api/myfoods", headers=h).json())
    bad = {**body, "ingredients": [{"food_id": r["id"], "grams": 10}]}
    assert client.put(f"/api/recipes/{r['id']}", headers=h, json=bad).status_code == 422


def test_editing_a_food_never_rewrites_history(client, h):
    f = _custom(client, h, "Granola maison", 450, 10, 60, 18)
    d = (date.today() - timedelta(days=2)).isoformat()
    client.post("/api/log", headers=h, json={"day": d, "meal": "breakfast", "food_id": f["id"], "grams": 50})
    client.put(f"/api/foods/{f['id']}", headers=h, json={"name": "Granola maison v2", "basis_g": 100, "kcal": 500,
                                                         "protein": 12, "carbs": 60, "fat": 22})
    client.delete(f"/api/foods/{f['id']}", headers=h)
    log = client.get("/api/log", headers=h, params={"d": d}).json()
    entry = next(e for e in log["meals"]["breakfast"] if e["name"].startswith("Granola"))
    assert entry["kcal"] == 225                                     # still the old 450 kcal/100 g
    assert not any(x["id"] == f["id"] for x in client.get("/api/myfoods", headers=h).json())


def test_export_import_round_trip(client, h):
    before = client.get("/api/export", headers=h).json()
    assert before["taper_backup"] == 1 and before["food_entries"] and before["weights"]
    other = client.post("/api/auth/register", json={"email": "restore@example.com", "name": "R", "password": "supersecret"})
    h2 = {"Authorization": f"Bearer {other.json()['token']}"}
    assert client.post("/api/import", headers=h2, json={"data": before}).status_code == 422     # needs confirmation
    r = client.post("/api/import", headers=h2, json={"data": before, "confirm_replace": True})
    assert r.status_code == 200, r.text
    after = client.get("/api/export", headers=h2).json()
    for k in ("sessions", "food_entries", "weights", "habits", "day_logs"):
        assert len(after[k]) == len(before[k]), k
    assert sum(e["kcal"] for e in after["food_entries"]) == pytest.approx(sum(e["kcal"] for e in before["food_entries"]))
    recipe = next(f for f in after["foods"] if f["source"] == "recipe")
    ids = {f["id"] for f in after["foods"]}
    assert all(i["food_id"] in ids for i in recipe["ingredients"])  # ingredient links remapped


def test_storage_info(client, h):
    s = client.get("/api/storage", headers=h).json()
    assert s["database"].endswith(".db") and s["backup_count"] >= 1 and "copy a file" in s["restore"]


def test_migration_adds_new_columns_to_old_databases(tmp_path):
    import sqlite3

    from sqlalchemy import create_engine, inspect

    from app import db as dbmod
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE foods (id INTEGER PRIMARY KEY, source VARCHAR(10), source_id VARCHAR(64), name VARCHAR(255))")
    con.execute("INSERT INTO foods (source, source_id, name) VALUES ('custom', 'x', 'Old food')")
    con.commit()
    con.close()
    old_engine = dbmod.engine
    dbmod.engine = create_engine(f"sqlite:///{path}")
    try:
        added = dbmod.add_missing_columns()
    finally:
        dbmod.engine = old_engine
    assert "foods.ingredients" in added and "foods.hidden" in added
    cols = {c["name"] for c in inspect(create_engine(f"sqlite:///{path}")).get_columns("foods")}
    assert {"kcal", "ingredients", "recipe_servings"} <= cols
    assert sqlite3.connect(path).execute("SELECT name FROM foods").fetchone()[0] == "Old food"


def test_daily_backup_is_consistent_and_rotated(tmp_path, monkeypatch):
    import sqlite3

    from app import storage
    db = tmp_path / "taper.db"
    sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.commit()
    monkeypatch.setattr(storage, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    for i in range(35):
        storage.daily_backup(f"sqlite:///{db}", date(2026, 1, 1) + timedelta(days=i))
    files = sorted((tmp_path / "backups").glob("taper-*.db"))
    assert len(files) == storage.KEEP_BACKUPS and files[-1].name == "taper-2026-02-04.db"


def test_level_check_flow_places_and_replans(client):
    r = client.post("/api/auth/register", json={"email": "newbie@example.com", "name": "N", "password": "supersecret"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    client.put("/api/settings", headers=h, json={"body": {"sex": "male", "birth_year": 2002, "height_cm": 178}})
    race = (date.today() + timedelta(weeks=34)).isoformat()
    client.post("/api/plans", headers=h, json={"event": "half_marathon", "race_date": race, "level": "beginner",
                                               "baseline_hours": 1, "gym_per_week": 0})
    lv = client.get("/api/levels", headers=h).json()["sports"]["run"]
    assert not lv["assessed"] and lv["pending_check"]["kind"] == "test" and lv["check"]["instructions"]
    sid = lv["pending_check"]["id"]
    assert client.post("/api/levels/run/assess", headers=h, json={"session_id": sid}).status_code == 422
    out = client.post("/api/levels/run/assess", headers=h, json={"session_id": sid, "minutes": 20, "continuous_min": 3,
                                                                  "distance_km": 1.9, "rpe": 6}).json()
    assert out["stage"] == 0 and out["name"] == "First steps" and out["replanned"]
    lv = client.get("/api/levels", headers=h).json()["sports"]["run"]
    assert lv["assessed"] and lv["pending_check"] is None and lv["projection"]["on_track"]
    start = date.today() + timedelta(days=7 - date.today().weekday())
    nxt = client.get("/api/sessions", headers=h, params={"start": start.isoformat(),
                                                          "end": (start + timedelta(6)).isoformat()}).json()
    runs = [s for s in nxt if s["sport"] == "run"]
    assert runs and all("Walk" in s["dsl"] for s in runs) and len(runs) <= 3


def test_weekly_review_moves_athlete_up(client, monkeypatch):
    from app import levels as LV
    from app.db import SessionLocal, User
    r = client.post("/api/auth/register", json={"email": "climber@example.com", "name": "C", "password": "supersecret"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    db = SessionLocal()
    user = db.query(User).filter_by(email="climber@example.com").one()
    monday = date.today() - timedelta(days=date.today().weekday())
    start = monday - timedelta(weeks=3)
    LV._save(db, user, {"run": {"stage": 0, "progress": 0.0, "assessed": True,
                                "evaluated_through": start.isoformat(), "history": []}})
    monkeypatch.setattr(LV, "_replan", lambda db, user: True)
    for w in range(3):                                  # three good weeks of run-walks, easy effort
        for d in (0, 2, 4):
            day = (start + timedelta(weeks=w, days=d)).isoformat()
            s = client.post("/api/sessions", headers=h, json={"day": day, "dsl": "Run: 5min Walk @ Z1 -> 6x(1min @ Z2, 2min Walk @ Z1)"}).json()
            client.post(f"/api/sessions/{s['id']}/complete", headers=h, json={"rpe": 5})
    db.close()
    changes = client.get("/api/today", headers=h).json()["level_changes"]
    assert len(changes) == 3 and changes[-1]["stage_changed"]
    lv = client.get("/api/levels", headers=h).json()["sports"]["run"]
    assert lv["stage"] == 1 and lv["history"][0]["event"] == "week review"
    assert client.get("/api/today", headers=h).json()["level_changes"] == []     # each week reviewed once


def test_manual_stage_and_gym_questionnaire(client, h):
    assert client.put("/api/levels/run", headers=h, json={"stage": 9}).status_code == 422
    assert client.put("/api/levels/run", headers=h, json={"stage": 4}).status_code == 200
    g = client.post("/api/levels/gym/assess", headers=h, json={"months_lifting": 10}).json()
    assert g["stage"] == 2 and g["name"] == "Strength"


def test_flexible_goals_and_target_time(client):
    r = client.post("/api/auth/register", json={"email": "flex@example.com", "name": "F", "password": "supersecret"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    races = {e["key"]: e for e in client.get("/api/events").json()}
    assert "general" not in races and races["olympic_tri"]["sports"] == ["swim", "bike", "run"]
    assert races["half_marathon"]["target_time"] and not races["olympic_tri"]["target_time"]
    assert client.post("/api/plans/preview", headers=h, json={"event": "custom", "sports": []}).status_code == 422
    assert client.post("/api/plans/preview", headers=h, json={"event": "custom", "sports": ["ski"]}).status_code == 422
    assert client.post("/api/plans/preview", headers=h, json={"event": "half_marathon"}).status_code == 422   # needs a date
    p = client.post("/api/plans/preview", headers=h, json={"event": "custom", "sports": ["gym", "walk"], "body_focus": True,
                                                           "block_weeks": 12, "gym_per_week": 3}).json()
    assert p["projection"]["event_name"] == "Strength + Walking" and len(p["weeks"]) >= 12
    assert {s["sport"] for s in p["sample"]} == {"gym", "walk"}
    race = (date.today() + timedelta(weeks=16)).isoformat()
    t = client.post("/api/plans/preview", headers=h, json={"event": "half_marathon", "race_date": race, "sports": ["run"],
                                                           "target_time": "1:45:00"}).json()["projection"]["target"]
    assert t["race_pace"] == 299 and t["threshold_pace"] < t["race_pace"]


def test_goal_weight_by_date_sets_a_safe_rate(client):
    r = client.post("/api/auth/register", json={"email": "cut@example.com", "name": "A", "password": "supersecret"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    client.put("/api/settings", headers=h, json={"body": {"sex": "male", "birth_year": 2003, "height_cm": 180},
                                                 "nutrition": {"mode": "lose", "goal_weight_kg": 78,
                                                               "goal_date": (date.today() + timedelta(weeks=24)).isoformat()}})
    client.put("/api/weights", headers=h, json={"day": date.today().isoformat(), "kg": 88})
    wg = client.get("/api/nutrition/day", headers=h).json()["weight_goal"]
    assert wg["mode"] == "lose" and wg["rate"] == pytest.approx(-10 / 88 / 24 * 100, abs=0.02) and not wg["capped"]
    client.put("/api/settings", headers=h, json={"nutrition": {"goal_date": (date.today() + timedelta(weeks=4)).isoformat()}})
    wg = client.get("/api/nutrition/day", headers=h).json()["weight_goal"]
    assert wg["capped"] and wg["rate"] == -1.0 and wg["eta"]                 # 10 kg in 4 weeks is refused
