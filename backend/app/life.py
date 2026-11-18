"""
Services for the "life" side of Taper: nutrition targets, food log, body weight,
daily checklist and the real calendar. Thin layer between the DB and the pure
engines in ``app.engine``.
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB
from sqlalchemy.orm.attributes import flag_modified

from . import foods as F
from . import services
from .db import CalendarSource, CnfIndex, DayLog, Food, FoodEntry, Habit, Session, User, WeightEntry
from .engine import calendar as C
from .engine import nutrition as N
from .engine.constraints import _HEAVY

SETTINGS_DEFAULTS = {
    "body": {"sex": None, "birth_year": None, "height_cm": None, "neat": "moderate"},
    "nutrition": {"mode": "recomp", "rate_pct_per_week": None, "protein_g_per_kg": None,
                  "goal_weight_kg": None, "goal_date": None},
    "schedule": {"tz": "America/Toronto", "day_start": "06:30", "day_end": "21:30", "buffer_min": 15,
                 "prefer": "evening"},
    "gym_routines": [],
    "levels": {},       # sport -> {stage, progress, assessed, assessed_at, evaluated_through, history}
}
MEALS = ("breakfast", "lunch", "dinner", "snack", "training")


def settings(user: User) -> dict:
    s = user.settings or {}
    out = {}
    for k, default in SETTINGS_DEFAULTS.items():
        if isinstance(default, dict):
            out[k] = {**default, **(s.get(k) or {})}
        else:
            out[k] = s.get(k, default)
    return out


def merge_settings(user: User, patch: dict) -> dict:
    cur = settings(user)
    for k, v in patch.items():
        if k not in SETTINGS_DEFAULTS:
            continue
        cur[k] = {**cur[k], **v} if isinstance(SETTINGS_DEFAULTS[k], dict) and isinstance(v, dict) else v
    user.settings = cur
    flag_modified(user, "settings")          # JSON column: make sure nested changes are persisted
    return cur


# ------------------------------------------------------------------ weight
def weights(db: DB, user: User, since: Optional[date] = None) -> list[WeightEntry]:
    q = select(WeightEntry).where(WeightEntry.user_id == user.id)
    if since:
        q = q.where(WeightEntry.day >= since)
    return list(db.scalars(q.order_by(WeightEntry.day)))


def current_weight(db: DB, user: User, on: Optional[date] = None) -> Optional[float]:
    on = on or date.today()
    pts = [(w.day, w.kg) for w in weights(db, user, on - timedelta(days=120)) if w.day <= on]
    if not pts:
        return None
    return N.weight_trend(pts)[pts[-1][0]]


def body(db: DB, user: User, on: Optional[date] = None) -> tuple[Optional[N.Body], list[str]]:
    """The athlete's body, or the list of fields still missing (never guessed)."""
    s = settings(user)["body"]
    kg = current_weight(db, user, on)
    missing = [k for k, v in (("sex", s.get("sex")), ("birth_year", s.get("birth_year")),
                              ("height_cm", s.get("height_cm")), ("weight", kg)) if not v]
    if missing:
        return None, missing
    age = (on or date.today()).year - int(s["birth_year"])
    return N.Body(s["sex"], age, float(s["height_cm"]), kg, s.get("neat") or "moderate"), []


# ------------------------------------------------------------------ sessions → energy
def _if(s: Session, done: bool) -> float:
    tss = (s.actual_tss if done and s.actual_tss is not None else s.tss) or 0
    minutes = (s.actual_minutes if done and s.actual_minutes else s.minutes) or 1
    h = minutes / 60
    if tss <= 0:
        return 0.6
    return (tss / (h * 100)) ** (1 / 3 if s.sport == "swim" else 1 / 2)


def session_energy(s: Session) -> N.SessionEnergy:
    done = s.status == "done"
    return N.SessionEnergy(
        sport=s.sport, minutes=(s.actual_minutes if done and s.actual_minutes else s.minutes),
        distance_m=(s.actual_distance_m if done and s.actual_distance_m is not None else s.distance_m) or 0,
        intensity=_if(s, done), rpe=s.rpe if done else None, kind=s.kind, title=s.title, start=s.start_time,
        heavy=s.sport == "gym" and bool(_HEAVY.search(s.dsl or "")))


