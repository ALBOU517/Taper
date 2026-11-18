from datetime import date, timedelta

import pytest

from app.engine import nutrition as N

BODY = N.Body(sex="male", age=24, height_cm=178, weight_kg=80, neat="moderate")


def test_mifflin_st_jeor_matches_published_equation():
    # 10·80 + 6.25·178 − 5·24 + 5
    assert N.bmr(BODY) == pytest.approx(800 + 1112.5 - 120 + 5)
    female = N.Body("female", 30, 165, 60)
    assert N.bmr(female) == pytest.approx(600 + 1031.25 - 150 - 161)


def test_running_costs_about_one_kcal_per_kg_per_km():
    s = N.SessionEnergy("run", minutes=30, distance_m=4500)
    assert N.session_kcal(s, 80) == pytest.approx(80 * 4.5, rel=0.01)


def test_cycling_energy_from_power():
    s = N.SessionEnergy("bike", minutes=60, avg_power=200)
    # 720 kJ → 720/4.184/0.24 ≈ 717 kcal gross, minus ~80 kcal resting
    assert N.session_kcal(s, 80) == pytest.approx(717 - 80, abs=5)


def test_gym_uses_compendium_codes():
    typical = N.session_kcal(N.SessionEnergy("gym", 60, rpe=8), 80)            # 02054
    heavy = N.session_kcal(N.SessionEnergy("gym", 60, heavy=True), 80)         # 02052
    vigorous = N.session_kcal(N.SessionEnergy("gym", 60, rpe=9), 80)           # 02050
    assert typical == pytest.approx((3.5 - 1) * 80)
    assert heavy == pytest.approx((5.0 - 1) * 80)
    assert vigorous == pytest.approx((6.0 - 1) * 80)


def _week(sessions: dict[int, list[N.SessionEnergy]]):
    mon = date(2026, 11, 16)
    days = [mon + timedelta(i) for i in range(7)]
    return days, {days[i]: v for i, v in sessions.items()}


def test_weekly_deficit_matches_goal_rate_and_spares_hard_days():
    days, by_day = _week({2: [N.SessionEnergy("run", 30, 4500)], 5: [N.SessionEnergy("run", 110, 18000, kind="long")]})
    goal = N.Goal("lose", rate_pct_per_week=-0.5)
    t = N.week_targets(days, by_day, BODY, goal)
    total_adj = sum(x.goal_adjust_kcal for x in t)
    assert total_adj == pytest.approx(-0.005 * 80 * 7700, abs=5)          # ≈ −3080 kcal / week
    rest, long_day = t[0], t[5]
    assert rest.goal_adjust_kcal < long_day.goal_adjust_kcal < 0          # less deficit on the long-run day
    assert long_day.kcal > t[2].kcal > rest.kcal
    assert long_day.training_kcal == pytest.approx(80 * 18, abs=2)


def test_macros_protein_fixed_carbs_follow_load():
    days, by_day = _week({1: [N.SessionEnergy("run", 150, 25000, kind="long")]})
    t = N.week_targets(days, by_day, BODY, N.Goal("maintain", protein_g_per_kg=2.25))
    assert all(x.protein_g == 180 for x in t)
    assert t[1].load == "high" and t[0].load == "rest"
    assert t[1].carbs_g > t[0].carbs_g
    for x in t:                                                           # macros add up to the kcal target
        assert abs(4 * x.protein_g + 4 * x.carbs_g + 9 * x.fat_g - x.kcal) <= 10
        assert x.fat_g >= 0.6 * 80 - 1


def test_never_below_resting_plus_training():
    tiny = N.Body("female", 40, 150, 45, "low")
    days, by_day = _week({})
    t = N.week_targets(days, by_day, tiny, N.Goal("lose", rate_pct_per_week=-1.0))
    assert all(x.kcal >= N.bmr(tiny) - 10 for x in t)


def test_carb_loading_before_long_race():
    days, by_day = _week({6: [N.SessionEnergy("run", 120, 21100, kind="race")]})
    t = N.week_targets(days, by_day, BODY, N.Goal("maintain"), race_day=days[6])
    assert t[5].carbs_g >= 10 * 80 * 0.95 and t[4].carbs_g >= 10 * 80 * 0.95
    assert t[3].carbs_g < 10 * 80


def test_fuel_plan_follows_jeukendrup_bands():
    short = N.fuel_plan(N.SessionEnergy("run", 30, 4500, start="17:00"), 80)
    assert not any(s.get("dose") for s in short)
    long = N.fuel_plan(N.SessionEnergy("run", 140, 22000, start="08:00"), 80)
    during = next(s for s in long if s["phase"] == "during" and s.get("per_hour"))
    assert during["carbs_g"] == 60
    doses = [s for s in long if s.get("dose")]
    assert doses and doses[0]["at"] == "08:20" and doses[0]["carbs_g"] == 20
    assert sum(d["carbs_g"] for d in doses) <= 60 * 140 / 60 + 20
    gym = N.fuel_plan(N.SessionEnergy("gym", 90, rpe=8), 80)
    assert any(s["protein_g"] == 25 for s in gym)


def test_back_to_back_sessions_trigger_rapid_refuel():
    plan = N.fuel_plan(N.SessionEnergy("bike", 90, intensity=0.75), 80, hours_to_next_session=5)
    after = next(s for s in plan if s["phase"] == "after")
    assert after["carbs_g"] == 90


def test_weight_trend_smooths_noise():
    d0 = date(2026, 1, 1)
    pts = [(d0 + timedelta(i), 80 + (1.5 if i % 2 else -1.5)) for i in range(30)]
    tr = N.weight_trend(pts)
    vals = list(tr.values())[10:]
    assert max(vals) - min(vals) < 1.0


def test_adaptive_base_recovers_true_expenditure():
    """Simulated athlete: true base 2400, trains 400 kcal/day, eats 2500 → loses 300 kcal/day."""
    d0 = date(2026, 1, 1)
    recs = []
    w = 82.0
    for i in range(35):
        w -= 300 / N.KCAL_PER_KG
        recs.append(N.DayRecord(d0 + timedelta(i), intake_kcal=2500, weight_kg=round(w + (0.4 if i % 3 == 0 else -0.2), 1),
                                exercise_kcal=400, complete=True))
    est = N.adaptive_base(recs, prior=2000)
    assert est["observed"] == pytest.approx(2400, abs=150)
    assert est["base"] > 2200 and est["confidence"] > 0.8


def test_adaptive_base_waits_for_enough_data():
    recs = [N.DayRecord(date(2026, 1, i + 1), 2500, 80, 0, True) for i in range(5)]
    assert N.adaptive_base(recs, prior=2100)["base"] == 2100
