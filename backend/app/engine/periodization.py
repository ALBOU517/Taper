"""
Reverse-periodization engine.

Given an anchor goal (race type + date), the athlete's weekly availability and
current fitness, build the whole season *backwards from race day*:

    [ Base ......... | Build ....... | Peak | Taper | Race ]
      3:1 load/deload cycles            hold   cut volume, keep intensity

Load targets come straight from the Banister model: to move fitness from
CTL_start to CTL_end over 7 days of constant daily load T,

    CTL_end = T + (CTL_start − T)·e^(−7/42)
    ⇒ T = (CTL_end − CTL_start·e^(−7/42)) / (1 − e^(−7/42))

so weekly TSS = 7·T. Ramp is capped per experience level to keep injury risk
low, and every generated week is calibrated (fixed-point iteration) so the TSS
of the *actual* scheduled workouts matches the target.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from . import dsl, ladder, templates
from .constraints import PlannedSession, group_by_day, violations
from .load import Thresholds, banister, estimate

DECAY_WEEK = math.exp(-7 / 42)

# ------------------------------------------------------------------ events
EVENTS: dict[str, dict] = {
    "5k": dict(name="5K", mix={"run": .85, "gym": .15}, taper=1, ctl=(30, 45, 60),
               long_cap={"run": 80}, race=["Run: Race 5km @ Z5"]),
    "10k": dict(name="10K", mix={"run": .85, "gym": .15}, taper=1, ctl=(35, 50, 65),
                long_cap={"run": 100}, race=["Run: Race 10km @ Z4"]),
    "half_marathon": dict(name="Half Marathon", mix={"run": .87, "gym": .13}, taper=2, ctl=(40, 55, 70),
                          long_cap={"run": 135}, race=["Run: Race 21.1km @ Z4"]),
    "marathon": dict(name="Marathon", mix={"run": .9, "gym": .1}, taper=3, ctl=(50, 65, 85),
                     long_cap={"run": 180}, race=["Run: Race 42.2km @ Z3"]),
    "sprint_tri": dict(name="Sprint Triathlon", mix={"swim": .2, "bike": .42, "run": .3, "gym": .08}, taper=1,
                       ctl=(35, 50, 65), long_cap={"run": 70, "bike": 120, "swim": 45},
                       race=["Swim: Race 750m @ Z4", "Bike: Race 20km @ Z4", "Run: Race 5km @ Z4"]),
    "olympic_tri": dict(name="Olympic Triathlon", mix={"swim": .2, "bike": .43, "run": .29, "gym": .08}, taper=2,
                        ctl=(45, 60, 75), long_cap={"run": 90, "bike": 180, "swim": 60},
                        race=["Swim: Race 1500m @ Z4", "Bike: Race 40km @ Z4", "Run: Race 10km @ Z4"]),
    "half_ironman": dict(name="70.3 Half Ironman", mix={"swim": .17, "bike": .5, "run": .27, "gym": .06}, taper=2,
                         ctl=(55, 70, 90), long_cap={"run": 120, "bike": 240, "swim": 70}, long_share={"bike": 0.5},
                         race=["Swim: Race 1900m @ Z3", "Bike: Race 90km @ Z3", "Run: Race 21.1km @ Z3"]),
    "ironman": dict(name="Ironman", mix={"swim": .15, "bike": .52, "run": .28, "gym": .05}, taper=3,
                    ctl=(65, 85, 110), long_cap={"run": 165, "bike": 360, "swim": 80}, long_share={"bike": 0.55},
                    race=["Swim: Race 3800m @ Z2", "Bike: Race 180km @ Z2", "Run: Race 42.2km @ Z2"]),
    "gran_fondo": dict(name="Gran Fondo (160 km)", mix={"bike": .9, "gym": .1}, taper=2, ctl=(40, 55, 70),
                       long_cap={"bike": 330}, long_share={"bike": 0.5}, race=["Bike: Race 160km @ Z2"]),
    "general": dict(name="General Fitness", mix={"run": .45, "gym": .55}, taper=0, ctl=(30, 40, 55),
                    long_cap={"run": 75}, race=[]),
}
LEVELS = {"beginner": 0, "intermediate": 1, "advanced": 2}
# design choice (common coaching practice): long sessions start modest and grow ~10 min per week
LONG_START = {"beginner": 60, "intermediate": 75, "advanced": 90}
LONG_STEP = 10
LONG_SPORT_FACTOR = {"run": 1.0, "bike": 2.0, "swim": 0.6, "walk": 1.6}
# long session = this share of the sport's weekly time (default 38 %, swim 35 %). Long-course events lean on
# the weekend ride: ~half of the week's riding in one long ride is common Ironman / gran fondo practice.
RAMP = {"beginner": 4.0, "intermediate": 5.0, "advanced": 6.0}   # max CTL gain / week
TAPER_CURVE = {1: [0.55], 2: [0.70, 0.50], 3: [0.75, 0.60, 0.45]}
MIN_SESSION = {"run": 25, "bike": 40, "swim": 30, "gym": 45, "walk": 20, "mobility": 10}
MOBILITY_MIN = 20      # design choice: a 20-min routine fits before work or between two sessions
GYM_MIN = 50
EXAM_FACTOR = 0.6     # design choice: exam weeks keep 60 % of planned load and become deload weeks
QUALITY_MIN = {"run": 55, "bike": 70, "swim": 50, "walk": 40}
QUALITY_MIN_TAPER = {"run": 45, "bike": 50, "swim": 40, "walk": 30}
EASY_TARGET = {"run": 45, "bike": 75, "swim": 45, "walk": 40}
MAX_SESSIONS = {"run": (6, 3), "bike": (5, 3), "swim": (4, 3), "walk": (6, 4)}   # (single-sport, multisport)
RACE_DISTANCE_KM = {"5k": 5.0, "10k": 10.0, "half_marathon": 21.0975, "marathon": 42.195}
SPORT_NAMES = {"run": "Running", "bike": "Cycling", "swim": "Swimming", "gym": "Strength", "walk": "Walking",
               "mobility": "Mobility"}
COUNTED = ("gym", "mobility")          # scheduled as N sessions a week, outside the endurance load model
NO_RACE = ("general", "custom")


def event_config(event: str, sports: Optional[list] = None, body_focus: bool = False) -> dict:
    """Training mix for a race (adjusted to the sports the athlete picked) or a race-free block.

    Race-free ("custom"): strength-led when the body goal is the focus (fat loss / muscle) and
    strength is picked — 55 % strength, cardio shares the rest; otherwise endurance-led with
    strength at 20 %. Design choice, tunable here.
    """
    if event != "custom":
        cfg = dict(EVENTS[event])
        cfg["mobility"] = bool(sports and "mobility" in sports)
        if sports:
            mix = {k: v for k, v in cfg["mix"].items() if k != "gym" or "gym" in sports}
            if "gym" in sports and "gym" not in mix:
                mix["gym"] = 0.12
            if "walk" in sports:
                mix["walk"] = 0.08
            cfg["mix"] = mix
        return cfg
    picked = [s for s in (sports or ["run"]) if s in MIN_SESSION]
    cardio = [s for s in picked if s not in COUNTED]
    if "gym" in picked and (body_focus or not cardio):
        gym_share = 0.55 if cardio else 1.0
    else:
        gym_share = 0.2 if "gym" in picked else 0.0
    mix = {s: (1 - gym_share) / len(cardio) for s in cardio} if cardio else {}
    if gym_share:
        mix["gym"] = gym_share
    return dict(name=" + ".join(SPORT_NAMES[s] for s in picked), mix=mix, taper=0, ctl=(25, 40, 55),
                long_cap={"run": 90, "bike": 180, "swim": 60, "walk": 150}, race=[], mobility="mobility" in picked)


def threshold_for_time(event: str, seconds: float) -> Optional[dict]:
    """Running pace needed for a target time, and the 1-hour (threshold) pace it implies.

    Riegel's endurance model T2 = T1 · (D2/D1)^1.06 (Riegel, *Am Sci* 1981) converts the goal
    into the distance you'd cover in 60 min — a common proxy for lactate-threshold pace.
    """
    d = RACE_DISTANCE_KM.get(event)
    if not d or not seconds:
        return None
    d60 = d * (3600 / seconds) ** (1 / 1.06)
    return {"race_pace": round(seconds / d), "threshold_pace": round(3600 / d60), "distance_km": d}


@dataclass
class Goal:
    event: str
    race_date: date
    start_date: date
    level: str = "intermediate"
    availability: dict = field(default_factory=lambda: {0: 60, 1: 75, 2: 60, 3: 75, 4: 0, 5: 120, 6: 150})
    baseline_ctl: Optional[float] = None
    baseline_hours: float = 4.0
    gym_per_week: int = 2
    recovery_every: int = 4          # 4 = 3 weeks load : 1 week deload
    name: Optional[str] = None
    # real calendar: date -> (free minutes, longest free window); falls back to weekday availability
    availability_by_date: dict = field(default_factory=dict)
    exam_weeks: set = field(default_factory=set)          # Mondays of weeks to lighten
    gym_routines: list = field(default_factory=list)      # [{"name", "dsl", "minutes"}] in rotation
    # sport -> {"stage": int, "progress": float, "assessed": bool}; missing sports get a level check
    levels: dict = field(default_factory=dict)
    sports: list = field(default_factory=list)       # what the athlete picked; empty = the event's default mix
    target_time_s: Optional[int] = None              # race goal time (run races)
    body_focus: bool = False                         # fat loss / muscle is the main goal
    equipment: str = "gym"                           # gym | home | none — what strength sessions can use
    away_days: set = field(default_factory=set)      # weekdays away from your usual equipment (travel) → "none"
    # training you already do elsewhere: [{"day": 0-6, "minutes", "name", "hard": bool}]
    commitments: list = field(default_factory=list)
    easy_dates: set = field(default_factory=set)     # recovery days (after night shifts): no hard or heavy work
    mobility_per_week: Optional[int] = None          # None = automatic


@dataclass
class WeekPlan:
    index: int
    start: date
    phase: str
    recovery: bool
    target_tss: float
    planned_tss: float
    minutes: int
    sport_minutes: dict
    notes: list = field(default_factory=list)
    endurance_tss: float = 0.0


@dataclass
class Plan:
    goal: Goal
    weeks: list
    sessions: list
    projection: dict

    def to_dict(self) -> dict:
        d = asdict(self)
        d["goal"].pop("availability_by_date", None)          # derived from the calendar, not part of the goal
        d["goal"]["exam_weeks"] = sorted(x.isoformat() for x in self.goal.exam_weeks)
        d["goal"]["easy_dates"] = sorted(x.isoformat() for x in self.goal.easy_dates)
        d["goal"]["away_days"] = sorted(self.goal.away_days)
        d["goal"]["race_date"] = self.goal.race_date.isoformat()
        d["goal"]["start_date"] = self.goal.start_date.isoformat()
        for w in d["weeks"]:
            w["start"] = w["start"].isoformat()
        for s in d["sessions"]:
            s["day"] = s["day"].isoformat()
        return d


# ------------------------------------------------------------------ macrocycle
def macrocycle(n_weeks: int, event: str, recovery_every: int = 4, cfg: Optional[dict] = None) -> list[tuple[str, bool]]:
    """Phase for each week, laid out backwards from race week."""
    cfg = cfg or EVENTS[event]
    if event in NO_RACE:
        return [("base" if i < n_weeks / 2 else "build", (i + 1) % recovery_every == 0) for i in range(n_weeks)]
    if n_weeks < 2:
        return [("race", False)] * n_weeks

    tail: list[str] = ["race"]
    taper = min(cfg["taper"], max(0, n_weeks - 2))
    tail = ["taper"] * taper + tail
    remaining = n_weeks - len(tail)
    peak = 2 if remaining >= 10 else (1 if remaining >= 5 else 0)
    tail = ["peak"] * peak + tail
    remaining -= peak
    base = round(remaining * 0.55) if remaining >= 6 else remaining // 2
    build = remaining - base
    head = ["base"] * base + ["build"] * build

    phases: list[tuple[str, bool]] = []
    for i, p in enumerate(head):
        is_rec = (i + 1) % recovery_every == 0 and i != len(head) - 1
        phases.append((p, is_rec))
    phases += [(p, False) for p in tail]
    return phases


def _daily_for(ctl_start: float, ctl_end: float) -> float:
    return max(0.0, (ctl_end - ctl_start * DECAY_WEEK) / (1 - DECAY_WEEK))


def load_targets(phases: list[tuple[str, bool]], ctl0: float, peak_ctl: float, ramp: float,
                 taper_weeks: int) -> list[float]:
    """Weekly TSS targets that walk CTL from ctl0 to peak_ctl safely.

    Deload weeks lose fitness, so the per-week ramp is *solved* (bisection) such
    that CTL at the end of the peak block lands on ``peak_ctl``, capped at the
    level's safe ramp rate.
    """
    if peak_ctl <= ctl0:
        return _targets_for_step(phases, ctl0, 0.0, taper_weeks)[0]
    lo, hi = 0.0, ramp
    if _targets_for_step(phases, ctl0, hi, taper_weeks)[1] < peak_ctl:
        return _targets_for_step(phases, ctl0, hi, taper_weeks)[0]
    for _ in range(40):
        mid = (lo + hi) / 2
        if _targets_for_step(phases, ctl0, mid, taper_weeks)[1] < peak_ctl:
            lo = mid
        else:
            hi = mid
    return _targets_for_step(phases, ctl0, hi, taper_weeks)[0]


def _targets_for_step(phases: list[tuple[str, bool]], ctl0: float, step: float,
                      taper_weeks: int) -> tuple[list[float], float]:
    """Simulate the season for a given CTL step; return (weekly TSS, peak CTL)."""
    targets: list[float] = []
    ctl = ctl0
    last_load = 7 * max(ctl0, 15)
    taper_i = 0
    peak_level = ctl0
    for phase, rec in phases:
        if phase in ("base", "build"):
            if rec:
                tss = last_load * 0.6
            else:
                tss = 7 * _daily_for(ctl, ctl + step)
                last_load = tss
        elif phase == "peak":
            peak_level = max(peak_level, ctl)
            tss = 7 * _daily_for(ctl, ctl + step * 0.5)
            last_load = tss
        elif phase == "taper":
            curve = TAPER_CURVE.get(taper_weeks, [0.6])
            tss = last_load * curve[min(taper_i, len(curve) - 1)]
            taper_i += 1
        else:  # race week (race itself added separately)
            tss = last_load * 0.30
        targets.append(tss)
        # advance CTL with the chosen weekly load
        daily = tss / 7
        ctl = daily + (ctl - daily) * DECAY_WEEK
        if phase in ("base", "build", "peak"):
            peak_level = max(peak_level, ctl)
    return targets, peak_level


# ------------------------------------------------------------------ weekly layout
def _week_sessions(phase: str, recovery: bool, event, minutes: float, gym_per_week: int,
                   th: Thresholds, week_index: int, gym_min: float = GYM_MIN,
                   long_ceiling: float = 1e9, stages: Optional[dict] = None,
                   mobility_n: int = 0, day_cap: float = 1e9) -> list[PlannedSession]:
    """Decide *what* to do this week (not yet *when*).

    ``stages``: sport -> ladder.Stage the athlete is projected to be at this week.
    The stage caps session length, long sessions and intensity, and swaps in
    stage-specific formats (walk-run, learn-to-swim…). Beginners get *less*
    than the load target asks for; that is intended.
    """
    stages = stages or {}
    cfg = event if isinstance(event, dict) else EVENTS[event]
    mix = dict(cfg["mix"])
    eff_phase = phase
    # second half of build gets more specific work
    if phase == "build" and week_index % 2 == 1:
        eff_phase = "build2"

    gyms = gym_per_week if "gym" in mix else 0
    strength_led = mix.get("gym", 0) >= 0.5
    if (phase in ("peak",) or recovery) and not strength_led:
        gyms = min(gyms, 1)      # endurance plans protect key sessions; strength plans deload instead (see generate)
    if phase in ("taper", "race"):
        gyms = min(gyms, 1) if phase == "taper" else 0
    mob_n = mobility_n if phase != "race" else min(mobility_n, 2)
    gym_minutes = gyms * gym_min + mob_n * MOBILITY_MIN
    endurance = {s: v for s, v in mix.items() if s != "gym"}
    tot = sum(endurance.values()) or 1
    endurance_minutes = max(0.0, minutes - gym_minutes)

    out: list[PlannedSession] = []
    placeholder = date.min
    for sport, share in endurance.items():
        sm = endurance_minutes * share / tot
        if sm < MIN_SESSION[sport]:
            continue
        quality_n = 0 if recovery else {
            "base": 1, "build": 1, "build2": 2 if sport == "run" and len(endurance) == 1 else 1,
            "peak": 2 if len(endurance) == 1 else 1, "taper": 1, "race": 1 if phase != "race" else 0,
        }.get(eff_phase, 1)
        if phase == "race":
            quality_n = 0
        st = stages.get(sport)
        if st and not st.quality:
            quality_n = 0
        if quality_n and sm < QUALITY_MIN[sport] + MIN_SESSION[sport]:
            quality_n = 0       # too little time for this sport: steady volume first
        if st and st.index < len(ladder.LADDERS[sport]) - 2:
            quality_n = min(quality_n, 1)     # one hard session a week until the top two stages
        has_long = phase not in ("race",) and not (sport == "swim" and phase in ("base", "taper"))
        if st and st.long_max == 0:
            has_long = False
        long_m = min(cfg["long_cap"].get(sport, 90), long_ceiling * LONG_SPORT_FACTOR[sport],
                     st.long_max if st else 1e9,
                     sm * cfg.get("long_share", {}).get(sport, 0.38 if sport != "swim" else 0.35)) if has_long else 0
        if recovery:
            long_m *= 0.75
        if long_m < MIN_SESSION[sport]:
            long_m = 0          # too short to be a "long" session: its time goes to regular sessions
        q_m = (QUALITY_MIN_TAPER if phase in ("taper", "race") else QUALITY_MIN)[sport] if quality_n else 0
        rest_m = sm - long_m - q_m * quality_n
        if rest_m < 0:  # not enough time: drop long first, then quality
            long_m = max(0, sm - q_m * quality_n)
            rest_m = sm - long_m - q_m * quality_n
            if long_m < MIN_SESSION[sport]:
                long_m = 0
                rest_m = sm - q_m * quality_n
        cap = MAX_SESSIONS[sport][0 if len(endurance) == 1 else 1]
        if st:
            cap = min(cap, ladder.max_sessions(sport, st.index))
        slots = max(0, cap - quality_n - (1 if long_m >= MIN_SESSION[sport] else 0))
        # sessions sized to the athlete's longest free day (20-min mornings → several short sessions)
        easy_n = min(slots, max(int(round(rest_m / EASY_TARGET[sport])), math.ceil(rest_m / max(day_cap, 1) - 0.05)),
                     int(rest_m // MIN_SESSION[sport]))
        if easy_n == 0 and rest_m >= MIN_SESSION[sport] and slots:
            easy_n = 1
        easy_m = rest_m / easy_n if easy_n else 0
        if st and easy_m > st.session_max:                 # stage caps session length; extra volume is dropped
            easy_m = st.session_max
        if easy_n == 0 and rest_m > 0 and long_m:
            long_m = min(cfg["long_cap"].get(sport, 90), long_m + rest_m, st.long_max if st else 1e9)

        def add(kind: str, mins: float, variant: int = 0, sport: str = sport, st=st) -> None:
            k_phase = "taper" if phase == "race" else eff_phase
            special = ladder.stage_session(sport, st, kind, mins) if st else None
            title, text = special or templates.build(sport, kind, k_phase, mins, th, variant)
            out.append(PlannedSession(placeholder, sport, kind, title, text, int(round(mins))))

        if long_m >= MIN_SESSION[sport]:
            add("long", long_m)
        for _ in range(quality_n):
            add("quality", q_m)
        for _ in range(easy_n):
            add("recovery" if recovery and sport == "run" else "easy", easy_m)

    gst = stages.get("gym")
    for g in range(gyms):
        special = ladder.stage_session("gym", gst, "strength", GYM_MIN, variant=g) if gst else None
        title, text = special or templates.build("gym", "strength",
                                                 "recovery" if recovery and not strength_led else phase, GYM_MIN,
                                                 th, variant=g)
        # size template sessions to their real content (a 37-min session must fit a 45-min lunch break)
        est = round(estimate(dsl.parse(text), th).duration_s / 60)
        out.append(PlannedSession(placeholder, "gym", "strength", title, text, max(20, est)))
    for k in range(mob_n):
        title, text = templates.mobility("mobility", eff_phase, MOBILITY_MIN, th, variant=k + week_index)
        out.append(PlannedSession(placeholder, "mobility", "mobility", title, text, MOBILITY_MIN))
    return out


PRIORITY = {"race": 0, "test": 1, "long": 1, "quality": 2, "strength": 3, "easy": 4, "recovery": 5, "mobility": 6}


def schedule_week(sessions: list[PlannedSession], week_start: date, availability: dict,
                  first_day: date, race_day: Optional[date], fixed: Optional[list[PlannedSession]] = None,
                  context: Optional[list[PlannedSession]] = None,
                  by_date: Optional[dict] = None, easy_dates: Optional[set] = None,
                  alt_gym=None, doubles: Optional[set] = None) -> tuple[list[PlannedSession], list[str]]:
    """Greedy constraint placement: most important sessions pick days first.

    ``context`` holds already-scheduled neighbours (e.g. last week's Sunday) that
    constrain placement but are not returned.
    """
    notes: list[str] = []
    placed: list[PlannedSession] = list(fixed or []) + list(context or [])
    days = [week_start + timedelta(days=i) for i in range(7)]
    days = [d for d in days if d >= first_day and (race_day is None or d <= race_day)]

    by_date = by_date or {}

    def avail(d: date) -> int:
        if d in by_date:
            return int(by_date[d][0])
        return int(availability.get(d.weekday(), availability.get(str(d.weekday()), 0)))

    def longest(d: date) -> Optional[int]:
        return int(by_date[d][1]) if d in by_date else None

    easy_dates = easy_dates or set()

    def free_on(d: date) -> int:
        return avail(d) - sum(x.minutes for x in group_by_day(placed).get(d, []) if x.sport != "other")

    def viol(s: PlannedSession, d: date, by_day: dict) -> list[str]:
        return violations(s, d, by_day, avail(d), race_day, longest(d), easy_only=d in easy_dates,
                          allow_double=s.sport in (doubles or ()))

    biggest = max([min(avail(d), longest(d) or avail(d)) for d in days] or [0])
    capped = []
    for s in sessions:
        if s.sport != "gym" and biggest and s.minutes > biggest * 1.1:
            new = _resize(s, int(biggest))
            new.notes = s.notes
            notes.append(f"Capped {s.title.lower()} at {int(biggest)} min, your longest free window")
            s = new
        capped.append(s)
    ordered = sorted(capped, key=lambda x: (PRIORITY.get(x.kind, 9), -x.minutes))
    # 0) level checks first, on the earliest day that works; that sport's other sessions come after
    checks = [s for s in ordered if s.kind == "test"]
    after: dict[str, date] = {}
    for c in checks:
        for d in days:
            if not viol(c, d, group_by_day(placed)):
                c.day = d
                placed.append(c)
                after[c.sport] = d
                break
        else:
            notes.append(f"No room for the {c.sport} level check this week")
    ordered = [s for s in ordered if s.kind != "test"]
    hard = [s for s in ordered if s.hard]
    soft = [s for s in ordered if not s.hard]
    # easy sessions: round-robin across sports, the hardest-to-place sport first (fewest sessions;
    # beginner run-walks need rest days around them), so one sport can't take every free slot
    easy = [x for x in soft if x.kind in ("easy", "recovery")]
    other = [x for x in soft if x.kind not in ("easy", "recovery")]
    queues: dict[str, list] = {}
    for x in easy:
        queues.setdefault(x.sport, []).append(x)
    order = sorted(queues, key=lambda sp: (not any("Walk @" in x.dsl for x in queues[sp]), len(queues[sp])))
    rr = []
    while any(queues.values()):
        for sp in order:
            if queues[sp]:
                rr.append(queues[sp].pop(0))
    soft = other + rr
    # 1) key sessions: backtracking search for the best-spaced legal layout
    best_layout = _place_hard(hard, days, placed, avail, viol, after=after)
    for s, d in best_layout.items():
        s.day = d
        placed.append(s)
    unplaced_hard = [s for s in hard if s not in best_layout]

    # 2) everything else (and any key session the search couldn't fit): greedy
    for s in unplaced_hard + soft:
        sdays = [d for d in days if d > after.get(s.sport, date.min)] or days
        by_day = group_by_day(placed)
        best, best_score = None, -1e9
        for d in sdays:
            if viol(s, d, by_day):
                continue
            score = 0.0
            free = avail(d) - sum(x.minutes for x in by_day.get(d, []) if x.sport != "other")
            if s.kind == "long":
                score += free / 10 + (8 if d.weekday() >= 5 else 0)   # longest free day, weekend preferred
            else:
                score -= abs(free - s.minutes) / 30        # snug fit
            score -= 15 * len(by_day.get(d, []))           # spread sessions
            if s.hard:                                     # spread hard days apart
                hard_days = [x.day for x in placed if x.hard]
                if hard_days:
                    score += min(abs((d - h).days) for h in hard_days) * 4
            same_sport = [x for x in by_day.get(d, []) if x.sport == s.sport]
            score -= 40 * len(same_sport)
            neighbours = by_day.get(d - timedelta(days=1), []) + by_day.get(d + timedelta(days=1), [])
            score -= 25 * sum(1 for x in neighbours if x.sport == s.sport)   # spread each sport across the week
            if s.kind == "test":                            # level checks first, so the rest of the week fits
                score -= (d - week_start).days * 25
            if score > best_score:
                best, best_score = d, score
        if best is None and s.sport == "gym":
            # strength that doesn't fit: try the upper-body version (legs stay fresh for whatever comes
            # next), then a shorter version of either in the biggest free slot
            options = [s] + ([a] if alt_gym and s.heavy_legs and (a := alt_gym(s)) else [])
            for cand in options:
                for d in sorted(sdays, key=lambda d: -free_on(d)):
                    room = free_on(d) if longest(d) is None else min(free_on(d), longest(d))
                    trial = cand
                    if cand.minutes > room * 1.1:
                        if room < 20:
                            continue
                        text = templates.fit_gym(cand.dsl, room - 2)
                        mins = round(estimate(dsl.parse(text), Thresholds()).duration_s / 60)
                        trial = PlannedSession(d, "gym", cand.kind, cand.title, text, mins)
                        trial.notes = cand.notes
                    if not viol(trial, d, group_by_day(placed)):
                        best, s = d, trial
                        if trial is not cand:
                            notes.append(f"Shortened {cand.title.lower()} to {trial.minutes} min to fit {d:%A}")
                        elif cand is not options[0]:
                            notes.append(f"Swapped a full-body session for {cand.title.lower()} to keep your legs fresh")
                        break
                if best is not None:
                    break
        if best is None:
            # relax: shrink to the largest free slot that satisfies the other rules
            for d in sorted(days, key=lambda d: -free_on(d)):
                free = free_on(d)
                if longest(d) is not None:
                    free = min(free, longest(d))
                if free >= MIN_SESSION[s.sport] and s.sport != "gym":
                    trial = PlannedSession(d, s.sport, s.kind, s.title, s.dsl, free)
                    if not viol(trial, d, group_by_day(placed)):
                        best = d
                        s = _resize(s, free)
                        notes.append(f"Shortened {s.title} to {free} min to fit your week")
                        break
        if best is None and s.kind in ("easy", "recovery") and s.sport != "gym":
            # merge into another easy session of the same sport rather than losing the volume
            ctx_ids = {id(c) for c in (context or [])} | {id(c) for c in (fixed or [])}
            for x in sorted((p for p in placed if p.sport == s.sport and p.kind in ("easy", "recovery", "long")
                             and "Walk @" not in p.dsl and p.day in days and id(p) not in ctx_ids),
                            key=lambda p: (p.kind == "long", p.minutes)):
                room = min(avail(x.day), longest(x.day) or avail(x.day)) - sum(
                    y.minutes for y in group_by_day(placed).get(x.day, []) if y.sport != "other")
                add = min(s.minutes, room - 5)          # 5-min margin: rebuilt workouts round up
                if s.sport == "run":                    # running volume is impact: grow a run by 30 % at most
                    add = min(add, x.minutes * 0.3)
                if add >= 10:
                    merged = _resize(x, x.minutes + int(add))
                    merged.day, merged.notes, merged.kind = x.day, x.notes, x.kind
                    placed[placed.index(x)] = merged
                    notes.append(f"Merged a {s.sport} session into {x.day:%A}'s ({x.minutes} → {merged.minutes} min)")
                    best = "merged"
                    break
            if best == "merged":
                continue
        if best is None and s.sport == "mobility":
            continue                # optional extra: no note, nothing lost
        if best is None:
            label = {"gym": "strength session", "easy": "easy", "long": "long", "quality": "key"}.get(s.kind, s.kind)
            notes.append(f"Skipped one {s.sport + ' ' if s.sport != 'gym' else ''}{label} session ({s.minutes} min): "
                         "no day had both the time and the recovery room for it")
            continue
        s.day = best
        placed.append(s)
    keep = [p for p in placed if not any(p is x for x in (fixed or []) + (context or []))]
    return keep, notes


def _place_hard(hard: list[PlannedSession], days: list[date], placed: list[PlannedSession], avail,
                viol, budget: int = 4000, after: Optional[dict] = None) -> dict:
    """DFS over day assignments for key sessions; maximises spacing and long-day fit.

    Returns {session: day} for the best layout found (may be partial if some
    session fits nowhere). The constraint check prunes the tree heavily, and a
    node budget bounds worst-case time.
    """
    best: dict = {}
    best_score = (-1, -1e9)
    nodes = 0
    current: list[PlannedSession] = []
    assign: dict = {}

    def score() -> float:
        sc = 0.0
        hard_days = sorted({x.day for x in placed + current if x.hard})
        for i in range(1, len(hard_days)):
            sc += min(3, (hard_days[i] - hard_days[i - 1]).days) * 4
        for x in current:
            if x.kind == "long":
                sc += avail(x.day) / 10 + (8 if x.day.weekday() >= 5 else 0)
        return sc

    def dfs(i: int) -> None:
        nonlocal best, best_score, nodes
        nodes += 1
        if nodes > budget:
            return
        if i == len(hard):
            key = (len(assign), score())
            if key > best_score:
                best_score, best = key, dict(assign)
            return
        s = hard[i]
        by_day = group_by_day(placed + current)
        options = [d for d in days if d > (after or {}).get(s.sport, date.min)
                   and not viol(s, d, by_day)]
        for d in options:
            s.day = d
            current.append(s)
            assign[s] = d
            dfs(i + 1)
            current.pop()
            del assign[s]
        # also explore leaving this one out (it will fall back to greedy/shrink)
        if not options or len(assign) + (len(hard) - i - 1) >= best_score[0]:
            dfs(i + 1)

    dfs(0)
    return best


def _routine_order(week: list[PlannedSession], commits: list[PlannedSession], availability: dict, goal: Goal) -> None:
    """Your own routines run in the order you wrote them (Squat day → Bench day → Deadlift day…).

    The scheduler picks the *days*; this puts the routines back in sequence on those days,
    as long as the result still breaks no recovery rule.
    """
    gyms = sorted((x for x in week if x.sport == "gym"), key=lambda x: x.day)
    if len(gyms) < 2:
        return
    order = {r["name"]: i for i, r in enumerate(goal.gym_routines)}
    content = sorted(((x.title, x.dsl, x.minutes, x.fixed_minutes) for x in gyms),
                     key=lambda c: order.get(c[0].removeprefix("Deload — "), 99))
    before = [(x.title, x.dsl, x.minutes, x.fixed_minutes) for x in gyms]
    for x, c in zip(gyms, content, strict=True):
        x.title, x.dsl, x.minutes, x.fixed_minutes = c
    by_day = group_by_day(week + commits)
    for x in gyms:
        cap = goal.availability_by_date.get(x.day, (availability.get(x.day.weekday(), 0), None))[0]
        if violations(x, x.day, by_day, cap, None, None, easy_only=x.day in goal.easy_dates):
            for y, c in zip(gyms, before, strict=True):           # keep the scheduler's layout
                y.title, y.dsl, y.minutes, y.fixed_minutes = c
            return


def _resize(s: PlannedSession, minutes: int) -> PlannedSession:
    """Regenerate a session's DSL for a new duration, keeping its intent."""
    if s.sport == "gym":
        return s
    phase = s.notes[0] if s.notes else "base"
    variant = templates.mobility_variant(s.title) if s.sport == "mobility" else 0
    title, text = templates.build(s.sport, s.kind, phase, minutes, Thresholds(), variant)
    return PlannedSession(s.day, s.sport, s.kind, s.title, text, minutes, id=s.id, status=s.status)


def _score(sessions: list[PlannedSession], th: Thresholds) -> None:
    for s in sessions:
        est = estimate(dsl.parse(s.dsl), th)
        est_min = max(1, round(est.duration_s / 60))
        if s.fixed_minutes:
            # the athlete's real session length; stress scales with time at the same intensity
            s.tss = round(est.tss * s.fixed_minutes / est_min, 1)
            s.minutes = s.fixed_minutes
        else:
            s.tss = est.tss
            s.minutes = est_min
        s.distance_m = est.distance_m


# ------------------------------------------------------------------ public API
def generate(goal: Goal, th: Optional[Thresholds] = None) -> Plan:
    th = th or Thresholds()
    if goal.event not in EVENTS and goal.event != "custom":
        raise ValueError(f"Unknown event {goal.event}. Options: {', '.join(EVENTS)}")
    if goal.race_date <= goal.start_date:
        raise ValueError("Race date must be after the start date")
    cfg = event_config(goal.event, goal.sports or None, goal.body_focus)
    availability = {int(k): int(v) for k, v in goal.availability.items()}
    week0 = goal.start_date - timedelta(days=goal.start_date.weekday())
    n_weeks = (goal.race_date - week0).days // 7 + 1
    phases = macrocycle(n_weeks, goal.event, goal.recovery_every, cfg)

    ctl0 = goal.baseline_ctl if goal.baseline_ctl is not None else goal.baseline_hours * 50 / 7
    level_i = LEVELS.get(goal.level, 1)
    peak_ctl = max(cfg["ctl"][level_i], ctl0 + 5)
    targets = load_targets(phases, ctl0, peak_ctl, RAMP.get(goal.level, 5.0), cfg["taper"])
    def week_budget(ws: date) -> int:
        """Free minutes this week: real calendar where known, weekday defaults elsewhere."""
        return sum(goal.availability_by_date.get(ws + timedelta(days=k), (availability.get(k, 0), 0))[0]
                   for k in range(7))

    weeks: list[WeekPlan] = []
    sessions: list[PlannedSession] = []
    sports = list(cfg["mix"])
    start_levels = {}
    for sp in [x for x in sports if x in ladder.LADDERS]:
        lv = goal.levels.get(sp) or {}
        start_levels[sp] = (int(lv.get("stage", ladder.DEFAULT_STAGE.get(goal.level, ladder.DEFAULT_STAGE["beginner"])[sp])),
                            float(lv.get("progress", 0.0)), bool(lv.get("assessed")))
    pending_checks = [sp for sp in start_levels if sp in ladder.LEVEL_CHECK and not start_levels[sp][2]]
    routine_i = 0
    strength_led = cfg["mix"].get("gym", 0) >= 0.5
    cardio = [s for s in cfg["mix"] if s not in COUNTED and s != "walk"]
    mobility_n = 0
    if cfg.get("mobility"):
        # design choice: mobility-led plans (nothing harder than walking) get 3 routines a week, others 2
        mobility_n = goal.mobility_per_week if goal.mobility_per_week is not None else (
            2 if cardio or "gym" in cfg["mix"] else 3)
    commits_by_day: dict[int, list] = {}
    for c in goal.commitments:
        commits_by_day.setdefault(int(c["day"]), []).append(c)
    all_commits: list[PlannedSession] = []

    def commitments_for(ws: date) -> list[PlannedSession]:
        out = []
        for k in range(7):
            d = ws + timedelta(days=k)
            if d < goal.start_date or (d > goal.race_date and goal.event not in NO_RACE):
                continue
            for c in commits_by_day.get(k, []):
                hard = bool(c.get("hard", True))
                m = int(c.get("minutes") or 60)
                # design choice for the load curve only: hard practice ~70 TSS/h, easy ~40 TSS/h
                out.append(PlannedSession(d, "other", "other", c.get("name") or "Your training", "", m,
                                          tss=round(m / 60 * (70 if hard else 40)), commit_hard=hard))
        return out

    def alt_gym(s: PlannedSession) -> Optional[PlannedSession]:
        """Upper-body version of a full-body template session (not for the athlete's own routines)."""
        if goal.gym_routines:
            return None
        st = week_stages.get("gym")
        phase_ = s.notes[0] if s.notes else "base"
        special = ladder.stage_session("gym", st, "strength", GYM_MIN, variant=1) if st else None
        title, text = special or templates.build("gym", "strength", phase_ if phase_ in ("base", "build") else "base",
                                                 GYM_MIN, th, variant=1)
        if text == s.dsl:
            return None
        alt = PlannedSession(s.day, "gym", s.kind, title, text,
                             max(20, round(estimate(dsl.parse(text), th).duration_s / 60)))
        alt.notes = s.notes
        return alt
    routine_min = (sum(int(r.get("minutes") or GYM_MIN) for r in goal.gym_routines) / len(goal.gym_routines)
                   if goal.gym_routines else GYM_MIN)
    for i, ((phase, rec), target) in enumerate(zip(phases, targets, strict=True)):
        ws = week0 + timedelta(weeks=i)
        notes: list[str] = []
        exam = ws in goal.exam_weeks and phase not in ("race", "taper")
        if exam:
            target *= EXAM_FACTOR
            rec = True
        fixed: list[PlannedSession] = []
        fixed_tss = 0.0
        if phase == "race" and goal.event not in NO_RACE:
            for text in cfg["race"]:
                w = dsl.parse(text)
                fixed.append(PlannedSession(goal.race_date, w.sport, "race", f"🏁 {cfg['name']} — {w.sport}", text, 0))
            _score(fixed, th)
            fixed_tss = sum(s.tss for s in fixed)
            target += fixed_tss

        max_week_min = week_budget(ws)
        minutes = (target - fixed_tss) / 55 * 60 + goal.gym_per_week * routine_min                    # first guess: ~55 TSS/hour
        week_sessions: list[PlannedSession] = []
        for _ in range(4):                             # fixed-point calibration on TSS
            if minutes > max_week_min:
                minutes = max_week_min
            week_stages = {sp: ladder.stage(sp, ladder.project(start_levels[sp][0], start_levels[sp][1], sp, i))
                           for sp in start_levels}
            draft = _week_sessions(phase, rec, cfg, minutes, goal.gym_per_week, th, i, routine_min,
                                   LONG_START.get(goal.level, 75) + LONG_STEP * i, week_stages, mobility_n,
                                   day_cap=max([goal.availability_by_date.get(ws + timedelta(days=k),
                                                (availability.get(k, 0), 0))[0] for k in range(7)] or [1e9]))
            if i == 0 and pending_checks:
                # replace one easy session per unassessed sport with its level check
                for sp in pending_checks:
                    title, text, _ = ladder.LEVEL_CHECK[sp]
                    easy = next((x for x in draft if x.sport == sp and x.kind in ("easy", "recovery")), None)
                    if easy:
                        draft.remove(easy)
                    draft.append(PlannedSession(date.min, sp, "test", title, text, 30))
            ri = routine_i
            for d in draft:
                d.notes = [phase]
                if d.sport == "gym" and goal.gym_routines:
                    r = goal.gym_routines[ri % len(goal.gym_routines)]
                    ri += 1
                    d.title, d.dsl = r["name"], r["dsl"]
                    d.fixed_minutes = int(r.get("minutes") or 0) or None
                    d.minutes = d.fixed_minutes or d.minutes
                if d.sport == "gym" and rec and strength_led:
                    # strength-led plans keep their days in a deload week, with ~60 % of the sets
                    d.dsl = templates.deload(d.dsl)
                    d.title = f"Deload — {d.title}"
                    d.fixed_minutes = None
                    d.minutes = max(20, round(estimate(dsl.parse(d.dsl), th).duration_s / 60))
            commits = commitments_for(ws)
            week_sessions, notes = schedule_week(draft, ws, availability, goal.start_date,
                                                 goal.race_date if goal.event not in NO_RACE else None,
                                                 fixed + commits,
                                                 context=[x for x in sessions + all_commits
                                                          if x.day >= ws - timedelta(days=1)],
                                                 by_date=goal.availability_by_date, easy_dates=goal.easy_dates,
                                                 alt_gym=alt_gym,
                                                 doubles={sp for sp, st in week_stages.items() if sp != "gym"
                                                          and st.index >= len(ladder.LADDERS[sp]) - 2})
            week_sessions = [x for x in week_sessions if x.sport != "other"]
            if goal.gym_routines:
                _routine_order(week_sessions, commits, availability, goal)
            for x in week_sessions:                    # the equipment you'll have that day
                if x.sport == "gym":
                    eq = "none" if x.day.weekday() in goal.away_days else goal.equipment
                    new = templates.for_equipment(x.dsl, eq)
                    if new != x.dsl:
                        x.dsl = new
                        x.title = f"{x.title} ({templates.EQUIP_LABEL[eq]})"
            _score(week_sessions, th)
            # template rounding (a "55-min" VO2max set is really 59) must not overflow the day or a window
            for k, x in enumerate(week_sessions):
                lim = goal.availability_by_date.get(x.day, (0, None))[1]
                if lim and x.sport not in COUNTED and x.minutes > lim:
                    phase_note = x.notes
                    week_sessions[k] = _resize(x, max(MIN_SESSION[x.sport], int(lim) - 5))
                    week_sessions[k].day, week_sessions[k].notes = x.day, phase_note
                    _score([week_sessions[k]], th)
            for d, ss in group_by_day(week_sessions).items():
                cap = goal.availability_by_date[d][0] if d in goal.availability_by_date else availability.get(d.weekday(), 0)
                extra = sum(x.minutes for x in ss) - cap * 1.1
                flex = [x for x in ss if x.sport not in COUNTED and x.kind not in ("race", "test")]
                if extra > 0 and flex:
                    x = max(flex, key=lambda y: y.minutes)
                    k = week_sessions.index(x)
                    for trim in (5, 10, 15):     # templates round to 5 min; aim a little lower each try
                        new = _resize(x, max(MIN_SESSION[x.sport], int(x.minutes - extra - trim)))
                        new.day, new.notes = x.day, x.notes
                        _score([new], th)
                        if sum(y.minutes for y in ss if y is not x) + new.minutes <= cap * 1.1:
                            break
                    week_sessions[k] = new
            # load targets drive *endurance* volume; strength sessions are scheduled on top
            planned = sum(s.tss for s in week_sessions if s.sport not in COUNTED)
            goal_tss = target - fixed_tss
            if planned <= 0 or abs(planned - goal_tss) / max(goal_tss, 1) < 0.08:
                break
            new_minutes = minutes * (goal_tss / planned) ** 0.9
            if new_minutes >= max_week_min and minutes >= max_week_min:
                notes.append("Capped by your weekly availability — add time to reach the target load")
                break
            minutes = new_minutes
        routine_i = ri if goal.gym_routines else 0
        all_commits.extend(commits)
        if exam:
            notes.insert(0, "Exam period: training cut to 60 % and kept easy so you can study")
        all_week = week_sessions + fixed
        for s in all_week:
            s.notes = []
        planned_tss = sum(s.tss for s in all_week)
        sport_minutes: dict[str, int] = {}
        for s in all_week:
            sport_minutes[s.sport] = sport_minutes.get(s.sport, 0) + s.minutes
        weeks.append(WeekPlan(i, ws, phase, rec, round(target), round(planned_tss),
                              sum(s.minutes for s in all_week), sport_minutes, notes,
                              round(sum(s.tss for s in all_week if s.sport not in COUNTED))))
        sessions.extend(sorted(all_week, key=lambda s: (s.day, PRIORITY.get(s.kind, 9))))

    # projected form on race day
    daily: dict[date, float] = {}
    for s in sessions + all_commits:
        daily[s.day] = daily.get(s.day, 0) + s.tss
    curve = banister(daily, week0 - timedelta(days=1), goal.race_date, ctl0, ctl0)
    race_pt = curve[-1]
    target = threshold_for_time(goal.event, goal.target_time_s) if goal.target_time_s else None
    if target:
        pace = f"{target['race_pace'] // 60}:{target['race_pace'] % 60:02d}/km"
        for x in sessions:
            if x.sport != "run":
                continue
            if x.kind == "race":
                x.dsl = re.sub(r"@ Z\d", f"@ {pace}", x.dsl)
            elif "race-pace" in x.title.lower() or "Race-pace" in x.title:
                x.dsl = re.sub(r"(\d+min) @ Z[34]", rf"\1 @ {pace}", x.dsl)
                x.title = x.title.replace("race-pace", f"goal-pace ({pace})").replace("Race-pace", f"Goal-pace ({pace})")
        _score([x for x in sessions if x.sport == "run" and x.kind != "race"], th)
        gain = th.run_threshold_pace / target["threshold_pace"] - 1          # speed improvement needed
        weeks_avail = max(1, len(weeks) - 1)
        # design choice: ~0.5 % threshold-speed gain per week is a realistic ceiling for recreational runners
        run_lv = goal.levels.get("run") or {}
        target.update(measured=bool(run_lv.get("assessed")),     # judged against real fitness only after a check
                      current_threshold_pace=round(th.run_threshold_pace), improvement_pct=round(gain * 100, 1),
                      realistic=gain <= 0.005 * weeks_avail,
                      weeks_needed=max(0, round(gain / 0.005)))
    req = ladder.EVENT_REQUIRES.get(goal.event, {})
    level_proj = {}
    for sp in start_levels:
        s0, p0, assessed = start_levels[sp]
        at_race = ladder.project(s0, p0, sp, len(weeks) - 1)
        need = req.get(sp)
        level_proj[sp] = {"now": s0, "now_name": ladder.stage(sp, s0).name, "assessed": assessed,
                          "at_goal": at_race, "at_goal_name": ladder.stage(sp, at_race).name,
                          "required": need, "required_name": ladder.stage(sp, need).name if need is not None else None,
                          "on_track": need is None or at_race >= need,
                          "weeks_to_required": ladder.weeks_to_reach(s0, p0, sp, need) if need is not None else None}
    projection = {
        "levels": level_proj,
        "target": target,
        "event_name": cfg["name"],
        "start_ctl": round(ctl0, 1),
        "peak_ctl_target": round(peak_ctl, 1),
        "race_day_ctl": race_pt.ctl,
        "race_day_tsb": race_pt.tsb,
        "max_ramp": round(max((curve[j].ctl - curve[j - 7].ctl) for j in range(7, len(curve))) if len(curve) > 7 else 0, 1),
    }
    return Plan(goal, weeks, sessions, projection)
