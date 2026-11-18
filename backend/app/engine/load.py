"""
Unified multi-sport load model.

Every session — a pool swim, a ride, a run, a barbell session — is converted to
a single number, the Training Stress Score (TSS), so that the four disciplines
can share one fitness/fatigue model.

    TSS = hours × IF² × 100            (run, bike, gym)
    sTSS = hours × IF³ × 100           (swim — water resistance scales ~cubically)

where IF (intensity factor) is effort relative to the athlete's threshold:

    bike : IF = power / FTP
    run  : IF = threshold_pace / pace          (speed ratio)
    swim : IF = CSS_pace / pace                (critical swim speed)
    zone : Z1..Z5 → 0.55, 0.70, 0.83, 0.94, 1.05
    RPE  : 0.40 + 0.06 × RPE                   (session-RPE fallback)

The Banister impulse–response model then turns the daily TSS series into

    CTL (fitness)  = EWMA of TSS, time constant 42 days
    ATL (fatigue)  = EWMA of TSS, time constant 7 days
    TSB (form)     = CTL(yesterday) − ATL(yesterday)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from .dsl import Exercise, Step, Superset, Workout, flatten

ZONE_IF = {1: 0.55, 2: 0.70, 3: 0.83, 4: 0.94, 5: 1.05}
ROLE_IF = {"warmup": 0.60, "cooldown": 0.55, "rest": 0.40, "easy": 0.68,
           "long": 0.72, "work": 0.85, "race": 0.95}

CTL_DAYS = 42
ATL_DAYS = 7


@dataclass
class Thresholds:
    """Athlete physiology used to normalise intensity."""
    run_threshold_pace: float = 300.0   # sec / km   (≈ 1-hour race pace)
    swim_css: float = 120.0             # sec / 100m (critical swim speed)
    bike_ftp: float = 200.0             # watts
    bike_threshold_kmh: float = 32.0    # speed at FTP on flat road

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Thresholds":
        d = d or {}
        return cls(**{k: float(v) for k, v in d.items() if k in cls.__dataclass_fields__ and v})


@dataclass
class SessionEstimate:
    duration_s: float
    distance_m: float
    tss: float
    intensity_factor: float
    breakdown: list = field(default_factory=list)


def _rpe_if(rpe: float) -> float:
    return max(0.4, min(1.1, 0.40 + 0.06 * rpe))


def step_if(step: Step, sport: str, th: Thresholds) -> float:
    t = step.target
    if t is None:
        return ROLE_IF.get(step.role, 0.75)
    if t.kind == "zone":
        return ZONE_IF[int(t.value)]
    if t.kind == "rpe":
        return _rpe_if(t.value)
    if t.kind == "power":
        return t.value / th.bike_ftp
    if t.kind == "ftp_pct":
        return t.value / 100.0
    if t.kind == "pace":
        if t.unit == "100m":
            return th.swim_css / t.value
        sec_per_km = t.value if t.unit == "km" else t.value / 1.60934
        return th.run_threshold_pace / sec_per_km
    return 0.75


def _duration_for_distance(dist_m: float, intensity: float, sport: str, step: Step,
                           th: Thresholds) -> float:
    """Estimate time to cover a distance at a given intensity."""
    if step.target is not None and step.target.kind == "pace":
        per = step.target.value
        unit_m = 100 if step.target.unit == "100m" else (1000 if step.target.unit == "km" else 1609.34)
        return dist_m / unit_m * per
    intensity = max(intensity, 0.45)
    if sport == "walk":
        return dist_m / 1000 / walk_kmh(intensity) * 3600
    if sport == "swim":
        return dist_m / 100 * th.swim_css / intensity
    if sport == "bike":
        kmh = th.bike_threshold_kmh * (intensity ** 0.5)  # aero drag → speed grows sub-linearly
        return dist_m / 1000 / kmh * 3600
    # run: speed roughly linear in intensity
    return dist_m / 1000 * th.run_threshold_pace / intensity


def walk_kmh(intensity: float) -> float:
    """Walking speed by effort: easy ~4.5 km/h, brisk ~6 km/h (incline walks are slower but harder)."""
    return max(3.5, min(6.5, 4.5 + (intensity - 0.55) * 10))


def _distance_for_duration(dur_s: float, intensity: float, sport: str, th: Thresholds) -> float:
    intensity = max(intensity, 0.45)
    if sport == "walk":
        return dur_s / 3600 * walk_kmh(intensity) * 1000
    if sport == "swim":
        return dur_s / (th.swim_css / intensity) * 100
    if sport == "bike":
        return dur_s / 3600 * th.bike_threshold_kmh * (intensity ** 0.5) * 1000
    return dur_s / (th.run_threshold_pace / intensity) * 1000


def estimate(workout: Workout, th: Optional[Thresholds] = None) -> SessionEstimate:
    """Duration, distance and TSS for a parsed workout."""
    th = th or Thresholds()
    if workout.sport == "gym":
        return _estimate_gym(workout)
    if workout.sport == "mobility":
        return _estimate_gym(workout, rest_s=20, warmup_s=0, fixed_if=0.4)

    exponent = 3 if workout.sport == "swim" else 2
    total_s = total_m = stress = 0.0
    weighted_if = 0.0
    rows = []
    for step in flatten(workout.steps):
        i = step_if(step, workout.sport, th)
        if step.duration_s:
            dur = step.duration_s
            dist = step.distance_m or (0 if step.role == "rest" else
                                       _distance_for_duration(dur, i, workout.sport, th))
        else:
            dist = step.distance_m or 0
            dur = _duration_for_distance(dist, i, workout.sport, step, th)
        s = dur / 3600 * (i ** exponent) * 100
        total_s += dur
        total_m += dist
        stress += s
        weighted_if += i * dur
        rows.append({"label": step.label or step.role, "duration_s": round(dur), "if": round(i, 2), "tss": round(s, 1)})
    avg_if = weighted_if / total_s if total_s else 0
    return SessionEstimate(round(total_s), round(total_m), round(stress, 1), round(avg_if, 2), rows)


def _estimate_gym(workout: Workout, rest_s: int = 120, warmup_s: int = 600,
                  fixed_if: Optional[float] = None) -> SessionEstimate:
    """Strength work: time under tension + rest, intensity from RPE.

    Mobility uses the same structure with short transitions and a fixed low
    intensity (it is the warm-up, and it is not meant to tire you).
    """
    total_s = stress = 0.0
    rows = []
    exercises: list[tuple[Exercise, bool]] = []
    for n in workout.steps:
        if isinstance(n, Exercise):
            exercises.append((n, False))
        elif isinstance(n, Superset):
            exercises.extend((e, True) for e in n.exercises)
    for ex, in_superset in exercises:
        rpe = ex.rpe or 7.0
        work = ex.sets * (ex.hold_s or ex.reps * 4)          # ~4 s per rep, or the hold time
        rest = ex.sets * (rest_s // 2 if in_superset else rest_s)   # supersets share rest
        dur = work + rest
        i = fixed_if or 0.45 + 0.04 * rpe                    # RPE 8 → IF 0.77
        s = dur / 3600 * i * i * 100
        total_s += dur
        stress += s
        rows.append({"label": ex.name, "duration_s": round(dur), "if": round(i, 2), "tss": round(s, 1)})
    warmup_s = min(warmup_s, 0.3 * total_s)   # a 15-min session gets a ~4-min warm-up, not 10
    total_s += warmup_s  # general warm-up
    stress += warmup_s / 3600 * 0.55 ** 2 * 100
    avg_if = fixed_if or 0.45 + 0.04 * 7
    return SessionEstimate(round(total_s), 0, round(stress, 1), round(avg_if, 2), rows)


def tss_from_actual(sport: str, duration_s: float, rpe: Optional[float] = None,
                    avg_power: Optional[float] = None, avg_hr: Optional[float] = None,
                    distance_m: Optional[float] = None, th: Optional[Thresholds] = None,
                    hr_threshold: float = 170.0) -> float:
    """TSS for a completed activity using the best signal available."""
    th = th or Thresholds()
    i: Optional[float] = None
    if sport == "bike" and avg_power:
        i = avg_power / th.bike_ftp
    elif avg_hr:
        i = avg_hr / hr_threshold                     # hrTSS approximation
    elif distance_m and duration_s and sport in ("run", "swim"):
        if sport == "run":
            i = th.run_threshold_pace / (duration_s / (distance_m / 1000))
        else:
            i = th.swim_css / (duration_s / (distance_m / 100))
    elif rpe:
        i = _rpe_if(rpe) if sport != "gym" else 0.45 + 0.04 * rpe
    i = min(max(i or 0.7, 0.3), 1.2)
    exp = 3 if sport == "swim" else 2
    return round(duration_s / 3600 * i ** exp * 100, 1)


# --------------------------------------------------------------------------- #
# Banister fitness–fatigue model
# --------------------------------------------------------------------------- #
@dataclass
class LoadPoint:
    day: date
    tss: float
    ctl: float
    atl: float
    tsb: float
    projected: bool = False


def banister(daily_tss: dict[date, float], start: date, end: date,
             ctl0: float = 0.0, atl0: float = 0.0,
             projected_from: Optional[date] = None) -> list[LoadPoint]:
    """Run the impulse–response model day by day."""
    kc = 1 - math.exp(-1 / CTL_DAYS)
    ka = 1 - math.exp(-1 / ATL_DAYS)
    ctl, atl = ctl0, atl0
    out = []
    d = start
    while d <= end:
        tsb = ctl - atl                      # form = yesterday's fitness − fatigue
        t = daily_tss.get(d, 0.0)
        ctl += (t - ctl) * kc
        atl += (t - atl) * ka
        out.append(LoadPoint(d, round(t, 1), round(ctl, 1), round(atl, 1), round(tsb, 1),
                             projected=bool(projected_from and d >= projected_from)))
        d += timedelta(days=1)
    return out


def readiness(tsb: float, ctl: float, ramp_7d: float) -> dict:
    """Classify form into an actionable status."""
    if ramp_7d > 8:
        status, msg = "risk", "Fitness is ramping faster than ~8 CTL/week — injury risk. Back off."
    elif tsb < -30:
        status, msg = "risk", "Deep fatigue. Prioritise sleep and an easy day."
    elif tsb < -10:
        status, msg = "productive", "Productive overload — you're building fitness."
    elif tsb <= 5:
        status, msg = "neutral", "Balanced. Good day for quality work."
    elif tsb <= 25:
        status, msg = "fresh", "Fresh and race-ready."
    else:
        status, msg = "detraining", "Very fresh — fitness may be slipping if this lasts."
    score = int(max(0, min(100, 60 + tsb * 1.5 - max(0, ramp_7d - 5) * 4)))
    return {"status": status, "message": msg, "score": score}


def daily_totals(items: Iterable[tuple[date, float]]) -> dict[date, float]:
    out: dict[date, float] = {}
    for d, t in items:
        out[d] = out.get(d, 0.0) + (t or 0.0)
    return out
