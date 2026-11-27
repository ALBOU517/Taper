"""Full export / import of one account as plain JSON (portable, human-readable)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .db import CalendarSource, DayLog, Deadline, Food, FoodEntry, Habit, Plan, Session, StudyBlock, User, WeightEntry

VERSION = 1
SESSION_FIELDS = ("day", "sport", "kind", "title", "dsl", "minutes", "tss", "distance_m", "status", "actual_minutes",
                  "actual_tss", "actual_distance_m", "rpe", "source", "external_id", "note", "start_time")
FOOD_FIELDS = ("source", "source_id", "name", "brand", "barcode", "kcal", "protein", "carbs", "fat", "fiber", "sugar",
               "sodium_mg", "servings", "ingredients", "recipe_weight_g", "recipe_servings", "hidden")
ENTRY_FIELDS = ("day", "meal", "name", "grams", "kcal", "protein", "carbs", "fat")


def _ser(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _row(obj, fields) -> dict:
    return {f: _ser(getattr(obj, f)) for f in fields}


def export_user(db: DB, user: User) -> dict:
    entries = list(db.scalars(select(FoodEntry).where(FoodEntry.user_id == user.id)))
    food_ids = {e.food_id for e in entries if e.food_id}
    owned = list(db.scalars(select(Food).where(Food.owner_id == user.id)))
    for f in owned:
        food_ids |= {int(i["food_id"]) for i in (f.ingredients or [])}
    foods = {f.id: f for f in owned}
    for fid in food_ids - set(foods):
        f = db.get(Food, fid)
        if f:
            foods[fid] = f
    return {
        "taper_backup": VERSION, "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": {"name": user.name, "email": user.email, "settings": user.settings, "thresholds": user.thresholds,
                 "availability": user.availability},
        "plans": [{"goal": p.goal, "weeks": p.weeks, "projection": p.projection, "active": p.active, "id": p.id}
                  for p in db.scalars(select(Plan).where(Plan.user_id == user.id))],
        "sessions": [{**_row(s, SESSION_FIELDS), "plan_id": s.plan_id}
                     for s in db.scalars(select(Session).where(Session.user_id == user.id).order_by(Session.day))],
        "foods": [{**_row(f, FOOD_FIELDS), "id": f.id, "owned": f.owner_id == user.id} for f in foods.values()],
        "food_entries": [{**_row(e, ENTRY_FIELDS), "food_id": e.food_id} for e in entries],
        "weights": [{"day": w.day.isoformat(), "kg": w.kg}
                    for w in db.scalars(select(WeightEntry).where(WeightEntry.user_id == user.id))],
        "day_logs": [{"day": d.day.isoformat(), "food_complete": d.food_complete, "reflection": d.reflection,
                      "checks": d.checks} for d in db.scalars(select(DayLog).where(DayLog.user_id == user.id))],
        "habits": [{"label": h.label, "weekdays": h.weekdays, "active": h.active, "sort": h.sort, "id": h.id}
                   for h in db.scalars(select(Habit).where(Habit.user_id == user.id))],
        "calendars": [{"name": c.name, "url": c.url, "ics": c.ics}
                      for c in db.scalars(select(CalendarSource).where(CalendarSource.user_id == user.id))],
        "deadlines": [{**_row(d, DEADLINE_FIELDS), "id": d.id}
                      for d in db.scalars(select(Deadline).where(Deadline.user_id == user.id))],
        "study_blocks": [_row(b, BLOCK_FIELDS)
                         for b in db.scalars(select(StudyBlock).where(StudyBlock.user_id == user.id))],
    }


DEADLINE_FIELDS = ("course", "title", "kind", "due_date", "due_time", "end_time", "weight", "hours", "location", "note",
                   "done")
BLOCK_FIELDS = ("deadline_id", "day", "start_time", "minutes", "status", "source")


def _d(v):
    return date.fromisoformat(v) if isinstance(v, str) else v


def import_user(db: DB, user: User, data: dict) -> dict:
    """Replace this account's data with a backup. Runs in one transaction."""
    if data.get("taper_backup") != VERSION:
        raise ValueError("unsupported backup version")
    for model in (StudyBlock, Deadline, FoodEntry, WeightEntry, DayLog, Habit, CalendarSource, Session):
        for row in db.scalars(select(model).where(model.user_id == user.id)):
            db.delete(row)
    for p in db.scalars(select(Plan).where(Plan.user_id == user.id)):
        db.delete(p)
    db.flush()
    u = data["user"]
    user.settings, user.thresholds, user.availability = u.get("settings") or {}, u.get("thresholds") or {}, \
        u.get("availability") or {}

    plan_map = {}
    for p in data.get("plans", []):
        row = Plan(user_id=user.id, goal=p["goal"], weeks=p["weeks"], projection=p["projection"], active=p["active"])
        db.add(row)
        db.flush()
        plan_map[p["id"]] = row.id

    food_map: dict[int, int] = {}
    for f in sorted(data.get("foods", []), key=lambda x: x["source"] == "recipe"):
        source_id = f["source_id"]
        existing = db.scalar(select(Food).where(Food.source == f["source"], Food.source_id == source_id))
        if existing and existing.owner_id in (None, user.id):
            row = existing
        else:
            if existing:                       # someone else's custom food with the same id: give ours a new one
                source_id = f"{source_id}-u{user.id}"
            row = Food(source=f["source"], source_id=source_id)
            db.add(row)
        for k in FOOD_FIELDS:
            setattr(row, k, f.get(k))
        row.source_id = source_id
        row.owner_id = user.id if f.get("owned") else None
        db.flush()
        food_map[f["id"]] = row.id
    for f in db.scalars(select(Food).where(Food.owner_id == user.id, Food.source == "recipe")):
        f.ingredients = [{**i, "food_id": food_map.get(int(i["food_id"]), i["food_id"])} for i in (f.ingredients or [])]

    for s in data.get("sessions", []):
        db.add(Session(user_id=user.id, plan_id=plan_map.get(s.get("plan_id")),
                       **{k: (_d(s[k]) if k == "day" else s.get(k)) for k in SESSION_FIELDS}))
    for e in data.get("food_entries", []):
        db.add(FoodEntry(user_id=user.id, food_id=food_map.get(e.get("food_id")),
                         **{k: (_d(e[k]) if k == "day" else e.get(k)) for k in ENTRY_FIELDS}))
    for w in data.get("weights", []):
        db.add(WeightEntry(user_id=user.id, day=_d(w["day"]), kg=w["kg"]))
    habit_map = {}
    for h in data.get("habits", []):
        row = Habit(user_id=user.id, label=h["label"], weekdays=h["weekdays"], active=h["active"], sort=h.get("sort", 0))
        db.add(row)
        db.flush()
        habit_map[str(h["id"])] = row.id
    for d in data.get("day_logs", []):
        checks = {}
        for k, v in (d.get("checks") or {}).items():
            if k.startswith("habit:") and k[6:] in habit_map:
                k = f"habit:{habit_map[k[6:]]}"
            checks[k] = v
        db.add(DayLog(user_id=user.id, day=_d(d["day"]), food_complete=d["food_complete"],
                      reflection=d.get("reflection"), checks=checks))
    for c in data.get("calendars", []):
        db.add(CalendarSource(user_id=user.id, name=c["name"], url=c["url"], ics=c.get("ics")))
    deadline_map = {}
    for d in data.get("deadlines", []):
        row = Deadline(user_id=user.id, **{k: (_d(d[k]) if k == "due_date" else d.get(k)) for k in DEADLINE_FIELDS})
        db.add(row)
        db.flush()
        deadline_map[d["id"]] = row.id
    for b in data.get("study_blocks", []):
        if b.get("deadline_id") in deadline_map:
            db.add(StudyBlock(user_id=user.id, **{k: (_d(b[k]) if k == "day" else
                                                     deadline_map[b[k]] if k == "deadline_id" else b.get(k))
                                                  for k in BLOCK_FIELDS}))
    db.commit()
    return {k: len(data.get(k, [])) for k in ("sessions", "foods", "food_entries", "weights", "day_logs", "habits")}
