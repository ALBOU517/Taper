"""
Nutrition engine: energy expenditure, training-aware daily targets, macros and
session fueling.

Every constant below comes from a published source, cited inline. Where a
choice is a design decision rather than a published number, the comment says so.

References
----------
[MSJ]   Mifflin MD, St Jeor ST et al. A new predictive equation for resting energy
        expenditure in healthy individuals. Am J Clin Nutr 1990;51:241-7.
[ACSM-R] ACSM's Guidelines for Exercise Testing and Prescription, metabolic
        equations: running VO2 = 0.2·speed(m/min) + 3.5 ml/kg/min (net 0.2·speed).
[CPA]   Ainsworth BE et al. Compendium of Physical Activities, 2011 update
        (resistance training 3.5 / 5.0 / 6.0 MET; freestyle swimming 5.8 / 9.8 MET).
[EFF]   Gross mechanical efficiency of cycling ≈ 20-25 % (Coyle 1992; Ettema &
        Lorås 2009) — 1 kJ of work ≈ 1 kcal of metabolic energy at ~24 %.
[TH16]  Thomas DT, Erdman KA, Burke LM. ACSM/AND/DC Joint Position Statement:
        Nutrition and Athletic Performance. Med Sci Sports Exerc 2016;48:543-68.
[J14]   Jeukendrup A. A step towards personalized sports nutrition: carbohydrate
        intake during exercise. Sports Med 2014;44(S1):S25-33.
[ISSN]  Jäger R et al. ISSN Position Stand: protein and exercise. JISSN 2017;14:20.
[H08]   Hall KD. What is the required energy deficit per unit weight loss?
        Int J Obes 2008;32:573-6 (≈7700 kcal per kg of body-weight change).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

KCAL_PER_KG = 7700.0            # [H08]
KCAL_PER_L_O2 = 5.0             # standard caloric equivalent of oxygen
CYCLING_EFFICIENCY = 0.24       # [EFF]

# Non-exercise activity multiplier on BMR (steps, standing, daily life — *not* training,
# which is added per session). Design choice anchored on the classic PAL scale
# (1.2 sedentary … 1.55 active) with the training component removed.
NEAT_FACTORS = {"low": 1.20, "moderate": 1.35, "high": 1.50}

# [TH16] Table 1: daily carbohydrate targets by training load (g/kg/day)
CARB_BANDS = {
    "rest":      (3.0, 5.0),    # no training: bottom of the "light" band
    "light":     (3.0, 5.0),    # low intensity or skill-based
    "moderate":  (5.0, 7.0),    # ~1 h/day moderate exercise
    "high":      (6.0, 10.0),   # 1-3 h/day moderate-high intensity
    "very_high": (8.0, 12.0),   # >4-5 h/day
}
CARB_FLOOR_DEFICIT = 2.0        # design choice: lowest carbs we'll prescribe on a deficit rest day
FAT_MIN_PCT, FAT_MAX_PCT = 0.20, 0.35   # [TH16] 20-35 % of energy from fat
FAT_MIN_G_PER_KG = 0.6                  # design floor so very low-kcal days keep some fat

PROTEIN_DEFAULT = {"lose": 2.0, "recomp": 2.0, "maintain": 1.6, "gain": 1.8}   # [ISSN] 1.4-2.0 g/kg; higher end in deficit
GOAL_RATE_DEFAULT = {"lose": -0.5, "recomp": -0.25, "maintain": 0.0, "gain": 0.25}  # % body weight per week
MAX_RATE = 1.0                  # cap |rate| at 1 %/week


@dataclass
class Body:
    sex: str                    # "male" | "female"
    age: int
    height_cm: float
    weight_kg: float
    neat: str = "moderate"


@dataclass
class Goal:
    mode: str = "recomp"        # lose | recomp | maintain | gain
    rate_pct_per_week: Optional[float] = None
    protein_g_per_kg: Optional[float] = None

    @property
    def rate(self) -> float:
        r = GOAL_RATE_DEFAULT[self.mode] if self.rate_pct_per_week is None else self.rate_pct_per_week
        return max(-MAX_RATE, min(MAX_RATE, r))

    @property
    def protein(self) -> float:
        return self.protein_g_per_kg or PROTEIN_DEFAULT[self.mode]


@dataclass
class SessionEnergy:
    sport: str
    minutes: float
    distance_m: float = 0.0
    intensity: float = 0.7      # intensity factor (see load.py)
    rpe: Optional[float] = None
    avg_power: Optional[float] = None
    kind: str = "easy"
    title: str = ""
    start: Optional[str] = None  # "HH:MM"
    heavy: bool = False          # gym: heavy squat/deadlift work


# ------------------------------------------------------------------ expenditure
def bmr(b: Body) -> float:
    """Resting energy expenditure, Mifflin-St Jeor [MSJ]."""
    s = 5 if b.sex == "male" else -161
    return 10 * b.weight_kg + 6.25 * b.height_cm - 5 * b.age + s


def base_expenditure(b: Body) -> float:
    """Daily expenditure *excluding* planned training (prior estimate)."""
    return bmr(b) * NEAT_FACTORS.get(b.neat, 1.35)


def session_kcal(s: SessionEnergy, kg: float, ftp: float = 200.0, run_threshold_pace: float = 300.0) -> float:
    """Net energy of a session (above resting), kcal."""
    hours = s.minutes / 60
    if s.sport == "run":
        km = s.distance_m / 1000 if s.distance_m else hours * 3600 / (run_threshold_pace / max(s.intensity, 0.4))
        # [ACSM-R] net VO2 = 0.2 ml/kg/min per m/min → 0.2·kg·distance_m ml O2 → ≈1.0 kcal/kg/km
        return 0.2 * kg * km * 1000 / 1000 * KCAL_PER_L_O2
    if s.sport == "bike":
        watts = s.avg_power or s.intensity * ftp
        work_kj = watts * hours * 3600 / 1000
        gross = work_kj / 4.184 / CYCLING_EFFICIENCY          # [EFF]
        resting = 1.0 * kg * hours                             # 1 MET = 1 kcal/kg/h
        return max(0.0, gross - resting)
    if s.sport == "swim":
        # [CPA] freestyle light/moderate 5.8 MET, vigorous 9.8 MET; interpolate on intensity
        met = min(11.0, max(4.0, 5.8 + (s.intensity - 0.7) * 20))
        return (met - 1) * kg * hours
    if s.sport == "walk":
        # [CPA] walking 3.0 mph 3.5 MET, 3.5 mph brisk 4.3 MET, uphill / hiking 6.0 MET
        met = 3.5 if s.intensity < 0.6 else (4.3 if s.intensity < 0.8 else 6.0)
        return (met - 1) * kg * hours
    if s.sport == "gym":
        # [CPA] codes, whole-session values (rest between sets included):
        #   02054  3.5 MET  multiple exercises, 8-15 reps at varied resistance   ← typical split session
        #   02052  5.0 MET  squats / deadlifts, slow or explosive effort         ← heavy lower-body session
        #   02050  6.0 MET  power lifting or body building, vigorous effort      ← logged RPE ≥ 9
        met = 6.0 if (s.rpe or 0) >= 9 else (5.0 if s.heavy else 3.5)
        return (met - 1) * kg * hours
    if s.sport == "mobility":
        # [CPA] 02101 stretching, mild 2.3 MET; 02150 yoga, hatha 2.5 MET
        return (2.4 - 1) * kg * hours
    if s.sport == "other":
        # outside training the athlete already does (fight practice, climbing, team sport)
        # [CPA] "sports, general" spans ~5–10 MET; design choice: hard 8.0, easy 5.0
        return ((8.0 if s.intensity >= 0.75 else 5.0) - 1) * kg * hours
    return 0.0


# ------------------------------------------------------------------ adaptive expenditure
@dataclass
class DayRecord:
    day: date
    intake_kcal: Optional[float] = None     # None = not logged
    weight_kg: Optional[float] = None
    exercise_kcal: float = 0.0              # net, from completed sessions
    complete: bool = False                  # user confirmed the food log is complete


def weight_trend(points: Iterable[tuple[date, float]], alpha: float = 0.1) -> dict[date, float]:
    """Exponentially smoothed weight (filters water / glycogen / creatine noise)."""
    pts = sorted(points)
    if not pts:
        return {}
    out: dict[date, float] = {}
    t = pts[0][1]
    d, i = pts[0][0], 0
    end = pts[-1][0]
    while d <= end:
        if i < len(pts) and pts[i][0] == d:
            t += alpha * (pts[i][1] - t)
            i += 1
        out[d] = round(t, 2)
        d += timedelta(days=1)
    return out


def adaptive_base(records: list[DayRecord], prior: float, min_days: int = 14, window: int = 28) -> dict:
    """Estimate non-training expenditure from the energy balance of real data.

        expenditure ≈ mean intake − Δtrend_weight × 7700 / days        [H08]
        base        = expenditure − mean training energy

    Only days marked complete count as intake. The estimate is blended with the
    prior in proportion to how much data there is (design choice: full weight
    after 4 weeks, capped at 90 %).
    """
    recs = sorted(records, key=lambda r: r.day)[-window:]
    logged = [r for r in recs if r.complete and r.intake_kcal]
    weights = [(r.day, r.weight_kg) for r in recs if r.weight_kg]
    result = {"base": round(prior), "prior": round(prior), "observed": None, "confidence": 0.0,
              "days_logged": len(logged), "weigh_ins": len(weights)}
    if len(logged) < min_days or len(weights) < 4:
        return result
    trend = weight_trend(weights)
    first, last = min(trend), max(trend)
    span = (last - first).days
    if span < 7:
        return result
    mean_intake = sum(r.intake_kcal for r in logged) / len(logged)
    mean_exercise = sum(r.exercise_kcal for r in recs) / len(recs)
    expenditure = mean_intake - (trend[last] - trend[first]) * KCAL_PER_KG / span
    observed = expenditure - mean_exercise
    w = min(0.9, len(logged) / window)
    result.update(base=round(prior * (1 - w) + observed * w), observed=round(observed),
                  confidence=round(w, 2), expenditure=round(expenditure),
                  trend_change_kg=round(trend[last] - trend[first], 2), span_days=span)
    return result


# ------------------------------------------------------------------ daily targets
@dataclass
class DayTarget:
    day: date
    kcal: int
    protein_g: int
    carbs_g: int
    fat_g: int
    load: str
    training_kcal: int
    goal_adjust_kcal: int
    base_kcal: int
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["day"] = self.day.isoformat()
        return d


def classify_load(sessions: list[SessionEnergy]) -> str:
    """Map a day's training to a [TH16] load band (gym counts at half weight: design choice)."""
    hours = sum(s.minutes for s in sessions if s.sport != "gym") / 60
    hours += sum(s.minutes for s in sessions if s.sport == "gym") / 60 * 0.5
    if hours == 0:
        return "rest"
    if hours < 1:
        return "light"
    if hours < 2:
        return "moderate"
    if hours < 4:
        return "high"
    return "very_high"


