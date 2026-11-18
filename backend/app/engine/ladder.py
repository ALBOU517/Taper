"""
Progression ladders: from "never done it" to performance, per sport.

Each sport has ordered stages. A stage defines what the athlete can safely do
(longest continuous effort, longest session, whether intensity work is allowed)
and how sessions look at that stage (walk-run intervals, short swim lengths…).

1. **Placement.** A level-check session measures where the athlete is today
   (minutes run without stopping, distance in 20 min, swim distance before the
   first stop, ride duration/speed, lifting experience).
2. **Progression.** After each week, the stage's progress moves up when the
   athlete completed the work at a comfortable effort, holds when it was
   patchy, and steps back when sessions were missed or easy work felt hard.
3. **Projection.** The planner assumes nominal progress (``weeks`` per stage)
   to lay out the season, and is re-run whenever the real stage changes.

Sources for the structure (the stage boundaries themselves are coaching design
choices, documented here so they can be tuned):
  - Run: walk-run → continuous progression as in NHS "Couch to 5K" (9 weeks to
    30 min continuous); pace bands from the 20-min distance.
  - Run threshold: a ~30-min solo time trial approximates lactate threshold
    (Friel, *The Triathlete's Training Bible*); a moderate 20-min effort is
    slower, so threshold pace is estimated as trial pace ÷ 1.10 (approximate,
    refined at the next check).
  - Bike FTP: 95 % of mean power in an all-out 20-min test (Allen & Coggan,
    *Training and Racing with a Power Meter*). Only set when the test was hard.
  - Swim: critical swim speed (Wakayoshi 1992) is close to sustainable 400 m
    pace; the 400 m check pace is used as the starting CSS.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class Stage:
    index: int
    name: str
    description: str
    criteria: str              # what places you here (shown in the UI)
    session_max: int           # minutes, longest regular session
    long_max: int              # minutes, longest long session (0 = none)
    quality: bool              # intensity sessions allowed
    weeks: int                 # typical weeks at this stage before moving up
    format: str = "normal"     # normal | walkrun1 | walkrun2 | swim_learn | swim_lengths | gym_learn | gym_found


RUN = [
    Stage(0, "First steps", "Short run intervals between walks. Builds tendons and bones before volume.",
          "Can't run 5 minutes without stopping", 30, 0, False, 3, "walkrun1"),
    Stage(1, "Run-walk", "Longer run blocks, shorter walks, until running 20 minutes straight.",
          "Runs 5–14 minutes without stopping", 35, 40, False, 3, "walkrun2"),
    Stage(2, "Continuous runner", "Easy continuous runs; strides for leg speed.",
          "Runs 15–20 minutes non-stop, under 3 km in 20 min", 35, 45, False, 3),
    # stages 0–2 take 3 + 3 + 3 weeks, the NHS Couch to 5K progression (zero to 30 min non-stop in 9 weeks)
    Stage(3, "5K runner", "First long runs and light intensity (hills, fartlek).",
          "3.0–3.8 km in a moderate 20 minutes", 45, 65, True, 6),
    Stage(4, "10K runner", "Longer long runs, tempo work.",
          "3.8–4.6 km in a moderate 20 minutes", 60, 95, True, 8),
    Stage(5, "Half-marathon runner", "Long runs up to ~2 h, threshold and interval sessions.",
          "4.6–5.3 km in a moderate 20 minutes", 75, 135, True, 10),
    Stage(6, "Performance", "Full training: marathon-length long runs, VO2max, race pace.",
          "Over 5.3 km in a moderate 20 minutes", 90, 180, True, 12),
]

SWIM = [
    Stage(0, "Water confidence", "Kick, float and drill in short lengths with full rest.",
          "Can't swim 25 m without stopping", 30, 0, False, 4, "swim_learn"),
    Stage(1, "Lengths", "Single lengths with rest; technique every session.",
          "Swims 25–99 m without stopping", 35, 0, False, 4, "swim_lengths"),
    Stage(2, "100s", "Repeats of 100 m; 1–1.5 km per session.",
          "Swims 100–399 m without stopping", 45, 45, False, 6),
    Stage(3, "Continuous 400", "Critical-swim-speed sets and longer steady swims.",
          "Swims 400 m non-stop, slower than 1:45 per 100 m", 55, 60, True, 8),
    Stage(4, "Open-water ready", "1.5–3 km sessions, race-pace sets.",
          "400 m non-stop at 1:30–1:45 per 100 m", 65, 75, True, 10),
    Stage(5, "Performance", "Full squad-style training.",
          "400 m non-stop faster than 1:30 per 100 m", 80, 90, True, 12),
]

BIKE = [
    Stage(0, "Getting comfortable", "Short easy rides to build time in the saddle.",
          "Couldn't ride 30 minutes comfortably", 35, 45, False, 3),
    Stage(1, "Endurance base", "Steady rides up to about 75 minutes.",
          "Rode 30 min comfortably, under 20 km/h", 60, 90, False, 4),
    Stage(2, "Club rider", "Long rides up to 2 h, sweet-spot work.",
          "20–26 km/h for 30 minutes", 75, 150, True, 8),
    Stage(3, "Strong rider", "Long rides up to 3 h, threshold intervals.",
          "26–31 km/h for 30 minutes", 90, 210, True, 10),
    Stage(4, "Performance", "Full training including VO2max and 4 h+ rides.",
          "Over 31 km/h for 30 minutes", 120, 360, True, 12),
]

GYM = [
    Stage(0, "Learn the movements", "Machines and light free weights, 2 sets of 12, technique first.",
          "Less than a month of regular lifting", 50, 0, False, 4, "gym_learn"),
    Stage(1, "Foundation", "3 sets of 8–12 on the main lifts.",
          "1–6 months of regular lifting", 60, 0, False, 8, "gym_found"),
    Stage(2, "Strength", "Heavier compound work, 4–6 reps.",
          "6–24 months of regular lifting", 75, 0, False, 12),
    Stage(3, "Advanced", "Periodised strength alongside endurance.",
          "Over 2 years of regular lifting", 90, 0, False, 16),
]

LADDERS = {"run": RUN, "swim": SWIM, "bike": BIKE, "gym": GYM}

# sessions per week allowed at each stage (beginners need recovery days between runs;
# Couch to 5K uses 3 runs a week). Design choice, same order as the stages above.
PER_WEEK = {"run": [3, 3, 4, 4, 5, 6, 6], "swim": [2, 2, 3, 3, 4, 4], "bike": [2, 3, 3, 4, 4], "gym": [2, 3, 4, 5]}


def max_sessions(sport: str, index: int) -> int:
    lst = PER_WEEK[sport]
    return lst[max(0, min(index, len(lst) - 1))]

# minimum stage needed on race day, per event and sport (design choice: the stage whose
# long session covers the race duration at an easy effort)
EVENT_REQUIRES = {
    "5k": {"run": 3}, "10k": {"run": 4}, "half_marathon": {"run": 5}, "marathon": {"run": 6},
    "sprint_tri": {"run": 3, "swim": 3, "bike": 1}, "olympic_tri": {"run": 4, "swim": 4, "bike": 2},
    "half_ironman": {"run": 5, "swim": 4, "bike": 3}, "ironman": {"run": 6, "swim": 4, "bike": 4},
    "gran_fondo": {"bike": 2}, "general": {}, "custom": {},
}

# starting stage when there's no level check yet, by self-reported experience
DEFAULT_STAGE = {"beginner": {"run": 1, "swim": 1, "bike": 1, "gym": 1},
                 "intermediate": {"run": 3, "swim": 2, "bike": 2, "gym": 2},
                 "advanced": {"run": 4, "swim": 3, "bike": 3, "gym": 2}}

LEVEL_CHECK = {
    "run": ("Level check: run", "Run: Level check 20min @ RPE 6",
            "Run 20 minutes at a steady effort where you could still say short sentences (RPE 6/10). "
            "Walk whenever you need to. Afterwards, note how many minutes you ran before your first walk break, "
            "the total distance, and your average heart rate if you wear a watch."),
    "swim": ("Level check: swim", "Swim: Level check 400m @ RPE 6",
             "Swim up to 400 m at an easy-steady pace (RPE 6/10). Stop whenever you need to. "
             "Note the distance before your first stop and the time for the whole swim."),
    "bike": ("Level check: ride", "Bike: Level check 30min @ RPE 6",
             "Ride 30 minutes on flat roads or a trainer at a steady effort (RPE 6/10). "
             "Note the distance (or average speed), whether you finished comfortably, and average power if you have it."),
}


def stage(sport: str, index: int) -> Stage:
    ladder = LADDERS[sport]
    return ladder[max(0, min(index, len(ladder) - 1))]


# ------------------------------------------------------------------ placement
@dataclass
class Placement:
    sport: str
    stage: int
    reasons: list
    thresholds: dict            # updates to the athlete's thresholds (only those we can justify)

    def to_dict(self) -> dict:
        return asdict(self)


def place_run(continuous_min: float, distance_km: Optional[float], minutes: float = 20,
              rpe: Optional[float] = None, avg_hr: Optional[float] = None, age: Optional[int] = None) -> Placement:
    reasons, th = [], {}
    if continuous_min < 5:
        s = 0
        reasons.append(f"Ran {continuous_min:g} min before the first walk break")
    elif continuous_min < 15:
        s = 1
        reasons.append(f"Ran {continuous_min:g} min before the first walk break")
    else:
        km20 = (distance_km or 0) * 20 / max(minutes, 1)
        bands = [(3.0, 2), (3.8, 3), (4.6, 4), (5.3, 5)]
        s = next((st for lim, st in bands if km20 < lim), 6)
        if continuous_min < minutes and s > 2:
            s = 2           # couldn't run the whole check: build continuity first
            reasons.append("Fast, but walked during the check: continuity comes first")
        reasons.append(f"{km20:.2f} km in 20 minutes" if distance_km else "No distance logged")
    if distance_km and continuous_min >= 15:
        pace = minutes * 60 / distance_km                      # s/km at moderate effort
        factor = 1.10
        if rpe and rpe >= 8:
            factor = 1.03                                      # it was close to a time trial
        elif avg_hr and age and avg_hr >= 0.88 * (208 - 0.7 * age):  # Tanaka HRmax
            factor = 1.04
        th["run_threshold_pace"] = round(pace / factor)
    return Placement("run", s, reasons, th)


def place_swim(continuous_m: float, total_m: Optional[float] = None, total_sec: Optional[float] = None) -> Placement:
    reasons = [f"Swam {continuous_m:g} m before the first stop"]
    th = {}
    if continuous_m < 25:
        s = 0
    elif continuous_m < 100:
        s = 1
    elif continuous_m < 400:
        s = 2
    else:
        per100 = (total_sec / (total_m or 400) * 100) if total_sec else None
        if per100 is None:
            s = 3
        else:
            s = 5 if per100 < 90 else (4 if per100 < 105 else 3)
            reasons.append(f"{int(per100 // 60)}:{int(per100 % 60):02d} per 100 m")
            th["swim_css"] = round(per100)
    return Placement("swim", s, reasons, th)


def place_bike(minutes: float, distance_km: Optional[float], comfortable: bool = True,
               avg_power: Optional[float] = None, rpe: Optional[float] = None) -> Placement:
    reasons, th = [], {}
    if minutes < 30 or not comfortable:
        s = 0
        reasons.append("30 minutes wasn't comfortable yet")
    else:
        kmh = (distance_km or 0) / (minutes / 60)
        s = 1 if kmh < 20 else 2 if kmh < 26 else 3 if kmh < 31 else 4
        reasons.append(f"{kmh:.1f} km/h for {minutes:g} min" if distance_km else "No distance logged")
        if distance_km:
            th["bike_threshold_kmh"] = round(kmh * 1.12, 1)     # moderate effort ≈ 0.8 FTP → speed ∝ √power
    if avg_power and rpe and rpe >= 8 and minutes >= 20:
        th["bike_ftp"] = round(avg_power * 0.95)
        reasons.append("FTP set from a hard 20-min effort (95 % of average power)")
    return Placement("bike", s, reasons, th)


def place_gym(months_lifting: float) -> Placement:
    s = 0 if months_lifting < 1 else 1 if months_lifting < 6 else 2 if months_lifting < 24 else 3
    return Placement("gym", s, [f"{months_lifting:g} months of regular lifting"], {})


# ------------------------------------------------------------------ progression
@dataclass
class WeekStats:
    planned: int
    done: int
    easy_rpes: list            # RPE logged on easy/long sessions


def progress(stage_i: int, prog: float, sport: str, w: WeekStats) -> tuple[int, float, str]:
    """One week's update. Returns (stage, progress 0..1, explanation)."""
    st = stage(sport, stage_i)
    top = len(LADDERS[sport]) - 1
    if w.planned == 0:
        return stage_i, prog, "No sessions planned"
    compliance = w.done / w.planned
    rpe = sum(w.easy_rpes) / len(w.easy_rpes) if w.easy_rpes else None
    step = 1 / st.weeks
    if compliance >= 0.75 and (rpe is None or rpe <= 6):
        prog += step
        why = f"Completed {w.done}/{w.planned}" + (f", easy effort felt {rpe:.0f}/10" if rpe else "")
    elif compliance < 0.5 or (rpe is not None and rpe >= 8):
        prog -= step
        why = f"Completed {w.done}/{w.planned}" + (f", easy sessions felt hard ({rpe:.0f}/10)" if rpe else "") + \
              " — easing off"
    else:
        why = f"Completed {w.done}/{w.planned} — holding this level another week"
    if prog >= 1 and stage_i < top:
        return stage_i + 1, 0.0, why + f" → moved up to {stage(sport, stage_i + 1).name}"
    if prog < 0 and stage_i > 0:
        return stage_i - 1, 0.5, why + f" → back to {stage(sport, stage_i - 1).name}"
    return stage_i, max(0.0, min(prog, 1.0)), why


