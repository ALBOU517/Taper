from datetime import date

from app.engine import dsl
from app.engine import ladder as L
from app.engine import periodization as P


def test_run_placement_from_level_check():
    assert L.place_run(continuous_min=2, distance_km=1.8).stage == 0
    assert L.place_run(continuous_min=9, distance_km=2.4).stage == 1
    assert L.place_run(continuous_min=20, distance_km=2.7).stage == 2
    assert L.place_run(continuous_min=20, distance_km=3.4).stage == 3
    assert L.place_run(continuous_min=20, distance_km=4.2).stage == 4
    assert L.place_run(continuous_min=20, distance_km=5.6).stage == 6
    fast_but_walked = L.place_run(continuous_min=16, distance_km=4.0)
    assert fast_but_walked.stage == 2


def test_run_threshold_estimate():
    p = L.place_run(continuous_min=20, distance_km=4.0)          # 5:00/km moderate
    assert p.thresholds["run_threshold_pace"] == round(300 / 1.10)
    hard = L.place_run(continuous_min=20, distance_km=4.0, rpe=8)
    assert hard.thresholds["run_threshold_pace"] == round(300 / 1.03)
    assert "run_threshold_pace" not in L.place_run(continuous_min=8, distance_km=1.5).thresholds


def test_swim_bike_gym_placement():
    assert L.place_swim(10).stage == 0 and L.place_swim(50).stage == 1 and L.place_swim(200).stage == 2
    s = L.place_swim(400, 400, 400)                               # 1:40/100 m
    assert s.stage == 4 and s.thresholds["swim_css"] == 100
    assert L.place_bike(20, 6).stage == 0
    assert L.place_bike(30, 12).stage == 2
    b = L.place_bike(20, 9, avg_power=250, rpe=9)
    assert b.thresholds["bike_ftp"] == round(250 * 0.95)
    assert "bike_ftp" not in L.place_bike(30, 12, avg_power=200, rpe=6).thresholds
    assert [L.place_gym(m).stage for m in (0, 3, 12, 40)] == [0, 1, 2, 3]


def test_weekly_progression_up_hold_down():
    good = L.WeekStats(planned=3, done=3, easy_rpes=[5, 5])
    s, p, why = L.progress(0, 0.0, "run", good)
    assert (s, round(p, 2)) == (0, round(1 / 3, 2)) and "Completed 3/3" in why
    s, p, _ = L.progress(0, 0.7, "run", good)
    assert s == 1 and p == 0                                       # moved up
    s, p, why = L.progress(2, 0.5, "run", L.WeekStats(3, 2, [6]))
    assert (s, p) == (2, 0.5) and "holding" in why
    s, p, why = L.progress(2, 0.1, "run", L.WeekStats(3, 3, [8, 8]))
    assert s == 1 and "back to" in why                              # easy runs felt hard → step back


def test_stage_sessions_parse_and_walk_run_has_walks():
    for sport, ladder_ in L.LADDERS.items():
        for st in ladder_:
            out = L.stage_session(sport, st, "easy", 30)
            if out:
                dsl.parse(out[1])
    _, text = L.stage_session("run", L.RUN[0], "easy", 30)
    assert "Walk" in text and "1min @ Z2" in text
    for _title, text, _ in L.LEVEL_CHECK.values():
        dsl.parse(text)


def test_never_ran_to_half_marathon_in_8_months():
    """The athlete's example: no running background, half marathon ~34 weeks away."""
    goal = P.Goal("half_marathon", race_date=date(2027, 7, 11), start_date=date(2026, 11, 16), level="beginner",
                  baseline_hours=2, levels={"run": {"stage": 0, "assessed": True}, "gym": {"stage": 1, "assessed": True}},
                  availability={0: 60, 1: 60, 2: 60, 3: 60, 4: 0, 5: 90, 6: 120})
    plan = P.generate(goal)
    runs = [s for s in plan.sessions if s.sport == "run"]
    first = [s for s in runs if s.day < date(2026, 12, 7)]
    assert first and all("Walk" in s.dsl for s in first)            # starts with run-walk
    assert not any(s.kind == "quality" for s in first)
    assert all(s.minutes <= L.RUN[0].session_max + 5 for s in first)
    wk1 = [s for s in runs if s.day < date(2026, 11, 23)]
    assert len(wk1) <= 3                                             # no more than 3 run-walks a week to start
    later_longs = [s for s in runs if s.kind == "long" and s.day > date(2027, 5, 1)]
    assert later_longs and max(s.minutes for s in later_longs) >= 90   # builds to half-marathon long runs
    lv = plan.projection["levels"]["run"]
    assert lv["required"] == 5 and lv["on_track"] and lv["weeks_to_required"] <= 34


def test_unassessed_sports_get_a_level_check_in_week_one():
    goal = P.Goal("sprint_tri", race_date=date(2027, 6, 1), start_date=date(2026, 11, 16), level="beginner",
                  availability={i: 90 for i in range(7)})
    plan = P.generate(goal)
    tests = [s for s in plan.sessions if s.kind == "test"]
    assert {s.sport for s in tests} == {"run", "swim", "bike"}
    assert all(s.day < date(2026, 11, 23) for s in tests)
    for t in tests:                                                   # each check comes before that sport's other sessions
        others = [s for s in plan.sessions if s.sport == t.sport and s.kind != "test" and s.day < date(2026, 11, 23)]
        assert all(t.day <= o.day for o in others)
    assert all(not v["assessed"] for k, v in plan.projection["levels"].items() if k != "gym")


def test_off_track_goal_is_flagged():
    goal = P.Goal("marathon", race_date=date(2027, 2, 1), start_date=date(2026, 11, 16), level="beginner",
                  levels={"run": {"stage": 0, "assessed": True}})
    lv = P.generate(goal).projection["levels"]["run"]
    assert not lv["on_track"] and lv["weeks_to_required"] > 11