def macros(kcal: float, kg: float, load: str, goal: Goal, carb_load: bool = False) -> tuple[int, int, int, list]:
    notes = []
    p = goal.protein * kg
    lo, hi = (10.0, 12.0) if carb_load else CARB_BANDS[load]
    # design choice: aim low in the band when losing, mid-band otherwise
    frac = 0.0 if load == "rest" else (0.25 if goal.rate < 0 else 0.5)
    c = (lo + (hi - lo) * frac) * kg
    fat_floor = max(FAT_MIN_G_PER_KG * kg, FAT_MIN_PCT * kcal / 9)
    f = (kcal - 4 * p - 4 * c) / 9
    if f < fat_floor:
        c_min = (CARB_FLOOR_DEFICIT if goal.rate < 0 and not carb_load else lo) * kg
        c = max(c_min, (kcal - 4 * p - 9 * fat_floor) / 4)
        f = (kcal - 4 * p - 4 * c) / 9
        if c < lo * kg:
            notes.append(f"Carbs below the {lo:g}-{hi:g} g/kg guideline to keep the deficit — protein and fat protected")
    if f > FAT_MAX_PCT * kcal / 9:
        f = FAT_MAX_PCT * kcal / 9
        c = (kcal - 4 * p - 9 * f) / 4
    f = max(f, 0)
    return round(p), round(c), round(f), notes