def sessions_between(db: DB, user: User, a: date, b: date) -> list[Session]:
    return list(db.scalars(select(Session).where(Session.user_id == user.id, Session.day.between(a, b),
                                                 Session.status != "missed").order_by(Session.day, Session.start_time)))


# ------------------------------------------------------------------ expenditure
def day_records(db: DB, user: User, end: date, days: int = 28) -> list[N.DayRecord]:
    start = end - timedelta(days=days)
    th = services.thresholds(user)
    intake = dict(db.execute(select(FoodEntry.day, func.sum(FoodEntry.kcal)).where(
        FoodEntry.user_id == user.id, FoodEntry.day.between(start, end)).group_by(FoodEntry.day)).all())
    complete = {d.day for d in db.scalars(select(DayLog).where(DayLog.user_id == user.id, DayLog.food_complete,
                                                               DayLog.day.between(start, end)))}
    wts = {w.day: w.kg for w in weights(db, user, start) if w.day <= end}
    kg = current_weight(db, user, end) or 70
    ex: dict[date, float] = {}
    for s in db.scalars(select(Session).where(Session.user_id == user.id, Session.status == "done",
                                              Session.day.between(start, end))):
        ex[s.day] = ex.get(s.day, 0) + N.session_kcal(session_energy(s), kg, th.bike_ftp, th.run_threshold_pace)
    out = []
    d = start
    while d < end:
        out.append(N.DayRecord(d, intake.get(d), wts.get(d), ex.get(d, 0.0), d in complete))
        d += timedelta(days=1)
    return out


def expenditure(db: DB, user: User, b: N.Body, on: date) -> dict:
    prior = N.base_expenditure(b)
    return N.adaptive_base(day_records(db, user, on), prior)


# ------------------------------------------------------------------ nutrition day
def _race_day(db: DB, user: User) -> Optional[date]:
    plan = services.active_plan(db, user)
    if plan and plan.goal.get("event") not in ("general", "custom"):
        return date.fromisoformat(plan.goal["race_date"])
    return None


def weight_goal(s: dict, kg: float, on: date) -> Optional[dict]:
    """Turn 'reach X kg by date' into a weekly rate, capped to safe limits (−1 % / +0.5 % per week)."""
    target = s.get("goal_weight_kg")
    if not target:
        return None
    target = float(target)
    diff = target - kg
    mode = "maintain" if abs(diff) < 0.5 else ("lose" if diff < 0 else "gain")
    out = {"goal_weight_kg": target, "goal_date": s.get("goal_date"), "to_go_kg": round(diff, 1), "mode": mode}
    if s.get("goal_date") and mode != "maintain":
        weeks = max(1.0, (date.fromisoformat(s["goal_date"]) - on).days / 7)
        needed = diff / kg / weeks * 100
        rate = max(-1.0, min(0.5, needed))
        out.update(rate_needed=round(needed, 2), rate=round(rate, 2), capped=abs(rate - needed) > 0.01,
                   weeks_left=round(weeks, 1))
    else:
        out.update(rate=N.GOAL_RATE_DEFAULT[mode], capped=False)
    if out["rate"]:
        out["eta"] = (on + timedelta(weeks=abs(diff / (kg * out["rate"] / 100)))).isoformat()
    return out


def commitment_energy(db: DB, user: User, a: date, b: date) -> list[tuple[date, N.SessionEnergy]]:
    """Training the athlete does outside Taper (club practice, climbing…) still costs energy."""
    plan = services.active_plan(db, user)
    if not plan:
        return []
    start = date.fromisoformat(plan.goal.get("start_date") or a.isoformat())
    out = []
    d = max(a, start)
    while d <= b:
        for c in plan.goal.get("commitments") or []:
            if int(c["day"]) == d.weekday():
                out.append((d, N.SessionEnergy("other", float(c["minutes"]), intensity=0.8 if c.get("hard", True) else 0.6,
                                               kind="other", title=c.get("name") or "Your training")))
        d += timedelta(days=1)
    return out


