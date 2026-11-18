"""
Scenario bench: real-life use cases as JSON files in ``backend/scenarios/``, run
through the planner and checked against universal rules plus each scenario's
own expectations.

    python -m app.scenarios            # readable report for every scenario
    python -m app.scenarios 02         # only files starting with 02
    pytest tests/test_scenarios.py     # same checks as a test suite

Universal rules (every scenario):
  * every session fits that day's free time (10 % grace) and parses
  * no second session of a sport on a day with a key session of that sport
  * beginner run-walk days have a rest day between them
  * level checks come before that sport's other sessions in week 1
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .engine import dsl, load
from .engine import nutrition as N
from .engine import periodization as P
from .engine.constraints import group_by_day
from .life import weight_goal

# equipment a strength session must not need, by what the athlete has
_BAR = r"Back Squat|Front Squat|Pause Squat|Bench Press|Barbell|Deadlift(?! *,)|Overhead Press|Leg Press|Hack Squat|Lat Pulldown|Cable|Chest Press|Leg Curl|Leg Extension(?! *,)|Pull-ups|Dips\b|Hanging"
BANNED = {"gym": re.compile(r"(?!x)x"), "home": re.compile(rf"(?<![\w-])(?<!Dumbbell )(?<!Single-Leg )(?<!Dumbbell Romanian )({_BAR})"),
          "none": re.compile(rf"(?<![\w-])(?<!Single-Leg )(?<!Single-Leg Romanian )({_BAR}|Dumbbell|Band |Kettlebell|Swiss-Ball)")}
DIR = Path(__file__).resolve().parents[1] / "scenarios"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
START = date(2026, 11, 23)          # a fixed Monday keeps reports reproducible


@dataclass
class Result:
    name: str
    story: str
    checks: list = field(default_factory=list)     # (label, passed, detail)
    weeks: list = field(default_factory=list)      # sample weeks for the report
    summary: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c[1] for c in self.checks)


def _target_seconds(text: str | None) -> int | None:
    if not text:
        return None
    secs = 0
    for x in text.split(":"):
        secs = secs * 60 + int(x)
    return secs


def shift_calendar(spec: dict | None, weeks: int) -> tuple[dict, set]:
    """Stand-in for an imported shift calendar: a repeating rotation of D (day), N (night), O (off).

    Day shift: no time. Night shift: free before it. The day a night ends: free in the
    afternoon but marked as a recovery day — exactly what Taper derives from a real calendar.
    """
    if not spec:
        return {}, set()
    pat = spec["pattern"]
    by_date, easy = {}, set()
    for k in range(weeks * 7 + 7):
        d = START + timedelta(days=k)
        today, yesterday = pat[k % len(pat)], pat[(k - 1) % len(pat)]
        if today == "D":
            mins = 0
        elif yesterday == "N":
            mins = spec["after_night_min"]
            easy.add(d)
        elif today == "N":
            mins = spec["before_night_min"]
        else:
            mins = spec["off_min"]
        by_date[d] = (mins, mins)
    return by_date, easy


def build(sc: dict) -> tuple[P.Plan, P.Goal, load.Thresholds]:
    g = sc["goal"]
    weeks = g.get("race_in_weeks") or g.get("block_weeks") or 12
    race = START + timedelta(weeks=weeks, days=6 if g.get("event") != "custom" else 0)
    av = {DAYS.index(k): v for k, v in sc["availability"].items()}
    levels = {k: {"stage": v, "assessed": True} for k, v in (sc.get("levels") or {}).items()}
    th = load.Thresholds.from_dict(sc.get("thresholds"))
    by_date, easy = shift_calendar(sc.get("shifts"), weeks)
    goal = P.Goal(g.get("event", "custom"), race, START, level=g.get("level", "beginner"), availability=av,
                  baseline_hours=g.get("baseline_hours", 3), gym_per_week=g.get("gym_per_week", 2), levels=levels,
                  sports=g.get("sports", []), target_time_s=_target_seconds(g.get("target_time")),
                  body_focus=bool(g.get("body_focus")), gym_routines=sc.get("routines", []),
                  equipment=g.get("equipment", "gym"), away_days={DAYS.index(d) for d in g.get("away_days", [])},
                  commitments=[{**c, "day": DAYS.index(c["day"])} for c in g.get("commitments", [])],
                  availability_by_date=by_date, easy_dates=easy, mobility_per_week=g.get("mobility_per_week"))
    return P.generate(goal, th), goal, th


def run(path: Path) -> Result:
    sc = json.loads(path.read_text())
    plan, goal, th = build(sc)
    r = Result(sc["name"], sc.get("story", ""))
    ex = sc.get("expect", {})
    by_day = group_by_day(plan.sessions)
    av = goal.availability

    def check(label: str, ok: bool, detail: str = "") -> None:
        r.checks.append((label, bool(ok), detail))

    # ---- universal rules
    def free(d):
        return goal.availability_by_date[d][0] if d in goal.availability_by_date else av.get(d.weekday(), 0)
    over = [s for s in plan.sessions if s.kind != "race" and sum(x.minutes for x in by_day[s.day] if x.kind != "race")
            > free(s.day) * 1.1 + 1]
    check("Every day fits the free time", not over,
          f"{len(over)} over, e.g. {over[0].day:%a %d %b} {over[0].title}" if over else "")
    bad = []
    for s in plan.sessions:
        try:
            dsl.parse(s.dsl)
        except dsl.DSLError as e:
            bad.append(f"{s.title}: {e}")
    check("Every workout is valid", not bad, "; ".join(bad[:2]))
    doubles = [d for d, ss in by_day.items() for sp in {x.sport for x in ss}
               if len([x for x in ss if x.sport == sp]) > 1
               and (sp not in ("run", "bike", "swim") or any(x.hard for x in ss if x.sport == sp))]
    check("No second session of a sport on its key day", not doubles,
          f"{len(doubles)} days, e.g. {doubles[0]}" if doubles else "")
    rw = sorted(s.day for s in plan.sessions if s.sport == "run" and "Walk @" in s.dsl)
    back_to_back = [d for d in rw if d - timedelta(days=1) in rw]
    check("Beginner run-walks have rest days between", not back_to_back,
          f"e.g. {back_to_back[0]}" if back_to_back else "")
    w1 = [s for s in plan.sessions if s.day < plan.weeks[0].start + timedelta(days=7)]
    late = [t.sport for t in w1 if t.kind == "test" and any(o.sport == t.sport and o.kind != "test" and o.day < t.day
                                                            for o in w1)]
    check("Level checks come first", not late, ", ".join(late))

    # ---- scenario expectations
    skipped = sum(1 for w in plan.weeks for n in w.notes if n.startswith("Skipped"))
    if "max_skipped_sessions" in ex:
        check(f"At most {ex['max_skipped_sessions']} sessions skipped for lack of time", skipped <= ex["max_skipped_sessions"],
              f"{skipped} skipped")
    if "sports" in ex:
        got = sorted({s.sport for s in plan.sessions if s.kind not in ("race",)})
        check("Only the chosen sports", got == sorted(ex["sports"]), f"got {', '.join(got)}")
    if "gym_per_week" in ex:
        lo, hi = ex["gym_per_week"]
        counts = [sum(1 for s in plan.sessions if s.sport == "gym" and w.start <= s.day < w.start + timedelta(7))
                  for w in plan.weeks if w.phase not in ("race", "taper") and not w.recovery]
        check(f"{lo}–{hi} strength sessions in normal weeks", all(lo <= c <= hi for c in counts),
              f"per week: {sorted(set(counts))}")
    if "no_sessions_on" in ex:
        idx = {DAYS.index(d) for d in ex["no_sessions_on"]}
        hits = [s for s in plan.sessions if s.day.weekday() in idx and s.kind != "race"]
        check(f"Nothing on {', '.join(ex['no_sessions_on'])}", not hits, f"{len(hits)} sessions")
    if ex.get("long_run_alone"):
        bad_days = [d for d, ss in by_day.items() if any(x.sport == "run" and x.kind == "long" for x in ss)
                    and sum(1 for x in ss if x.sport == "run") > 1]
        check("Long run is the only run that day", not bad_days, f"{len(bad_days)} days")
    if ex.get("has_taper"):
        check("Ends with a taper", any(w.phase == "taper" for w in plan.weeks))
    if "longest_run_min" in ex:
        lo, hi = ex["longest_run_min"]
        m = max((s.minutes for s in plan.sessions if s.sport == "run" and s.kind != "race"), default=0)
        check(f"Longest run {lo}–{hi} min", lo <= m <= hi, f"{m} min")
    if ex.get("beginner_rest_days"):
        check("Beginner gets rest days between runs", not back_to_back)
    if "max_runs_first_week" in ex:
        n = sum(1 for s in w1 if s.sport == "run")
        check(f"At most {ex['max_runs_first_week']} runs in week 1", n <= ex["max_runs_first_week"], f"{n} runs")
    if ex.get("first_runs_are_walk_run"):
        first = [s for s in w1 if s.sport == "run" and s.kind != "test"]
        check("Week 1 runs are run-walk", first and all("Walk @" in s.dsl for s in first))
    if "level_checks_first" in ex:
        tests = {s.sport for s in w1 if s.kind == "test"}
        check("Level check scheduled for each new sport", set(ex["level_checks_first"]) <= tests,
              f"checks: {', '.join(sorted(tests))}")
    if ex.get("on_track"):
        off = [k for k, v in plan.projection["levels"].items() if not v["on_track"]]
        check("Goal level reachable by the date", not off, ", ".join(off))
    if "target_realistic" in ex:
        t = plan.projection.get("target") or {}
        check("Target time judged realistic" if ex["target_realistic"] else "Target time flagged as ambitious",
              t.get("realistic") == ex["target_realistic"],
              f"needs {t.get('improvement_pct')} % faster threshold" if t else "no target")
    if "weekly_deficit_kcal" in ex and sc.get("body"):
        b, nut = sc["body"], sc.get("nutrition", {})
        rate = (nut["goal_weight_kg"] - b["weight_kg"]) / b["weight_kg"] / nut["goal_weeks"] * 100
        deficit = -rate / 100 * b["weight_kg"] * N.KCAL_PER_KG
        lo, hi = ex["weekly_deficit_kcal"]
        check(f"Weekly deficit {lo}–{hi} kcal", lo <= deficit <= hi, f"{deficit:.0f} kcal/week ({rate:.2f} %/week)")

    gyms = [s for s in plan.sessions if s.sport == "gym"]
    if "equipment" in ex:
        banned = BANNED[ex["equipment"]]
        bad_g = [s for s in gyms if banned.search(s.dsl)]
        check(f"Strength sessions only use {ex['equipment']} equipment", not bad_g,
              f"e.g. {bad_g[0].dsl[:70]}" if bad_g else "")
    if "no_equipment_on" in ex:
        idx = {DAYS.index(d) for d in ex["no_equipment_on"]}
        bad_g = [s for s in gyms if s.day.weekday() in idx and BANNED["none"].search(s.dsl)]
        n = sum(1 for s in gyms if s.day.weekday() in idx)
        check(f"No-equipment strength on {', '.join(ex['no_equipment_on'])}", not bad_g,
              f"{len(bad_g)} of {n} need equipment" if bad_g else f"{n} bodyweight sessions")
    if ex.get("respects_commitments"):
        cdays = {c["day"] for c in goal.commitments if c.get("hard", True)}
        same = [s for s in plan.sessions if s.day.weekday() in cdays and s.hard]
        before = [s for s in plan.sessions if s.heavy_legs and (s.day.weekday() + 1) % 7 in cdays]
        check("No hard session on your own training days", not same, f"{len(same)} found")
        check("No heavy legs the day before your own hard training", not before,
              f"e.g. {before[0].day:%a %d %b} {before[0].title}" if before else "")
    if ex.get("easy_after_night_shifts"):
        bad_e = [s for s in plan.sessions if s.day in goal.easy_dates and (s.hard or s.heavy_legs)]
        n = sum(1 for s in plan.sessions if s.day in goal.easy_dates)
        check("Only easy sessions after night shifts", not bad_e,
              f"e.g. {bad_e[0].day:%a %d %b} {bad_e[0].title}" if bad_e else f"{n} easy sessions on recovery days")
    if "max_session_min" in ex:
        m = max((s.minutes for s in plan.sessions if s.kind != "race"), default=0)
        check(f"No session longer than {ex['max_session_min']} min", m <= ex["max_session_min"], f"longest {m} min")
    for sp, (lo, hi) in (ex.get("longest_session_min") or {}).items():
        m = max((s.minutes for s in plan.sessions if s.sport == sp and s.kind != "race"), default=0)
        check(f"Longest {sp} session {lo}–{hi} min", lo <= m <= hi, f"{m} min")
    if "deload_every" in ex:
        rec = [w.index + 1 for w in plan.weeks if w.recovery]
        n = ex["deload_every"]
        check(f"Deload week every {n}th week", rec and all(r % n == 0 for r in rec), f"weeks {rec}")
    if ex.get("deload_keeps_sessions"):
        rw = [w for w in plan.weeks if w.recovery]
        counts = [sum(1 for s in gyms if w.start <= s.day < w.start + timedelta(7)) for w in rw]
        deloaded = all(s.title.startswith("Deload") for s in gyms
                       if any(w.start <= s.day < w.start + timedelta(7) for w in rw))
        check("Deload weeks keep every lifting day, lighter", rw and min(counts) == goal.gym_per_week and deloaded,
              f"sessions in deload weeks: {counts}")
    if "weekly_rate_pct" in ex and sc.get("body"):
        b, nut = sc["body"], sc["nutrition"]
        wg = weight_goal({"goal_weight_kg": nut["goal_weight_kg"],
                          "goal_date": (START + timedelta(weeks=nut["goal_weeks"])).isoformat()}, b["weight_kg"], START)
        lo, hi = ex["weekly_rate_pct"]
        check(f"Weight change {lo}–{hi} % per week", lo <= wg["rate"] <= hi,
              f"{wg['rate']} %/week (asked {wg['rate_needed']}), reaches {nut['goal_weight_kg']} kg on {wg['eta']}")
        if "rate_capped" in ex:
            check("Unsafe rate capped and explained" if ex["rate_capped"] else "Rate not capped",
                  wg["capped"] == ex["rate_capped"])

    # ---- sample weeks for the report
    picks = sorted({0, len(plan.weeks) // 2, max(0, len(plan.weeks) - 3)})
    for i in picks:
        w = plan.weeks[i]
        ss = [s for s in plan.sessions if w.start <= s.day < w.start + timedelta(days=7)]
        r.weeks.append({"week": i + 1, "phase": w.phase + (" (deload)" if w.recovery else ""),
                        "sessions": [f"{s.day:%a} {s.title} ({s.minutes} min)" for s in ss]})
    r.summary = {"weeks": len(plan.weeks), "levels": {k: f"{v['now_name']} → {v['at_goal_name']}"
                                                      for k, v in plan.projection["levels"].items()},
                 "target": plan.projection.get("target"), "skipped": skipped}
    return r


def scenarios(prefix: str = "") -> list[Path]:
    return sorted(p for p in DIR.glob("*.json") if p.name.startswith(prefix))


def report(results: list[Result]) -> str:
    out = []
    for r in results:
        out.append(f"\n{'✅' if r.ok else '❌'} {r.name}\n   {r.story}")
        for label, ok, detail in r.checks:
            out.append(f"   {'✓' if ok else '✗'} {label}{' — ' + detail if detail else ''}")
        out.append(f"   Levels: {r.summary['levels']}")
        if r.summary.get("target"):
            t = r.summary["target"]
            out.append(f"   Target: race pace {t['race_pace'] // 60}:{t['race_pace'] % 60:02d}/km, "
                       f"{'realistic' if t.get('realistic') else 'ambitious'}")
        for w in r.weeks:
            out.append(f"   Week {w['week']} ({w['phase']}): " + "; ".join(w["sessions"]))
    passed = sum(r.ok for r in results)
    out.append(f"\n{passed}/{len(results)} scenarios pass")
    return "\n".join(out)


if __name__ == "__main__":
    res = []
    for p in scenarios(sys.argv[1] if len(sys.argv) > 1 else ""):
        try:
            res.append(run(p))
        except Exception as e:  # a crash is a failed scenario, not a failed bench
            r = Result(p.stem, f"CRASH: {type(e).__name__}: {e}")
            r.checks.append(("Plan generates", False, str(e)[:120]))
            r.summary = {"levels": {}, "skipped": 0}
            res.append(r)
    print(report(res))
    sys.exit(0 if all(r.ok for r in res) else 1)