def week_targets(days: list[date], sessions_by_day: dict[date, list[SessionEnergy]], body: Body, goal: Goal,
                 base: Optional[float] = None, ftp: float = 200.0, run_threshold_pace: float = 300.0,
                 race_day: Optional[date] = None) -> list[DayTarget]:
    """Targets for consecutive days (normally Mon-Sun).

    The weekly energy adjustment (deficit or surplus) is spread so that hard days
    get *less* deficit and rest days more — same weekly total, but key sessions
    are never under-fuelled (design choice following the "fuel for the work
    required" principle [TH16]).
    """
    kg = body.weight_kg
    base = base if base is not None else base_expenditure(body)
    floor = bmr(body)
    train = {d: sum(session_kcal(s, kg, ftp, run_threshold_pace) for s in sessions_by_day.get(d, [])) for d in days}
    weekly_adj = goal.rate / 100 * kg * KCAL_PER_KG * len(days) / 7
    if weekly_adj < 0:
        w = {d: 1 / (1 + train[d] / 300) for d in days}
    else:
        w = {d: 1 + train[d] / 300 for d in days}
    total_w = sum(w.values()) or 1
    out = []
    for d in days:
        adj = weekly_adj * w[d] / total_w
        kcal = base + train[d] + adj
        notes = []
        if kcal < floor + train[d]:
            kcal = floor + train[d]
            notes.append("Raised to your resting expenditure plus training — eating less would cost muscle")
        sessions = sessions_by_day.get(d, [])
        load = classify_load(sessions)
        carb_load = bool(race_day and 0 < (race_day - d).days <= 2 and _long_race(sessions_by_day.get(race_day, [])))
        if carb_load:
            notes.append("Carb-loading: 10-12 g/kg carbs in the 36-48 h before a race over 90 min [ACSM 2016]")
        p, c, f, mnotes = macros(kcal, kg, load, goal, carb_load)
        kcal_from_macros = 4 * p + 4 * c + 9 * f
        out.append(DayTarget(d, round(kcal_from_macros), p, c, f, load, round(train[d]), round(adj), round(base),
                             notes + mnotes))
    return out


