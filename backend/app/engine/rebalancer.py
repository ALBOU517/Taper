"""
Missed-workout rebalancer ("life happens" solver).

When a session is missed the rest of the week is re-solved instead of showing a
red badge. Strategy, in order:

1. **Key session** (long / quality) → try to *move* it to a later day this week
   where every recovery constraint still holds, swapping out an easy session
   on that day if needed (the easy one is dropped — key work wins).
2. **Volume** that cannot be moved is *redistributed* onto remaining easy
   sessions of the same sport, each extended by at most +25 % and never past
   the day's availability.
3. We deliberately recover at most ~70 % of the missed TSS. Chasing lost volume
   is a classic overtraining trap; the remaining weeks absorb the rest.

Every change is returned with a plain-language reason so the UI can show a diff.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import dsl, templates
from .constraints import PlannedSession, group_by_day, violations
from .load import Thresholds, estimate

MAX_EXTEND = 0.25
RECOVER_FRACTION = 0.70


@dataclass
class Change:
    action: str                 # moved | extended | dropped | skipped
    session_id: Optional[int]
    detail: str
    new_day: Optional[date] = None
    new_minutes: Optional[int] = None
    new_dsl: Optional[str] = None
    new_tss: Optional[float] = None


def _phase_of(s: PlannedSession) -> str:
    t = s.title.lower()
    if "race-pace" in t or "sharpener" in t or "openers" in t:
        return "taper"
    if "vo2" in t or "race-pace blocks" in t:
        return "peak"
    if "threshold" in t or "fast finish" in t or "tempo" in t:
        return "build"
    return "base"


def rebalance(missed: PlannedSession, week: list[PlannedSession], today: date,
              availability: dict, race_day: Optional[date] = None,
              th: Optional[Thresholds] = None) -> list[Change]:
    th = th or Thresholds()
    availability = {int(k): int(v) for k, v in availability.items()}
    missed.status = "missed"
    changes: list[Change] = []
    future = [s for s in week if s.day > today and s.status == "planned" and s is not missed]
    candidate_days = sorted({d for d in _week_days(missed.day) if d > today})

    # 1) move key sessions --------------------------------------------------
    if missed.hard and missed.kind != "race":
        for d in candidate_days:
            by_day = group_by_day([s for s in week if s.status != "missed"])
            trial = PlannedSession(d, missed.sport, missed.kind, missed.title, missed.dsl, missed.minutes, missed.tss)
            v = violations(trial, d, by_day, availability.get(d.weekday(), 0), race_day)
            if not v:
                changes.append(Change("moved", missed.id, f"Moved {missed.title} to {d:%A} — all recovery rules hold",
                                      new_day=d))
                return changes
            # try swapping out an easy session on that day
            easies = [s for s in by_day.get(d, []) if s.kind in ("easy", "recovery")]
            for e in easies:
                e.status = "missed"  # pretend removed
                by_day2 = group_by_day([s for s in week if s.status != "missed"])
                v2 = violations(trial, d, by_day2, availability.get(d.weekday(), 0), race_day)
                e.status = "planned"
                if not v2:
                    changes.append(Change("dropped", e.id, f"Dropped {e.title} on {d:%A} to make room"))
                    changes.append(Change("moved", missed.id,
                                          f"Moved {missed.title} to {d:%A} (replaces the easy {e.sport})", new_day=d))
                    return changes
        changes.append(Change("skipped", missed.id,
                              f"{missed.title} can't be moved without breaking recovery rules — "
                              "redistributing part of its volume instead"))

    # 2) redistribute volume ------------------------------------------------
    budget = missed.tss * RECOVER_FRACTION
    pool = [s for s in future if s.sport == missed.sport and s.kind in ("easy", "recovery", "long")]
    if not pool:
        pool = [s for s in future if s.kind in ("easy", "recovery") and s.sport != "gym"]
    if not pool:
        changes.append(Change("skipped", missed.id, "No remaining easy sessions this week — "
                                                    "the missed load is absorbed by the following weeks"))
        return changes

    by_day = group_by_day([s for s in week if s.status != "missed"])
    per = budget / len(pool)
    for s in sorted(pool, key=lambda x: x.day):
        if budget <= 0.5:
            break
        used = sum(x.minutes for x in by_day.get(s.day, []))
        free = availability.get(s.day.weekday(), 0) * 1.1 - used
        tss_per_min = s.tss / max(s.minutes, 1)
        want = min(per, budget) / max(tss_per_min, 0.1)
        extra = min(want, s.minutes * MAX_EXTEND, free)
        extra = int(extra // 5 * 5)
        if extra < 5:
            continue
        new_min = s.minutes + extra
        _, text = templates.build(s.sport, s.kind, _phase_of(s), new_min, th)
        est = estimate(dsl.parse(text), th)
        gained = est.tss - s.tss
        budget -= gained
        changes.append(Change("extended", s.id,
                              f"Extended {s.title} on {s.day:%A} by {extra} min (+{gained:.0f} TSS)",
                              new_minutes=round(est.duration_s / 60), new_dsl=text, new_tss=est.tss))
    recovered = missed.tss * RECOVER_FRACTION - max(budget, 0)
    changes.append(Change("skipped", None,
                          f"Recovered {recovered:.0f} of {missed.tss:.0f} TSS this week; "
                          "the rest is intentionally not chased."))
    return changes


def _week_days(d: date) -> list[date]:
    from datetime import timedelta
    start = d - timedelta(days=d.weekday())
    return [start + timedelta(days=i) for i in range(7)]
