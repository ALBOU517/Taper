from datetime import date, timedelta

import pytest

from app.engine import dsl, ics, load, rebalancer
from app.engine import periodization as P
from app.engine.constraints import PlannedSession, group_by_day, violations


# ----------------------------------------------------------------- DSL
def test_parse_run_intervals():
    w = dsl.parse("Run: Warmup 15m @ Z2 -> 5x(1km @ 4:15/km, Rest 90s) -> Cooldown 10m")
    assert w.sport == "run"
    assert w.steps[0].role == "warmup" and w.steps[0].duration_s == 900
    rep = w.steps[1]
    assert isinstance(rep, dsl.Repeat) and rep.count == 5
    assert rep.steps[0].distance_m == 1000
    assert rep.steps[0].target.kind == "pace" and rep.steps[0].target.value == 255
    assert rep.steps[1].role == "rest" and rep.steps[1].duration_s == 90
    assert len(dsl.flatten(w.steps)) == 12


def test_parse_swim_shorthand_repeat_with_rest():
    w = dsl.parse("Swim: 400m Easy -> 8x100m Freestyle @ 1:45/100m (20s rest) -> 200m Pull Buoy")
    rep = w.steps[1]
    assert rep.count == 8 and rep.steps[0].distance_m == 100 and rep.steps[1].duration_s == 20
    assert w.steps[0].role == "easy" and w.steps[0].distance_m == 400


def test_parse_gym_superset():
    w = dsl.parse("Gym: Back Squat 4x6 @ RPE 8 | Superset: Romanian Deadlift 3x10 + Hanging Leg Raises 3x15")
    assert isinstance(w.steps[0], dsl.Exercise) and w.steps[0].rpe == 8
    assert isinstance(w.steps[1], dsl.Superset) and len(w.steps[1].exercises) == 2


def test_parse_pounds_converted():
    w = dsl.parse("Gym: Bench Press 5x5 @ 225lb")
    assert w.steps[0].load_kg == pytest.approx(102.1, 0.1)


@pytest.mark.parametrize("bad", ["Run 10min", "Ski: 10min", "Run: 5x(1km @ Z9)", "Run: 5x(1km", "Gym: Squat heavy"])
def test_parse_errors_are_friendly(bad):
    with pytest.raises(dsl.DSLError):
        dsl.parse(bad)


def test_m_means_minutes_for_short_run_values_and_meters_for_swims():
    assert dsl.parse("Run: Easy 30m").steps[0].duration_s == 1800
    assert dsl.parse("Run: 400m @ Z5").steps[0].distance_m == 400
    assert dsl.parse("Swim: 50m Kick").steps[0].distance_m == 50


# ----------------------------------------------------------------- load model
def test_tss_one_hour_at_threshold_is_100():
    w = dsl.parse("Bike: 60min @ 100%FTP")
    assert load.estimate(w).tss == pytest.approx(100, abs=0.5)


def test_swim_uses_cubic_intensity():
    th = load.Thresholds(swim_css=100)
    easy = load.estimate(dsl.parse("Swim: 3000m @ 2:00/100m"), th)    # IF = 0.833
    assert easy.tss == pytest.approx(1.0 * 0.8333 ** 3 * 100, rel=0.02)


def test_pace_target_drives_duration():
    est = load.estimate(dsl.parse("Run: 10km @ 5:00/km"))
    assert est.duration_s == 3000 and est.distance_m == 10000


def test_banister_converges_to_steady_load():
    start = date(2026, 1, 1)
    daily = {start + timedelta(days=i): 50.0 for i in range(400)}
    pts = load.banister(daily, start, start + timedelta(days=399))
    assert pts[-1].ctl == pytest.approx(50, abs=0.5)
    assert pts[-1].atl == pytest.approx(50, abs=0.1)
    assert abs(pts[-1].tsb) < 1


def test_readiness_flags_aggressive_ramp():
    assert load.readiness(tsb=0, ctl=50, ramp_7d=10)["status"] == "risk"
    assert load.readiness(tsb=12, ctl=50, ramp_7d=2)["status"] == "fresh"