def _long_race(sessions: list[SessionEnergy]) -> bool:
    return sum(s.minutes for s in sessions if s.kind == "race") > 90


# ------------------------------------------------------------------ fueling
def _hhmm_shift(start: Optional[str], minutes: int) -> Optional[str]:
    if not start:
        return None
    h, m = map(int, start.split(":"))
    t = (h * 60 + m + minutes) % (24 * 60)
    return f"{t // 60:02d}:{t % 60:02d}"


def fuel_plan(s: SessionEnergy, kg: float, hours_to_next_session: Optional[float] = None) -> list[dict]:
    """Before / during / after guidance for one session.

    Carbohydrate during exercise follows [J14]:
        < 45 min: none needed · 45-75 min: small amounts or mouth rinse
        1-2 h: up to 30 g/h · 2-3 h: up to 60 g/h · > 2.5 h: up to 90 g/h (glucose+fructose)
    Pre-exercise 1-4 g/kg 1-4 h before sessions > 60 min; post-exercise protein
    0.3 g/kg; rapid refuelling 1.0-1.2 g/kg/h for 4 h when the next session is
    < 8 h away [TH16]. Fluids 0.4-0.8 L/h, individualised [TH16].
    """
    m = s.minutes
    steps: list[dict] = []
    endurance = s.sport in ("run", "bike", "swim") or (s.sport == "walk" and m > 120)

    if endurance and m > 60:
        g = round(min(4.0, 1.0 + (m - 60) / 60) * kg / 5) * 5
        steps.append({"phase": "before", "offset_min": -180, "at": _hhmm_shift(s.start, -180),
                      "text": f"Carb-rich meal: ~{g} g carbs, moderate protein, low fat and fibre",
                      "carbs_g": g, "protein_g": None, "fluids_ml": 500})
        steps.append({"phase": "before", "offset_min": -45, "at": _hhmm_shift(s.start, -45),
                      "text": "Optional top-up: 20-30 g easy carbs (banana, toast, sports drink)",
                      "carbs_g": 25, "protein_g": None, "fluids_ml": 250})
    elif s.sport == "gym":
        g = round(0.3 * kg / 5) * 5
        steps.append({"phase": "before", "offset_min": -120, "at": _hhmm_shift(s.start, -120),
                      "text": f"Meal or snack with ~{g} g protein and some carbs 1-3 h before",
                      "carbs_g": None, "protein_g": g, "fluids_ml": 400})
    else:
        steps.append({"phase": "before", "offset_min": -120, "at": _hhmm_shift(s.start, -120),
                      "text": "Normal meal 2-3 h before is enough; no special fuelling needed",
                      "carbs_g": None, "protein_g": None, "fluids_ml": 300})

    if endurance:
        if m < 45:
            per_hour, txt = 0, "Water only"
        elif m <= 75:
            per_hour, txt = 0, "Water; a carb drink mouth-rinse can help on hard efforts"
        elif m <= 120:
            per_hour, txt = 30, "Up to 30 g carbs per hour"
        elif m <= 150:
            per_hour, txt = 60, "Up to 60 g carbs per hour"
        else:
            per_hour, txt = 90, "Up to 90 g carbs per hour — needs glucose + fructose products; practise in training"
        fluids = 600 if m > 60 else 400
        steps.append({"phase": "during", "offset_min": 0, "at": s.start, "text": f"{txt}. Drink ~{fluids} ml per hour"
                      + ("; add sodium on sessions over 2 h" if m > 120 else ""),
                      "carbs_g": per_hour, "protein_g": None, "fluids_ml": fluids, "per_hour": True})
        if per_hour:
            every = 20 if per_hour >= 60 else 30
            dose = round(per_hour * every / 60)
            n = int((m - 20) // every) + 1
            for i in range(n):
                off = 20 + i * every
                if off >= m:
                    break
                steps.append({"phase": "during", "offset_min": off, "at": _hhmm_shift(s.start, off),
                              "text": f"{dose} g carbs", "carbs_g": dose, "protein_g": None, "fluids_ml": None,
                              "dose": True})

    pg = round(0.3 * kg / 5) * 5
    if hours_to_next_session is not None and hours_to_next_session < 8 and endurance:
        cg = round(1.1 * kg / 5) * 5
        steps.append({"phase": "after", "offset_min": m, "at": _hhmm_shift(s.start, m),
                      "text": f"Next session in {hours_to_next_session:.0f} h: ~{cg} g carbs per hour for up to 4 h, "
                              f"plus ~{pg} g protein", "carbs_g": cg, "protein_g": pg, "fluids_ml": 500})
    else:
        steps.append({"phase": "after", "offset_min": m + 30, "at": _hhmm_shift(s.start, m + 30),
                      "text": f"Within 2 h: ~{pg} g protein" + (" with carbs" if endurance and m >= 45 else ""),
                      "carbs_g": None, "protein_g": pg, "fluids_ml": 500})
    return steps