def targets_for(db: DB, user: User, days: list[date], b: N.Body, base: float) -> list[N.DayTarget]:
    s = settings(user)["nutrition"]
    wg = weight_goal(s, b.weight_kg, min(days))
    if wg:
        goal = N.Goal(wg["mode"] if s.get("mode") != "recomp" or wg["mode"] == "gain" else "recomp",
                      wg["rate"], s.get("protein_g_per_kg"))
    else:
        goal = N.Goal(s.get("mode") or "recomp", s.get("rate_pct_per_week"), s.get("protein_g_per_kg"))
    th = services.thresholds(user)
    by_day: dict[date, list[N.SessionEnergy]] = {}
    race = _race_day(db, user)
    lo, hi = min(days), max(max(days), race or max(days))
    end = min(hi, max(days) + timedelta(days=2))
    for x in sessions_between(db, user, lo, end):
        by_day.setdefault(x.day, []).append(session_energy(x))
    for d, e in commitment_energy(db, user, lo, end):
        by_day.setdefault(d, []).append(e)
    out = N.week_targets(days, by_day, b, goal, base, th.bike_ftp, th.run_threshold_pace, race)
    return out


def eaten(db: DB, user: User, d: date) -> dict:
    rows = list(db.scalars(select(FoodEntry).where(FoodEntry.user_id == user.id, FoodEntry.day == d)
                           .order_by(FoodEntry.created_at)))
    meals = {m: [] for m in MEALS}
    tot = {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    for r in rows:
        meals.setdefault(r.meal, []).append(entry_dict(r))
        for k in tot:
            tot[k] += getattr(r, k) or 0
    return {"meals": meals, "totals": {k: round(v) for k, v in tot.items()}}


def entry_dict(r: FoodEntry) -> dict:
    return {"id": r.id, "meal": r.meal, "name": r.name, "grams": r.grams, "food_id": r.food_id,
            "kcal": round(r.kcal), "protein": round(r.protein, 1), "carbs": round(r.carbs, 1), "fat": round(r.fat, 1)}


def fuel_for_day(db: DB, user: User, d: date, kg: float) -> list[dict]:
    rows = sessions_between(db, user, d, d)
    out = []
    starts = [(r.start_time, r) for r in rows if r.start_time]
    for r in rows:
        nxt_h = None
        if r.start_time:
            later = [t for t, x in starts if x is not r and t > r.start_time]
            if later:
                h1, m1 = map(int, r.start_time.split(":"))
                h2, m2 = map(int, min(later).split(":"))
                nxt_h = ((h2 * 60 + m2) - (h1 * 60 + m1 + r.minutes)) / 60
        out.append({"session": services.session_dict(r), "fuel": N.fuel_plan(session_energy(r), kg, nxt_h)})
    return out


def nutrition_day(db: DB, user: User, d: date) -> dict:
    b, missing = body(db, user, d)
    if not b:
        return {"setup_needed": missing, "eaten": eaten(db, user, d)}
    exp = expenditure(db, user, b, d)
    monday = d - timedelta(days=d.weekday())
    week = [monday + timedelta(days=i) for i in range(7)]
    if d.weekday() == 6:
        week += [d + timedelta(days=1)]
    tgts = {t.day: t for t in targets_for(db, user, week, b, exp["base"])}
    today, tomorrow = tgts[d], tgts.get(d + timedelta(days=1))
    if tomorrow is None:
        nxt = d + timedelta(days=1)
        tomorrow = {t.day: t for t in targets_for(db, user, [nxt + timedelta(days=i) for i in range(7)], b,
                                                  exp["base"])}[nxt]
    e = eaten(db, user, d)
    remaining = {k: round(getattr(today, f) - e["totals"][k])
                 for k, f in (("kcal", "kcal"), ("protein", "protein_g"), ("carbs", "carbs_g"), ("fat", "fat_g"))}
    dl = db.scalar(select(DayLog).where(DayLog.user_id == user.id, DayLog.day == d))
    return {
        "date": d.isoformat(), "target": today.to_dict(), "eaten": e, "remaining": remaining,
        "food_complete": bool(dl and dl.food_complete),
        "sessions": fuel_for_day(db, user, d, b.weight_kg),
        "tomorrow": {"target": tomorrow.to_dict(), "sessions": fuel_for_day(db, user, d + timedelta(days=1), b.weight_kg)},
        "week": [t.to_dict() for t in tgts.values() if t.day in week[:7]],
        "expenditure": {**exp, "bmr": round(N.bmr(b)), "weight_kg": b.weight_kg},
        "weight_goal": weight_goal(settings(user)["nutrition"], b.weight_kg, d),
    }


# ------------------------------------------------------------------ food search & logging
def upsert_food(db: DB, f: F.FoodData) -> Food:
    row = db.scalar(select(Food).where(Food.source == f.source, Food.source_id == f.source_id))
    if row is None:
        row = Food(source=f.source, source_id=f.source_id)
        db.add(row)
    for k in ("name", "brand", "barcode", "kcal", "protein", "carbs", "fat", "fiber", "sugar", "sodium_mg", "servings"):
        setattr(row, k, getattr(f, k))
    row.fetched_at = datetime.now(timezone.utc)
    db.flush()
    return row


NUTRIENTS = ("kcal", "protein", "carbs", "fat", "fiber", "sugar", "sodium_mg")


def food_dict(f: Food) -> dict:
    d = {"id": f.id, "source": f.source, "source_id": f.source_id, "name": f.name, "brand": f.brand,
         "barcode": f.barcode, "per100": {k: getattr(f, k) for k in NUTRIENTS},
         "servings": f.servings or [], "complete": None not in (f.kcal, f.protein, f.carbs, f.fat)}
    if f.source == "recipe":
        d.update(ingredients=f.ingredients or [], recipe_weight_g=f.recipe_weight_g, recipe_servings=f.recipe_servings)
    return d


def compute_recipe(db: DB, user: User, ingredients: list[dict], cooked_weight_g: Optional[float],
                   servings: float) -> dict:
    """Sum ingredient nutrients and express them per 100 g of the finished dish.

    Cooked weight is optional: cooking changes water content, so weighing the pot
    gives correct per-gram values; without it the raw ingredient total is used.
    A nutrient missing from any ingredient makes the recipe incomplete (never guessed).
    """
    if not ingredients:
        raise ValueError("Add at least one ingredient")
    if servings <= 0:
        raise ValueError("Servings must be more than 0")
    totals = {k: 0.0 for k in NUTRIENTS}
    missing: set[str] = set()
    raw_weight, items = 0.0, []
    for ing in ingredients:
        f = db.get(Food, int(ing["food_id"]))
        grams = float(ing.get("grams") or 0)
        if not f or (f.owner_id and f.owner_id != user.id):
            raise ValueError("An ingredient wasn't found")
        if grams <= 0:
            raise ValueError(f"Enter grams for {f.name}")
        for k in NUTRIENTS:
            v = getattr(f, k)
            if v is None:
                missing.add(k)
            else:
                totals[k] += v * grams / 100
        raw_weight += grams
        items.append({"food_id": f.id, "name": f.name, "grams": grams})
    if "kcal" in missing:
        raise ValueError("One ingredient has no calorie value — replace it or add it as a custom food")
    weight = float(cooked_weight_g) if cooked_weight_g else raw_weight
    per100 = {k: (None if k in missing else round(totals[k] * 100 / weight, 3)) for k in NUTRIENTS}
    serving_g = round(weight / servings, 1)
    servings_list = [{"label": "1 serving", "grams": serving_g}]
    if servings != 1:
        servings_list.append({"label": "Whole recipe", "grams": round(weight, 1)})
    return {"per100": per100, "items": items, "weight": weight, "servings": servings_list,
            "per_serving": {k: (None if v is None else round(v * serving_g / 100, 1)) for k, v in per100.items()}}


def save_recipe(db: DB, user: User, name: str, ingredients: list[dict], cooked_weight_g: Optional[float],
                servings: float, food: Optional[Food] = None) -> Food:
    r = compute_recipe(db, user, ingredients, cooked_weight_g, servings)
    if food is None:
        food = Food(source="recipe", source_id=f"r{user.id}-{datetime.now(timezone.utc).timestamp():.6f}",
                    owner_id=user.id)
        db.add(food)
    food.name = name.strip()[:255]
    for k, v in r["per100"].items():
        setattr(food, k, v)
    food.ingredients, food.recipe_weight_g, food.recipe_servings = r["items"], r["weight"], servings
    food.servings = r["servings"]
    food.hidden = False
    db.commit()
    return food


def my_foods(db: DB, user: User) -> list[dict]:
    rows = db.scalars(select(Food).where(Food.owner_id == user.id, Food.hidden.is_not(True))
                      .order_by(Food.source.desc(), Food.name))
    return [food_dict(f) for f in rows]


async def ensure_cnf_index(db: DB, client) -> int:
    n = db.scalar(select(func.count()).select_from(CnfIndex)) or 0
    if n:
        return n
    rows = await F.cnf_index(client)
    db.add_all(CnfIndex(code=c, name_fr=fr, name_en=en) for c, fr, en in rows)
    db.commit()
    return len(rows)


async def search_foods(db: DB, user: User, q: str) -> dict:
    q = q.strip()
    folded = F.fold(q)
    words = [w for w in folded.split() if w]
    local = [f for f in db.scalars(select(Food).where((Food.owner_id == user.id) | (Food.owner_id.is_(None)),
                                                      Food.hidden.is_not(True))
                                   .order_by(Food.fetched_at.desc()).limit(5000))
             if all(w in F.fold(f.name + " " + (f.brand or "")) for w in words)][:15]
    status: dict[str, str] = {}
    results: dict[str, list] = {"mine": [food_dict(f) for f in local if f.source in ("custom", "recipe")],
                                "cnf": [], "off": [], "usda": []}
    async with F.client() as client:
        async def cnf():
            await ensure_cnf_index(db, client)
            index = [(r.code, r.name_fr, r.name_en) for r in db.scalars(select(CnfIndex))]
            return [{"source": "cnf", "source_id": str(c), "name": n, "brand": None, "per100": None,
                     "servings": [], "complete": None} for c, n in F.cnf_match(index, q)]

        tasks = {"cnf": cnf(), "off": F.off_search(client, q), "usda": F.usda_search(client, q)}
        done = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for name, res in zip(tasks, done, strict=True):
        if isinstance(res, Exception):
            status[name] = str(res) if isinstance(res, RuntimeError) else f"unavailable ({res.__class__.__name__})"
            continue
        status[name] = "ok"
        if name == "cnf":
            results["cnf"] = res
        else:
            results[name] = [food_dict(upsert_food(db, f)) for f in res]
    cached = [food_dict(f) for f in local if f.source not in ("custom", "recipe")]
    db.commit()
    return {"query": q, "results": results, "recent_matches": cached, "sources": status}


async def resolve_cnf(db: DB, code: int) -> Food:
    existing = db.scalar(select(Food).where(Food.source == "cnf", Food.source_id == str(code)))
    if existing:
        return existing
    idx = db.get(CnfIndex, code)
    name = (idx.name_fr or idx.name_en) if idx else f"CNF {code}"
    async with F.client() as client:
        f = await F.cnf_food(client, code, name)
    row = upsert_food(db, f)
    db.commit()
    return row


async def barcode(db: DB, code: str) -> Optional[Food]:
    row = db.scalar(select(Food).where(Food.barcode == code))
    if row:
        return row
    async with F.client() as client:
        f = await F.off_barcode(client, code)
    if not f:
        return None
    row = upsert_food(db, f)
    db.commit()
    return row


def log_food(db: DB, user: User, day: date, meal: str, food: Optional[Food], grams: Optional[float],
             quick: Optional[dict] = None) -> FoodEntry:
    if meal not in MEALS:
        raise ValueError(f"meal must be one of {', '.join(MEALS)}")
    if food:
        if food.kcal is None:
            raise ValueError("This food has no calorie value in its source database — add it as a custom food")
        if not grams or grams <= 0:
            raise ValueError("Enter a quantity in grams")
        k = grams / 100
        e = FoodEntry(user_id=user.id, day=day, meal=meal, food_id=food.id,
                      name=food.name + (f" ({food.brand})" if food.brand else ""), grams=grams,
                      kcal=food.kcal * k, protein=(food.protein or 0) * k, carbs=(food.carbs or 0) * k,
                      fat=(food.fat or 0) * k)
    else:
        q = quick or {}
        if not q.get("name") or q.get("kcal") is None:
            raise ValueError("Quick add needs a name and calories")
        e = FoodEntry(user_id=user.id, day=day, meal=meal, name=q["name"][:255], grams=None, kcal=float(q["kcal"]),
                      protein=float(q.get("protein") or 0), carbs=float(q.get("carbs") or 0),
                      fat=float(q.get("fat") or 0))
    db.add(e)
    db.commit()
    return e


def recent_foods(db: DB, user: User, limit: int = 20) -> list[dict]:
    rows = db.execute(select(FoodEntry.food_id, func.max(FoodEntry.created_at).label("t"),
                             func.count().label("n"), func.avg(FoodEntry.grams).label("g"))
                      .where(FoodEntry.user_id == user.id, FoodEntry.food_id.is_not(None))
                      .group_by(FoodEntry.food_id).order_by(func.max(FoodEntry.created_at).desc()).limit(limit)).all()
    out = []
    for fid, _, n, g in rows:
        f = db.get(Food, fid)
        if f:
            out.append({**food_dict(f), "times": n, "usual_grams": round(g) if g else None})
    return out


def day_log(db: DB, user: User, d: date) -> DayLog:
    dl = db.scalar(select(DayLog).where(DayLog.user_id == user.id, DayLog.day == d))
    if not dl:
        dl = DayLog(user_id=user.id, day=d, checks={})
        db.add(dl)
        db.flush()
    return dl


# ------------------------------------------------------------------ checklist
def checklist(db: DB, user: User, d: date, with_history: bool = True) -> dict:
    items = []
    for s in db.scalars(select(Session).where(Session.user_id == user.id, Session.day == d)
                        .order_by(Session.start_time, Session.id)):
        if s.status == "missed":
            continue
        label = s.title + (f" · {s.start_time}" if s.start_time else "") + f" · {s.minutes} min"
        items.append({"key": f"session:{s.id}", "label": label, "done": s.status == "done", "kind": "training",
                      "auto": True, "session_id": s.id, "sport": s.sport})
    dl = db.scalar(select(DayLog).where(DayLog.user_id == user.id, DayLog.day == d))
    checks = (dl.checks if dl else {}) or {}
    b, _ = body(db, user, d)
    if b:
        n = nutrition_day(db, user, d)
        t, tot = n["target"], n["eaten"]["totals"]
        items.append({"key": "protein", "label": f"Protein: {tot['protein']} / {t['protein_g']} g",
                      "done": tot["protein"] >= 0.95 * t["protein_g"], "kind": "nutrition", "auto": True})
        items.append({"key": "kcal", "label": f"Calories within 10 %: {tot['kcal']} / {t['kcal']}",
                      "done": abs(tot["kcal"] - t["kcal"]) <= 0.1 * t["kcal"], "kind": "nutrition", "auto": True})
        for fs in n["sessions"]:
            key_steps = [x for x in fs["fuel"] if x["phase"] == "before" and x.get("carbs_g")]
            if key_steps and fs["session"]["minutes"] > 60:
                items.append({"key": f"fuel:{fs['session']['id']}", "label": f"Pre-session meal: {key_steps[0]['text']}",
                              "done": bool(checks.get(f"fuel:{fs['session']['id']}")), "kind": "nutrition",
                              "auto": False})
    items.append({"key": "food_complete", "label": "Log every meal", "done": bool(dl and dl.food_complete),
                  "kind": "nutrition", "auto": False})
    items.append({"key": "weigh", "label": "Morning weigh-in",
                  "done": db.scalar(select(WeightEntry).where(WeightEntry.user_id == user.id, WeightEntry.day == d))
                  is not None, "kind": "body", "auto": True})
    for h in db.scalars(select(Habit).where(Habit.user_id == user.id, Habit.active).order_by(Habit.sort, Habit.id)):
        if d.weekday() in (h.weekdays or []):
            items.append({"key": f"habit:{h.id}", "label": h.label, "done": bool(checks.get(f"habit:{h.id}")),
                          "kind": "habit", "auto": False, "habit_id": h.id})
    done = sum(1 for i in items if i["done"])
    out = {"date": d.isoformat(), "items": items, "done": done, "total": len(items),
           "score": round(100 * done / len(items)) if items else 0, "reflection": dl.reflection if dl else None}
    if with_history:
        out["history"] = [{"date": (d - timedelta(days=k)).isoformat(),
                           "score": checklist(db, user, d - timedelta(days=k), False)["score"]}
                          for k in range(13, 0, -1)] + [{"date": d.isoformat(), "score": out["score"]}]
    return out


def set_check(db: DB, user: User, d: date, key: str, done: bool) -> None:
    dl = day_log(db, user, d)
    if key == "food_complete":
        dl.food_complete = done
    elif key.startswith(("habit:", "fuel:")):
        dl.checks = {**(dl.checks or {}), key: done}
    else:
        raise ValueError("This item is ticked automatically")
    db.commit()


# ------------------------------------------------------------------ calendar
def validate_calendar_url(url: str) -> str:
    """Only public https iCal feeds — blocks requests to internal addresses (SSRF)."""
    u = urlparse(url.strip())
    if u.scheme == "webcal":
        u = u._replace(scheme="https")
    if u.scheme != "https" or not u.hostname:
        raise ValueError("Use the https iCal address (Google Calendar → Settings → Secret address in iCal format)")
    try:
        for info in socket.getaddrinfo(u.hostname, 443):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError("That address points to a private network")
    except socket.gaierror as e:
        raise ValueError("Couldn't resolve that calendar host") from e
    return u.geturl()


async def fetch_calendar(src: CalendarSource) -> None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(src.url)
            r.raise_for_status()
        if "BEGIN:VCALENDAR" not in r.text[:500]:
            raise ValueError("That link didn't return a calendar")
        src.ics, src.last_error = r.text, None
    except Exception as e:  # noqa: BLE001 — store the reason for the UI
        src.last_error = str(e)[:300]
    src.last_sync = datetime.now(timezone.utc)


def busy_blocks(db: DB, user: User, a: date, b: date) -> tuple[list[C.Busy], list[str]]:
    tz = settings(user)["schedule"]["tz"]
    out, errors = [], []
    for src in db.scalars(select(CalendarSource).where(CalendarSource.user_id == user.id)):
        if not src.ics:
            continue
        busy, err = C.safe_parse(src.ics, a, b, tz)
        out.extend(busy)
        if err:
            errors.append(f"{src.name}: {err}")
    return sorted(out, key=lambda x: x.start), errors


def has_calendar(db: DB, user: User) -> bool:
    return db.scalar(select(CalendarSource.id).where(CalendarSource.user_id == user.id,
                                                     CalendarSource.ics.is_not(None))) is not None


def availability(db: DB, user: User, a: date, b: date) -> tuple[dict[date, C.DayAvailability], set[date]]:
    sch = settings(user)["schedule"]
    busy, _ = busy_blocks(db, user, a, b)
    av = C.availability_range(a, b, busy, tz=sch["tz"], day_start=sch["day_start"], day_end=sch["day_end"],
                              buffer_min=int(sch["buffer_min"]))
    return av, C.exam_days(busy)


def assign_start_times(db: DB, user: User, a: date, b: date) -> None:
    if not has_calendar(db, user):
        return
    av, _ = availability(db, user, a, b)
    prefer = settings(user)["schedule"].get("prefer", "any")
    rows = list(db.scalars(select(Session).where(Session.user_id == user.id, Session.day.between(a, b),
                                                 Session.status == "planned")))
    by_day: dict[date, list[Session]] = {}
    for r in rows:
        by_day.setdefault(r.day, []).append(r)
    for d, items in by_day.items():
        if d in av:
            slots = C.assign_slots(items, av[d].windows, prefer)
            for r in items:
                r.start_time = slots.get(r)
    db.commit()


def calendar_week(db: DB, user: User, d: date) -> dict:
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    busy, errors = busy_blocks(db, user, monday, sunday)
    sch = settings(user)["schedule"]
    av = C.availability_range(monday, sunday, busy, tz=sch["tz"], day_start=sch["day_start"],
                              day_end=sch["day_end"], buffer_min=int(sch["buffer_min"]))
    return {
        "week_start": monday.isoformat(), "errors": errors,
        "busy": [{"start": x.start.isoformat(), "end": x.end.isoformat(), "title": x.title, "kind": x.kind,
                  "all_day": x.all_day} for x in busy],
        "days": [{"date": k.isoformat(), "free_min": v.total_min, "longest_min": v.longest_min, "exam": v.exam,
                  "windows": [[w.start.strftime("%H:%M"), w.end.strftime("%H:%M")] for w in v.windows]}
                 for k, v in av.items()],
    }


def round_half(x: float) -> float:
    return math.floor(x * 2 + 0.5) / 2