# ----------------------------------------------------------------- periodization
def test_macrocycle_is_built_backwards_from_race():
    phases = P.macrocycle(16, "half_marathon")
    names = [p for p, _ in phases]
    assert names[-1] == "race" and names[-3:-1] == ["taper", "taper"]
    assert names.index("build") > names.index("base")
    assert "peak" in names
    assert any(r for _, r in phases)                      # deload weeks exist
    assert not phases[-1][1]


@pytest.fixture(scope="module")
def half_plan():
    goal = P.Goal("half_marathon", race_date=date(2027, 5, 9), start_date=date(2026, 11, 17),
                  level="intermediate", baseline_hours=4,
                  levels={"run": {"stage": 6, "assessed": True}, "gym": {"stage": 2, "assessed": True}})
    return P.generate(goal)


def test_plan_respects_recovery_rules(half_plan):
    by_day = group_by_day(half_plan.sessions)
    avail = half_plan.goal.availability
    for s in half_plan.sessions:
        v = violations(s, s.day, by_day, avail[s.day.weekday()], half_plan.goal.race_date,
                       allow_double=s.sport == "run")        # this athlete is at the top run stage
        v = [x for x in v if "already two" not in x]      # count check includes itself
        assert not v, (s.day, s.title, v)


def test_plan_has_race_and_nothing_after(half_plan):
    race = [s for s in half_plan.sessions if s.kind == "race"]
    assert race and race[0].day == date(2027, 5, 9)
    assert max(s.day for s in half_plan.sessions) == date(2027, 5, 9)
    assert min(s.day for s in half_plan.sessions) >= date(2026, 11, 17)


def test_plan_ramp_is_safe_and_race_day_is_fresh(half_plan):
    assert half_plan.projection["max_ramp"] <= P.RAMP["intermediate"] + 1
    assert half_plan.projection["race_day_tsb"] > 0


def test_planned_load_tracks_target(half_plan):
    loading = [w for w in half_plan.weeks if w.phase in ("base", "build") and not w.recovery
               and not any("Capped by your weekly availability" in n for n in w.notes)]
    assert len(loading) >= 6
    for w in loading:
        assert abs(w.endurance_tss - w.target_tss) / w.target_tss < 0.15


def test_deload_weeks_are_lighter(half_plan):
    w = half_plan.weeks
    for i, wk in enumerate(w):
        if wk.recovery and i > 0:
            assert wk.target_tss < w[i - 1].target_tss * 0.75


def test_every_generated_session_parses(half_plan):
    for s in half_plan.sessions:
        dsl.parse(s.dsl)


@pytest.mark.parametrize("event", list(P.EVENTS))
def test_all_events_generate(event):
    goal = P.Goal(event, race_date=date(2027, 3, 1), start_date=date(2026, 11, 17),
                  availability={0: 90, 1: 90, 2: 90, 3: 90, 4: 0, 5: 180, 6: 180})
    plan = P.generate(goal)
    assert plan.sessions and plan.weeks


# ----------------------------------------------------------------- rebalancer
def _s(day, sport, kind, minutes, tss, dsl_text, title=None, sid=None):
    return PlannedSession(day, sport, kind, title or f"{sport} {kind}", dsl_text, minutes, tss, id=sid)


def test_missed_key_session_is_moved_without_breaking_rules():
    mon = date(2026, 11, 16)
    week = [
        _s(mon + timedelta(1), "run", "quality", 55, 70, "Run: Warmup 15min @ Z2 -> 3x(8min @ Z4, Rest 2min) -> Cooldown 10min @ Z1", "Threshold intervals", 1),
        _s(mon + timedelta(3), "run", "easy", 45, 35, "Run: Easy 45min @ Z2", "Easy run", 2),
        _s(mon + timedelta(5), "run", "easy", 45, 35, "Run: Easy 45min @ Z2", "Easy run", 3),
        _s(mon + timedelta(6), "run", "long", 100, 85, "Run: Long 100min @ Z2", "Long run", 4),
    ]
    avail = {0: 60, 1: 75, 2: 60, 3: 75, 4: 0, 5: 120, 6: 150}
    changes = rebalancer.rebalance(week[0], week, today=mon + timedelta(1), availability=avail)
    moved = [c for c in changes if c.action == "moved"]
    assert moved and moved[0].new_day == mon + timedelta(2)      # Wednesday is free and legal


