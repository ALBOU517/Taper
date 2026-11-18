"""
Recovery constraints shared by the weekly scheduler and the missed-workout
rebalancer. Keeping them in one place guarantees the "life happens" solver can
never produce a week the planner itself would have rejected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

HARD_KINDS = {"quality", "long", "race"}
ENDURANCE = {"run", "bike", "swim"}
MAX_PER_DAY = 2
_HEAVY = re.compile(r"(Squat|Deadlift|RDL|Step-ups|Lunge|Leg Press|Hack Squat)\s+([3-9])x\d+(?!\s*@\s*RPE\s*[1-5]\b)", re.I)
OVERFLOW = 1.10  # allow 10% over a day's stated availability


@dataclass(eq=False)  # identity semantics: sessions are hashed by object
class PlannedSession:
    day: date
    sport: str
    kind: str                 # easy | recovery | quality | long | strength | race | test | mobility | other
    title: str
    dsl: str
    minutes: int
    tss: float = 0.0
    distance_m: float = 0.0
    id: Optional[int] = None
    status: str = "planned"
    notes: list = field(default_factory=list)
    fixed_minutes: Optional[int] = None      # user routine with a known real duration
    commit_hard: bool = False                # outside training (sport "other") that is a hard day

    @property
    def hard(self) -> bool:
        if self.sport == "other":
            return self.commit_hard
        # walking is never a hard day; an aerobic endurance swim is low-impact: not a hard day either
        return self.kind in HARD_KINDS and self.sport != "walk" and not (self.sport == "swim" and self.kind == "long")

    @property
    def heavy_legs(self) -> bool:
        """Lower-body lifting with real volume (>= 3 working sets) — maintenance doses don't count."""
        if self.sport != "gym":
            return False
        return bool(_HEAVY.search(self.dsl))


def violations(cand: PlannedSession, day: date, by_day: dict[date, list[PlannedSession]],
               availability_min: int, race_day: Optional[date] = None,
               longest_min: Optional[int] = None, easy_only: bool = False,
               allow_double: bool = False) -> list[str]:
    """Return the list of rules ``cand`` would break if placed on ``day``.

    Sessions with sport ``"other"`` are training the athlete already does elsewhere
    (fight practice, climbing, team sport). Their time is on top of the free time
    Taper fills, but they count as sessions and, when hard, as hard days.
    ``easy_only`` marks a recovery day (e.g. after a night shift): no hard or heavy work.
    """
    out: list[str] = []
    today = [s for s in by_day.get(day, []) if s is not cand and s.status != "missed"]
    prev = [s for s in by_day.get(day - timedelta(days=1), []) if s.status != "missed"]
    nxt = [s for s in by_day.get(day + timedelta(days=1), []) if s.status != "missed"]

    if availability_min <= 0:
        out.append("no availability that day")
    used = sum(s.minutes for s in today if s.sport != "other")
    if used + cand.minutes > availability_min * OVERFLOW:
        out.append(f"exceeds availability ({used + cand.minutes} > {availability_min} min)")
    if longest_min is not None and cand.minutes > longest_min * OVERFLOW:
        out.append(f"no free slot of {cand.minutes} min (longest is {longest_min})")
    if cand.sport != "mobility" and len([s for s in today if s.sport != "mobility"]) >= MAX_PER_DAY:
        out.append("already two sessions that day")      # a short mobility routine doesn't count
    if easy_only and (cand.hard or cand.heavy_legs):
        out.append("recovery day (after a night shift): easy sessions only")

    if race_day:
        if day == race_day and cand.kind != "race":
            out.append("race day")
        if day == race_day - timedelta(days=1) and cand.hard and cand.kind != "race":
            out.append("hard session the day before the race")
        if day > race_day:
            out.append("after the race")

    # one session per sport per day. Two easy sessions of a sport on one day is an advanced habit
    # (``allow_double``, top stages only); for everyone else the planner merges the volume instead.
    for s in today:
        if s.sport == cand.sport and not (allow_double and cand.sport in ENDURANCE and not s.hard
                                          and not cand.hard and "Walk @" not in s.dsl + cand.dsl):
            out.append(f"a second {cand.sport} session that day")
            break
    if cand.sport == "run" and "Walk @" in cand.dsl and any(s.sport == "run" for s in prev + nxt):
        out.append("beginner run-walks need a rest day in between")
    if cand.hard:
        # a hard swim may share a day with hard land work (common triathlon double), not two land sessions
        if any(s.hard and (s.sport == cand.sport or "swim" not in (s.sport, cand.sport)) for s in today):
            out.append("two hard sessions on the same day")
        for s in prev + nxt:
            if s.hard and s.sport == cand.sport:
                out.append(f"back-to-back hard {cand.sport} days")
                break
        if cand.sport in ENDURANCE and any(s.heavy_legs for s in prev):
            out.append("heavy leg day the day before a key endurance session")
    if cand.heavy_legs:
        if any(s.hard and s.sport in ENDURANCE for s in nxt):
            out.append("heavy legs the day before a key endurance session")
        for s in nxt:
            if s.sport == "other" and s.hard:
                out.append(f"heavy legs the day before {s.title}")
                break
        if any(s.heavy_legs for s in prev + nxt):
            out.append("consecutive heavy leg days")
    return out


def group_by_day(sessions: list[PlannedSession]) -> dict[date, list[PlannedSession]]:
    out: dict[date, list[PlannedSession]] = {}
    for s in sessions:
        out.setdefault(s.day, []).append(s)
    return out