def project(stage_i: int, prog: float, sport: str, weeks_ahead: int) -> int:
    """Expected stage after ``weeks_ahead`` weeks of nominal progress."""
    s, p = stage_i, prog
    top = len(LADDERS[sport]) - 1
    for _ in range(weeks_ahead):
        if s >= top:
            break
        p += 1 / stage(sport, s).weeks
        if p >= 1:
            s, p = s + 1, 0.0
    return s


def weeks_to_reach(stage_i: int, prog: float, sport: str, target: int) -> Optional[int]:
    for w in range(0, 260):
        if project(stage_i, prog, sport, w) >= target:
            return w
    return None


# ------------------------------------------------------------------ stage-specific sessions
def stage_session(sport: str, st: Stage, kind: str, minutes: float, variant: int = 0) -> Optional[tuple[str, str]]:
    """DSL for stages whose sessions look different from normal training, else None."""
    m = int(max(15, minutes))
    if st.format == "walkrun1":
        reps = max(4, min(10, (m - 10) // 3))
        return "Run-walk intervals", f"Run: 5min Walk @ Z1 -> {reps}x(1min @ Z2, 2min Walk @ Z1) -> 5min Walk @ Z1"
    if st.format == "walkrun2":
        reps = max(3, min(6, (m - 10) // 6))
        return "Run-walk intervals", f"Run: 5min Walk @ Z1 -> {reps}x(5min @ Z2, 1min Walk @ Z1) -> 5min Walk @ Z1"
    if st.format == "swim_learn":
        return "Water confidence", ("Swim: 4x25m Kick with board @ Z2 (60s rest) -> 6x25m Drill @ Z2 (60s rest) "
                                    "-> 4x25m Freestyle @ Z2 (60s rest)")
    if st.format == "swim_lengths":
        reps = max(8, min(16, m // 3))
        return "Swim lengths", f"Swim: 4x25m Drill @ Z2 (40s rest) -> {reps}x25m Freestyle @ Z2 (30s rest) -> 100m Easy"
    if st.format == "gym_learn":
        return "Strength — learn the movements", ("Gym: Goblet Squat 2x12 @ RPE 6 | Leg Press 2x12 @ RPE 6 | "
                                                 "Lat Pulldown 2x12 @ RPE 6 | Chest Press 2x12 @ RPE 6 | Plank Hold 2x1")
    if st.format == "gym_found":
        if variant % 2 == 1:     # alternate with an upper-body day so legs get 48 h between sessions
            return "Strength — foundation (upper)", ("Gym: Dumbbell Bench Press 3x10 @ RPE 7 | Seated Cable Row 3x10 @ RPE 7 | "
                                                    "Superset: Shoulder Press 3x10 + Lat Pulldown 3x10")
        return "Strength — foundation", ("Gym: Goblet Squat 3x10 @ RPE 7 | Romanian Deadlift 3x10 @ RPE 7 | "
                                        "Superset: Lat Pulldown 3x10 + Dumbbell Bench Press 3x10")
    if st.format == "gym_learn" and variant % 2 == 1:
        return "Strength — learn the movements (upper)", ("Gym: Chest Press 2x12 @ RPE 6 | Seated Cable Row 2x12 @ RPE 6 | "
                                                          "Lat Pulldown 2x12 @ RPE 6 | Plank Hold 2x1")
    return None