def test_missed_key_session_swaps_out_easy_when_needed():
    mon = date(2026, 11, 16)
    week = [
        _s(mon + timedelta(1), "run", "quality", 55, 70, "Run: Warmup 15min @ Z2 -> 3x(8min @ Z4, Rest 2min) -> Cooldown 10min @ Z1", "Threshold intervals", 1),
        _s(mon + timedelta(3), "run", "easy", 45, 35, "Run: Easy 45min @ Z2", "Easy run", 2),
        _s(mon + timedelta(6), "run", "long", 100, 85, "Run: Long 100min @ Z2", "Long run", 4),
    ]
    # Wednesday unavailable, Saturday is the day before the long run -> only Thursday works
    avail = {0: 60, 1: 75, 2: 0, 3: 75, 4: 0, 5: 120, 6: 150}
    changes = rebalancer.rebalance(week[0], week, today=mon + timedelta(1), availability=avail)
    moved = [c for c in changes if c.action == "moved"]
    assert moved and moved[0].new_day == mon + timedelta(3)
    assert any(c.action == "dropped" and c.session_id == 2 for c in changes)


def test_missed_easy_volume_is_partially_redistributed():
    mon = date(2026, 11, 16)
    week = [
        _s(mon, "run", "easy", 45, 35, "Run: Easy 45min @ Z2", "Easy run", 1),
        _s(mon + timedelta(2), "run", "easy", 40, 30, "Run: Easy 40min @ Z2", "Easy run", 2),
        _s(mon + timedelta(5), "run", "easy", 40, 30, "Run: Easy 40min @ Z2", "Easy run", 3),
    ]
    avail = {i: 90 for i in range(7)}
    changes = rebalancer.rebalance(week[0], week, today=mon, availability=avail)
    ext = [c for c in changes if c.action == "extended"]
    assert ext
    gained = sum(c.new_tss for c in ext) - sum(s.tss for s in week[1:] if s.id in {c.session_id for c in ext})
    assert 0 < gained <= 35 * rebalancer.RECOVER_FRACTION + 3
    for c in ext:
        orig = next(s for s in week if s.id == c.session_id)
        assert c.new_minutes <= orig.minutes * 1.3


def test_heavy_legs_never_before_long_run():
    sat, sun = date(2026, 11, 21), date(2026, 11, 22)
    long_run = _s(sun, "run", "long", 100, 85, "Run: Long 100min @ Z2")
    squat = _s(sat, "gym", "strength", 50, 30, "Gym: Back Squat 4x5 @ RPE 8")
    v = violations(squat, sat, group_by_day([long_run]), 120)
    assert any("heavy legs" in x for x in v)


# ----------------------------------------------------------------- ics
def test_ics_feed_is_valid_ish():
    text = ics.build_calendar([{"id": 1, "day": date(2026, 11, 20), "sport": "run", "title": "Easy run",
                                "dsl": "Run: Easy 45min @ Z2", "minutes": 45, "tss": 35, "status": "planned"}])
    assert text.startswith("BEGIN:VCALENDAR") and "BEGIN:VALARM" in text and text.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_maintenance_lifting_is_not_heavy_legs():
    light = _s(date(2026, 11, 21), "gym", "strength", 30, 15, "Gym: Back Squat 2x5 @ RPE 7 | Superset: Single-Leg RDL 2x8 + Copenhagen Plank 2x8")
    heavy = _s(date(2026, 11, 21), "gym", "strength", 50, 30, "Gym: Back Squat 4x5 @ RPE 8")
    assert not light.heavy_legs and heavy.heavy_legs


def test_swim_quality_can_share_a_day_with_bike_quality():
    tue = date(2026, 11, 17)
    bike = _s(tue, "bike", "quality", 60, 60, "Bike: 60min @ Z3")
    swim = _s(tue, "swim", "quality", 45, 40, "Swim: 2000m @ Z4")
    run = _s(tue, "run", "quality", 45, 50, "Run: 45min @ Z4")
    assert not violations(swim, tue, group_by_day([bike]), 120)
    assert any("same day" in v for v in violations(run, tue, group_by_day([bike]), 120))


