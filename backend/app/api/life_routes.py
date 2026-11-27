"""Nutrition, food log, body weight, daily checklist, calendar and settings endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .. import life
from ..auth import current_user
from ..db import CalendarSource, Food, FoodEntry, Habit, User, WeightEntry, get_db
from ..engine import dsl

router = APIRouter(prefix="/api")


def _day(d: Optional[date]) -> date:
    return d or date.today()


# ------------------------------------------------------------------ settings
class SettingsIn(BaseModel):
    body: Optional[dict] = None
    nutrition: Optional[dict] = None
    schedule: Optional[dict] = None
    gym_routines: Optional[list[dict]] = None
    study: Optional[dict] = None


@router.get("/settings")
def get_settings(user: User = Depends(current_user)):
    return life.settings(user)


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    patch = body.model_dump(exclude_none=True)
    b = patch.get("body") or {}
    if b.get("sex") not in (None, "male", "female"):
        raise HTTPException(422, "sex must be male or female (used by the Mifflin-St Jeor equation)")
    if b.get("height_cm") is not None and not 120 <= float(b["height_cm"]) <= 230:
        raise HTTPException(422, "height_cm looks wrong")
    if b.get("birth_year") is not None and not 1920 <= int(b["birth_year"]) <= date.today().year - 13:
        raise HTTPException(422, "birth_year looks wrong")
    n = patch.get("nutrition") or {}
    if n.get("mode") not in (None, "lose", "recomp", "maintain", "gain"):
        raise HTTPException(422, "mode must be lose, recomp, maintain or gain")
    for r in patch.get("gym_routines") or []:
        try:
            w = dsl.parse(r.get("dsl", ""))
        except dsl.DSLError as e:
            raise HTTPException(422, f"Routine '{r.get('name')}': {e}") from e
        if w.sport != "gym" or not r.get("name"):
            raise HTTPException(422, "Each routine needs a name and a 'Gym: …' workout")
    out = life.merge_settings(user, patch)
    db.commit()
    return out


# ------------------------------------------------------------------ nutrition
@router.get("/nutrition/day")
def nutrition_day(d: Optional[date] = None, user: User = Depends(current_user), db: DB = Depends(get_db)):
    return life.nutrition_day(db, user, _day(d))


# ------------------------------------------------------------------ foods
class CustomFoodIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: Optional[str] = None
    basis_g: float = Field(100, gt=0, description="the nutrient values below are for this many grams")
    kcal: float = Field(ge=0)
    protein: float = Field(0, ge=0)
    carbs: float = Field(0, ge=0)
    fat: float = Field(0, ge=0)
    serving_label: Optional[str] = None
    serving_g: Optional[float] = Field(None, gt=0)


@router.get("/foods/search")
async def search_foods(q: str, user: User = Depends(current_user), db: DB = Depends(get_db)):
    if len(q.strip()) < 2:
        raise HTTPException(422, "Type at least 2 characters")
    return await life.search_foods(db, user, q)


@router.get("/foods/recent")
def recent_foods(user: User = Depends(current_user), db: DB = Depends(get_db)):
    return life.recent_foods(db, user)


@router.get("/foods/cnf/{code}")
async def cnf_food(code: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    try:
        return life.food_dict(await life.resolve_cnf(db, code))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Canadian Nutrient File unavailable ({e.__class__.__name__})") from e


@router.get("/foods/barcode/{code}")
async def barcode(code: str, user: User = Depends(current_user), db: DB = Depends(get_db)):
    try:
        f = await life.barcode(db, code)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Open Food Facts unavailable ({e.__class__.__name__})") from e
    if not f:
        raise HTTPException(404, "This barcode isn't in Open Food Facts yet. Add it as a custom food.")
    return life.food_dict(f)


@router.post("/foods")
def custom_food(body: CustomFoodIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    k = 100 / body.basis_g
    f = Food(source="custom", source_id=f"u{user.id}-{date.today().isoformat()}-{body.name[:40]}", name=body.name,
             brand=body.brand, kcal=round(body.kcal * k, 2), protein=round(body.protein * k, 2),
             carbs=round(body.carbs * k, 2), fat=round(body.fat * k, 2), owner_id=user.id,
             servings=[{"label": body.serving_label or f"{body.serving_g:g} g", "grams": body.serving_g}]
             if body.serving_g else [])
    exists = db.scalar(select(Food).where(Food.source == "custom", Food.source_id == f.source_id))
    if exists:
        f.source_id += f"-{exists.id}"
    db.add(f)
    db.commit()
    return life.food_dict(f)


# ------------------------------------------------------------------ food log
class LogIn(BaseModel):
    day: date
    meal: str
    food_id: Optional[int] = None
    grams: Optional[float] = Field(None, gt=0, le=5000)
    name: Optional[str] = None
    kcal: Optional[float] = Field(None, ge=0, le=10000)
    protein: Optional[float] = Field(None, ge=0)
    carbs: Optional[float] = Field(None, ge=0)
    fat: Optional[float] = Field(None, ge=0)


@router.get("/log")
def get_log(d: Optional[date] = None, user: User = Depends(current_user), db: DB = Depends(get_db)):
    return life.eaten(db, user, _day(d))


@router.post("/log")
def add_log(body: LogIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    food = None
    if body.food_id:
        food = db.get(Food, body.food_id)
        if not food or (food.owner_id and food.owner_id != user.id):
            raise HTTPException(404, "Food not found")
    try:
        e = life.log_food(db, user, body.day, body.meal, food, body.grams, body.model_dump())
    except ValueError as err:
        raise HTTPException(422, str(err)) from err
    return life.entry_dict(e)


@router.delete("/log/{eid}")
def delete_log(eid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    e = db.get(FoodEntry, eid)
    if not e or e.user_id != user.id:
        raise HTTPException(404, "Entry not found")
    db.delete(e)
    db.commit()
    return {"ok": True}


class CopyIn(BaseModel):
    from_day: date
    to_day: date
    meal: Optional[str] = None


@router.post("/log/copy")
def copy_log(body: CopyIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    q = select(FoodEntry).where(FoodEntry.user_id == user.id, FoodEntry.day == body.from_day)
    if body.meal:
        q = q.where(FoodEntry.meal == body.meal)
    n = 0
    for e in list(db.scalars(q)):
        db.add(FoodEntry(user_id=user.id, day=body.to_day, meal=e.meal, food_id=e.food_id, name=e.name,
                         grams=e.grams, kcal=e.kcal, protein=e.protein, carbs=e.carbs, fat=e.fat))
        n += 1
    db.commit()
    return {"copied": n}


# ------------------------------------------------------------------ weight
class WeightIn(BaseModel):
    day: date
    kg: float = Field(gt=25, lt=350)


@router.get("/weights")
def get_weights(days: int = 120, user: User = Depends(current_user), db: DB = Depends(get_db)):
    rows = life.weights(db, user, date.today() - timedelta(days=days))
    trend = life.N.weight_trend([(w.day, w.kg) for w in rows])
    return {"entries": [{"day": w.day.isoformat(), "kg": w.kg} for w in rows],
            "trend": [{"day": d.isoformat(), "kg": v} for d, v in trend.items()]}


@router.put("/weights")
def put_weight(body: WeightIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    w = db.scalar(select(WeightEntry).where(WeightEntry.user_id == user.id, WeightEntry.day == body.day))
    if w:
        w.kg = body.kg
    else:
        db.add(WeightEntry(user_id=user.id, day=body.day, kg=body.kg))
    db.commit()
    return {"ok": True}


@router.delete("/weights/{d}")
def delete_weight(d: date, user: User = Depends(current_user), db: DB = Depends(get_db)):
    w = db.scalar(select(WeightEntry).where(WeightEntry.user_id == user.id, WeightEntry.day == d))
    if w:
        db.delete(w)
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ checklist & habits
class CheckIn(BaseModel):
    key: str
    done: bool


class DayNoteIn(BaseModel):
    reflection: Optional[str] = Field(None, max_length=4000)


class HabitIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))


@router.get("/checklist")
def get_checklist(d: Optional[date] = None, user: User = Depends(current_user), db: DB = Depends(get_db)):
    return life.checklist(db, user, _day(d))


@router.post("/checklist/{d}")
def set_check(d: date, body: CheckIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    try:
        life.set_check(db, user, d, body.key, body.done)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return life.checklist(db, user, d, with_history=False)


@router.put("/day/{d}/note")
def day_note(d: date, body: DayNoteIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    dl = life.day_log(db, user, d)
    dl.reflection = body.reflection
    db.commit()
    return {"ok": True}


@router.get("/habits")
def list_habits(user: User = Depends(current_user), db: DB = Depends(get_db)):
    return [{"id": h.id, "label": h.label, "weekdays": h.weekdays}
            for h in db.scalars(select(Habit).where(Habit.user_id == user.id, Habit.active).order_by(Habit.sort))]


@router.post("/habits")
def add_habit(body: HabitIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    if any(d not in range(7) for d in body.weekdays):
        raise HTTPException(422, "weekdays are 0 (Mon) to 6 (Sun)")
    h = Habit(user_id=user.id, label=body.label, weekdays=sorted(set(body.weekdays)))
    db.add(h)
    db.commit()
    return {"id": h.id, "label": h.label, "weekdays": h.weekdays}


@router.delete("/habits/{hid}")
def delete_habit(hid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    h = db.get(Habit, hid)
    if not h or h.user_id != user.id:
        raise HTTPException(404, "Habit not found")
    h.active = False
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ calendar
class CalendarIn(BaseModel):
    name: str = Field("Google Calendar", max_length=120)
    url: str = Field(min_length=10, max_length=2000)


def _src_out(s: CalendarSource) -> dict:
    tail = s.url[-12:]
    return {"id": s.id, "name": s.name, "url_hint": f"…{tail}", "last_sync": s.last_sync.isoformat() if s.last_sync
            else None, "last_error": s.last_error, "ok": bool(s.ics) and not s.last_error}


@router.get("/calendars")
def list_calendars(user: User = Depends(current_user), db: DB = Depends(get_db)):
    return [_src_out(s) for s in db.scalars(select(CalendarSource).where(CalendarSource.user_id == user.id))]


@router.post("/calendars")
async def add_calendar(body: CalendarIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    try:
        url = life.validate_calendar_url(body.url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    src = CalendarSource(user_id=user.id, name=body.name, url=url)
    await life.fetch_calendar(src)
    if src.last_error:
        raise HTTPException(422, f"Couldn't load that calendar: {src.last_error}")
    db.add(src)
    db.commit()
    return _src_out(src)


@router.post("/calendars/sync")
async def sync_calendars(user: User = Depends(current_user), db: DB = Depends(get_db)):
    srcs = list(db.scalars(select(CalendarSource).where(CalendarSource.user_id == user.id)))
    for s in srcs:
        await life.fetch_calendar(s)
    db.commit()
    from .. import study
    study.safe_replan(db, user)           # new classes or shifts: move revision blocks out of the way
    return [_src_out(s) for s in srcs]


@router.delete("/calendars/{cid}")
def delete_calendar(cid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    s = db.get(CalendarSource, cid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "Calendar not found")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/calendar/week")
def calendar_week(d: Optional[date] = None, user: User = Depends(current_user), db: DB = Depends(get_db)):
    return life.calendar_week(db, user, _day(d))


# ------------------------------------------------------------------ planning with the real calendar
@router.post("/plans/replan")
def replan(user: User = Depends(current_user), db: DB = Depends(get_db)):
    """Rebuild from today with the current calendar, fitness, levels and routines; history is kept."""
    from .. import planning
    try:
        new = planning.replan(db, user)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    from .. import study
    study.safe_replan(db, user)
    return {"id": new.id, "weeks": len(new.weeks)}


# ------------------------------------------------------------------ my foods & recipes
class IngredientIn(BaseModel):
    food_id: int
    grams: float = Field(gt=0, le=20000)


class RecipeIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ingredients: list[IngredientIn] = Field(min_length=1, max_length=80)
    servings: float = Field(1, gt=0, le=100)
    cooked_weight_g: Optional[float] = Field(None, gt=0, le=50000)


def _own_food(db: DB, user: User, fid: int, source: Optional[str] = None) -> Food:
    f = db.get(Food, fid)
    if not f or f.owner_id != user.id or (source and f.source != source):
        raise HTTPException(404, "Food not found")
    return f


@router.get("/myfoods")
def my_foods(user: User = Depends(current_user), db: DB = Depends(get_db)):
    return life.my_foods(db, user)


@router.post("/recipes/preview")
def preview_recipe(body: RecipeIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    try:
        return life.compute_recipe(db, user, [i.model_dump() for i in body.ingredients], body.cooked_weight_g,
                                   body.servings)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/recipes")
def create_recipe(body: RecipeIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    try:
        f = life.save_recipe(db, user, body.name, [i.model_dump() for i in body.ingredients], body.cooked_weight_g,
                             body.servings)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return life.food_dict(f)


@router.put("/recipes/{fid}")
def update_recipe(fid: int, body: RecipeIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    f = _own_food(db, user, fid, "recipe")
    if any(i.food_id == fid for i in body.ingredients):
        raise HTTPException(422, "A recipe can't contain itself")
    try:
        f = life.save_recipe(db, user, body.name, [i.model_dump() for i in body.ingredients], body.cooked_weight_g,
                             body.servings, f)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return life.food_dict(f)


@router.put("/foods/{fid}")
def update_custom_food(fid: int, body: CustomFoodIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    """Editing a food changes future logs only; entries already logged keep their numbers."""
    f = _own_food(db, user, fid, "custom")
    k = 100 / body.basis_g
    f.name, f.brand = body.name, body.brand
    f.kcal, f.protein = round(body.kcal * k, 2), round(body.protein * k, 2)
    f.carbs, f.fat = round(body.carbs * k, 2), round(body.fat * k, 2)
    f.servings = [{"label": body.serving_label or f"{body.serving_g:g} g", "grams": body.serving_g}] if body.serving_g else []
    db.commit()
    return life.food_dict(f)


@router.delete("/foods/{fid}")
def delete_food(fid: int, user: User = Depends(current_user), db: DB = Depends(get_db)):
    f = _own_food(db, user, fid)
    f.hidden = True          # hidden, not destroyed: past entries and recipes still reference it
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ export / import
@router.get("/export")
def export_all(user: User = Depends(current_user), db: DB = Depends(get_db)):
    from fastapi.responses import JSONResponse

    from .. import backup
    data = backup.export_user(db, user)
    return JSONResponse(data, headers={"Content-Disposition":
                                       f'attachment; filename="taper-backup-{date.today().isoformat()}.json"'})


class ImportIn(BaseModel):
    data: dict
    confirm_replace: bool = False


@router.post("/import")
def import_all(body: ImportIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    from .. import backup
    if not body.confirm_replace:
        raise HTTPException(422, "Importing replaces everything in this account. Send confirm_replace=true.")
    try:
        counts = backup.import_user(db, user, body.data)
    except (KeyError, ValueError, TypeError) as e:
        db.rollback()
        raise HTTPException(422, f"That file isn't a Taper backup ({e})") from e
    return {"imported": counts}


@router.get("/storage")
def storage_info(user: User = Depends(current_user)):
    from .. import storage
    from ..db import DATABASE_URL
    path = storage.sqlite_path(DATABASE_URL)
    backups = sorted(storage.BACKUP_DIR.glob("taper-*.db")) if path else []
    return {"database": str(path) if path else "external database (DATABASE_URL)",
            "backups_folder": str(storage.BACKUP_DIR) if path else None,
            "backups": [b.name for b in backups[-5:]], "backup_count": len(backups),
            "restore": storage.restore_hint() if path else None}


# ------------------------------------------------------------------ levels
class AssessIn(BaseModel):
    session_id: Optional[int] = None
    minutes: Optional[float] = Field(None, gt=0, le=600)
    continuous_min: Optional[float] = Field(None, ge=0, le=600)
    distance_km: Optional[float] = Field(None, gt=0, le=300)
    continuous_m: Optional[float] = Field(None, ge=0, le=10000)
    total_m: Optional[float] = Field(None, gt=0, le=10000)
    total_sec: Optional[float] = Field(None, gt=0, le=36000)
    comfortable: Optional[bool] = True
    avg_hr: Optional[float] = Field(None, gt=40, lt=230)
    avg_power: Optional[float] = Field(None, gt=0, lt=2000)
    rpe: Optional[float] = Field(None, ge=1, le=10)
    months_lifting: Optional[float] = Field(None, ge=0, le=600)


REQUIRED = {"run": "continuous_min", "swim": "continuous_m", "bike": "minutes", "gym": "months_lifting"}


@router.get("/levels")
def get_levels(user: User = Depends(current_user), db: DB = Depends(get_db)):
    from .. import levels
    changes = levels.weekly_update(db, user)
    return {**levels.overview(db, user), "recent_changes": changes}


@router.post("/levels/{sport}/assess")
def assess_level(sport: str, body: AssessIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    from .. import levels
    if sport not in REQUIRED:
        raise HTTPException(404, "Unknown sport")
    data = body.model_dump(exclude_none=True)
    if REQUIRED[sport] not in data:
        raise HTTPException(422, f"{REQUIRED[sport].replace('_', ' ')} is required for a {sport} level check")
    try:
        return levels.assess(db, user, sport, data, body.session_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class StageIn(BaseModel):
    stage: int = Field(ge=0, le=6)


@router.put("/levels/{sport}")
def set_level(sport: str, body: StageIn, user: User = Depends(current_user), db: DB = Depends(get_db)):
    from .. import levels
    from ..engine import ladder as L
    if sport not in L.LADDERS or body.stage >= len(L.LADDERS[sport]):
        raise HTTPException(422, "Unknown sport or stage")
    return levels.set_stage(db, user, sport, body.stage)