def test_session_longer_than_any_day_is_capped_not_dropped():
    goal = P.Goal("marathon", race_date=date(2027, 6, 20), start_date=date(2026, 11, 16),
                  availability={0: 60, 1: 75, 2: 60, 3: 75, 4: 0, 5: 120, 6: 150}, baseline_hours=6)
    plan = P.generate(goal)
    longs = [s for s in plan.sessions if s.kind == "long"]
    weeks_with_long = {(s.day - goal.start_date).days // 7 for s in longs}
    assert len(weeks_with_long) >= len(plan.weeks) - 2          # every week keeps its long run
    assert all(s.minutes <= 150 * 1.1 for s in longs)


def test_calendar_availability_and_exam_weeks_drive_the_plan():
    start = date(2026, 11, 16)
    by_date = {}
    for i in range(200):
        d = start + timedelta(i)
        by_date[d] = (0, 0) if d.weekday() == 1 else ((40, 40) if d.weekday() == 3 else (180, 120))
    routines = [{"name": "Push", "dsl": "Gym: Bench Press 4x8 @ RPE 8", "minutes": 90},
                {"name": "Legs", "dsl": "Gym: Leg Press 4x10 @ RPE 8", "minutes": 90}]
    goal = P.Goal("half_marathon", race_date=date(2027, 5, 2), start_date=start, level="beginner",
                  availability_by_date=by_date, exam_weeks={date(2026, 12, 7)}, gym_per_week=2, gym_routines=routines)
    plan = P.generate(goal)
    assert not any(s.day.weekday() == 1 for s in plan.sessions)                 # busy Tuesdays stay empty
    assert all(s.minutes <= 40 for s in plan.sessions if s.day.weekday() == 3)  # 40-min window respected
    exam = next(w for w in plan.weeks if w.start == date(2026, 12, 7))
    assert exam.recovery and "Exam" in exam.notes[0]
    gyms = [s for s in plan.sessions if s.sport == "gym"]
    assert {s.title for s in gyms} == {"Push", "Legs"} and all(s.minutes == 90 for s in gyms)
    longs = [s for s in plan.sessions if s.kind == "long" and s.sport == "run"]
    assert longs[0].minutes <= P.LONG_START["beginner"] + 5


def test_ics_timed_event_when_slot_known():
    text = ics.build_calendar([{"id": 2, "day": date(2026, 11, 20), "sport": "run", "title": "Easy run",
                                "dsl": "Run: Easy 45min @ Z2", "minutes": 45, "tss": 35, "status": "planned",
                                "start_time": "17:30"}])
    assert "DTSTART:20261120T173000" in text and "DTEND:20261120T181500" in text and "TRIGGER:-PT60M" in text


def test_no_second_run_on_long_run_day_and_beginner_rest_days():
    sat = date(2026, 11, 21)
    long_run = _s(sat, "run", "long", 90, 80, "Run: Long 90min @ Z2")
    easy = _s(sat, "run", "easy", 30, 20, "Run: Easy 30min @ Z2")
    assert any("second run" in v for v in violations(easy, sat, group_by_day([long_run]), 180))
    g1 = _s(sat, "gym", "strength", 40, 20, "Gym: Bench Press 3x8 @ RPE 7")
    g2 = _s(sat, "gym", "strength", 40, 20, "Gym: Lat Pulldown 3x8 @ RPE 7")
    assert any("second gym" in v for v in violations(g2, sat, group_by_day([g1]), 180))
    easy2 = _s(sat, "run", "easy", 30, 20, "Run: Easy 30min @ Z2")
    assert violations(easy2, sat, group_by_day([easy]), 180)              # one run a day by default
    assert not violations(easy2, sat, group_by_day([easy]), 180, allow_double=True)   # advanced: easy double ok
    rw = "Run: 5min Walk @ Z1 -> 6x(1min @ Z2, 2min Walk @ Z1)"
    mon = _s(date(2026, 11, 16), "run", "easy", 28, 15, rw)
    tue = _s(date(2026, 11, 17), "run", "easy", 28, 15, rw)
    assert any("rest day" in v for v in violations(tue, tue.day, group_by_day([mon]), 60))
