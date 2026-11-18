"""
Session template library.

Each template turns (phase, minutes) into a DSL string. Templates use heart-rate
zones / %FTP so a plan is portable across athletes; the load model resolves them
to real paces through the athlete's thresholds.
"""
from __future__ import annotations

from .load import Thresholds


def _r(x: float, step: int = 5) -> int:
    return max(step, int(round(x / step) * step))


def _swim_m(minutes: float, th: Thresholds, intensity: float = 0.75) -> int:
    """Approximate swim distance for a time budget (incl. ~15% wall rest)."""
    per100 = th.swim_css / intensity
    return max(400, int(round(minutes * 60 * 0.85 / per100 * 100 / 100)) * 100)


# ---------------------------------------------------------------- run
def run(kind: str, phase: str, minutes: float, th: Thresholds) -> tuple[str, str]:
    m = _r(minutes)
    if kind == "recovery":
        return "Recovery run", f"Run: Recovery {m}min @ Z1"
    if kind == "easy":
        if phase in ("base", "build") and m >= 40:
            return "Easy run + strides", f"Run: Easy {m - 5}min @ Z2 -> 5x(20s @ Z5, Rest 40s)"
        return "Easy run", f"Run: Easy {m}min @ Z2"
    if kind == "long":
        if phase == "base" or m < 60:
            return "Long run", f"Run: Long {m}min @ Z2"
        if phase == "build":
            ff = _r(m * 0.2)
            return "Long run, fast finish", f"Run: Long {m - ff}min @ Z2 -> {ff}min @ Z3"
        blocks = max(2, min(4, int((m - 30) // 20)))
        rest = _r(m - 15 - blocks * 15 - (blocks - 1) * 3)
        return "Long run w/ race-pace blocks", (
            f"Run: Warmup 15min @ Z2 -> {blocks}x(15min @ Z3, Rest 3min) -> Long {max(rest, 10)}min @ Z2")
    if kind == "quality":
        if phase == "base":
            reps = max(4, min(8, int((m - 25) // 3)))
            return "Hill sprints", f"Run: Warmup 15min @ Z2 -> {reps}x(1min @ Z5, Rest 2min) -> Cooldown 10min @ Z1"
        if phase == "build":
            reps = max(2, min(5, int((m - 25) // 10)))
            return "Threshold intervals", f"Run: Warmup 15min @ Z2 -> {reps}x(8min @ Z4, Rest 2min) -> Cooldown 10min @ Z1"
        if phase == "build2" or phase == "peak":
            reps = max(4, min(8, int((m - 25) // 6)))
            return "VO2max kilometres", f"Run: Warmup 15min @ Z2 -> {reps}x(1km @ Z5, Rest 2min) -> Cooldown 10min @ Z1"
        # taper / race week — short sharpening
        return "Race-pace sharpener", "Run: Warmup 15min @ Z2 -> 4x(3min @ Z4, Rest 2min) -> Cooldown 10min @ Z1"
    raise ValueError(kind)


# ---------------------------------------------------------------- bike
def bike(kind: str, phase: str, minutes: float, th: Thresholds) -> tuple[str, str]:
    m = _r(minutes)
    if kind in ("easy", "recovery"):
        z = "Z1" if kind == "recovery" else "Z2"
        return ("Recovery spin" if kind == "recovery" else "Endurance ride"), f"Bike: Endurance {m}min @ {z}"
    if kind == "long":
        if phase in ("build", "peak", "build2") and m >= 90:
            blocks = 3 if m < 180 else 4
            easy = _r(m - 20 - blocks * 15 - (blocks - 1) * 5)
            return "Long ride w/ tempo", (
                f"Bike: Warmup 20min @ Z2 -> {blocks}x(15min @ 80%FTP, Rest 5min) -> Long {max(easy, 15)}min @ Z2")
        return "Long ride", f"Bike: Long {m}min @ Z2"
    if kind == "quality":
        if phase == "base":
            reps = max(2, min(4, int((m - 25) // 15)))
            return "Sweet-spot", f"Bike: Warmup 15min @ Z2 -> {reps}x(10min @ 88%FTP, Rest 5min) -> Cooldown 10min @ Z1"
        if phase == "build":
            reps = max(2, min(4, int((m - 25) // 17)))
            return "Threshold", f"Bike: Warmup 15min @ Z2 -> {reps}x(12min @ 97%FTP, Rest 5min) -> Cooldown 10min @ Z1"
        if phase in ("peak", "build2"):
            reps = max(4, min(6, int((m - 25) // 8)))
            return "VO2max", f"Bike: Warmup 15min @ Z2 -> {reps}x(4min @ 115%FTP, Rest 4min) -> Cooldown 10min @ Z1"
        return "Openers", "Bike: Warmup 15min @ Z2 -> 3x(2min @ 100%FTP, Rest 3min) -> Cooldown 10min @ Z1"
    raise ValueError(kind)


# ---------------------------------------------------------------- swim
def swim(kind: str, phase: str, minutes: float, th: Thresholds) -> tuple[str, str]:
    total = _swim_m(minutes, th)
    if kind in ("easy", "recovery"):
        main = max(200, total - 300 - 300 - 200)
        return "Technique swim", f"Swim: 300m Easy -> 6x50m Drill @ Z2 (20s rest) -> {main}m Aerobic @ Z2 -> 200m Easy"
    if kind == "long":
        n = max(2, (total - 500) // 400)
        return "Endurance swim", f"Swim: 300m Easy -> {n}x400m @ Z3 (30s rest) -> 200m Easy"
    if kind == "quality":
        if phase in ("taper", "race"):
            return "Race-pace swim", "Swim: 400m Easy -> 6x100m @ Z4 (20s rest) -> 200m Easy"
        n = max(6, min(20, (total - 600) // 100))
        z = "Z4" if phase in ("build", "peak", "build2") else "Z3"
        return "CSS intervals", f"Swim: 400m Easy -> {n}x100m @ {z} (15s rest) -> 200m Easy Pull"
    raise ValueError(kind)


# ---------------------------------------------------------------- gym
GYM = {
    "base": ("Strength — foundation",
             "Gym: Back Squat 3x8 @ RPE 7 | Romanian Deadlift 3x8 @ RPE 7 | "
             "Superset: Bulgarian Split Squat 3x10 + Pull-ups 3x8 | Superset: Calf Raises 3x15 + Hanging Leg Raises 3x12"),
    "build": ("Strength — max",
              "Gym: Back Squat 4x5 @ RPE 8 | Romanian Deadlift 3x6 @ RPE 8 | "
              "Superset: Step-ups 3x8 + Pull-ups 3x6 | Hanging Leg Raises 3x15"),
    "peak": ("Strength — maintenance",
             "Gym: Back Squat 2x5 @ RPE 7 | Superset: Single-Leg RDL 2x8 + Copenhagen Plank 2x8"),
    "taper": ("Mobility & activation",
              "Gym: Goblet Squat 2x8 @ RPE 5 | Superset: Glute Bridge 2x12 + Dead Bug 2x10"),
}
GYM_UPPER = ("Strength — upper body",
             "Gym: Bench Press 4x6 @ RPE 8 | Superset: Barbell Row 4x8 + Overhead Press 3x8 | Superset: Face Pulls 3x15 + Ab Wheel 3x10")


def gym(kind: str, phase: str, minutes: float, th: Thresholds, variant: int = 0) -> tuple[str, str]:
    if variant % 2 == 1 and phase in ("base", "build"):
        return GYM_UPPER
    key = {"build2": "build", "race": "taper", "recovery": "peak"}.get(phase, phase)
    return GYM.get(key, GYM["base"])


# ---------------------------------------------------------------- walk (low-impact cardio)
def walk(kind: str, phase: str, minutes: float, th: Thresholds) -> tuple[str, str]:
    m = _r(minutes)
    if kind == "long":
        return "Long walk or hike", f"Walk: Hike {m}min @ Z2"
    if kind == "quality":
        return "Incline walk", f"Walk: 5min @ Z1 -> Incline {max(10, m - 10)}min @ Z3 -> 5min @ Z1"
    return "Brisk walk", f"Walk: Brisk {m}min @ Z2"


# ---------------------------------------------------------------- mobility (low-impact, no equipment)
# Three rotating focuses. "Easy" versions in base weeks, a harder variant of each exercise later.
# Contents follow common physio / ACSM guidance for older and deconditioned adults: spinal and hip
# mobility, single-leg balance, and trunk + glute endurance. None of it loads the spine heavily.
MOBILITY = [
    ("Mobility — spine & hips", [
        ("Cat-Cow 2x10", "Cat-Cow 2x12"), ("Thoracic Rotation 2x8", "Thread the Needle 2x8"),
        ("Hip Flexor Stretch 2x40s", "Hip Flexor Stretch 2x60s"), ("90/90 Hip Switch 2x8", "90/90 Hip Switch 3x8"),
        ("Child's Pose 2x40s", "World's Greatest Stretch 2x5"), ("Glute Bridge 2x12", "Glute Bridge 3x12"),
        ("Hamstring Floss 2x10", "Jefferson Curl, bodyweight 2x8")]),
    ("Mobility — balance & stability", [
        ("Single-Leg Stand 3x20s", "Single-Leg Stand, Eyes Closed 3x20s"), ("Sit-to-Stand 3x8", "Sit-to-Stand 3x12"),
        ("Heel-to-Toe Walk 3x10", "Heel-to-Toe Walk Backwards 3x10"), ("Side-Lying Leg Raise 2x12", "Standing Hip Abduction 3x12"),
        ("Calf Raises 2x12", "Single-Leg Calf Raises 2x10"), ("Bird Dog 2x8", "Bird Dog 3x10"),
        ("Step-Back Lunge, supported 2x8", "Step-Back Lunge 2x10")]),
    ("Mobility — core & glutes", [
        ("Dead Bug 2x8", "Dead Bug 3x10"), ("Glute Bridge 2x12", "Single-Leg Glute Bridge 2x10"),
        ("Bird Dog 2x8", "Bird Dog 3x10"), ("Clamshell 2x12", "Clamshell 3x15"), ("Side Plank, knees 2x20s", "Side Plank 2x30s"),
        ("Cat-Cow 2x10", "Cat-Cow 2x12"), ("Bodyweight Squat 2x10", "Bodyweight Squat 3x12")]),
]


def _item_s(text: str) -> float:
    import re
    m = re.search(r"(\d+)x(\d+)(s)?", text)
    sets, n, hold = int(m.group(1)), int(m.group(2)), m.group(3)
    return sets * ((n if hold else n * 4) + 20)


def mobility(kind: str, phase: str, minutes: float, th: Thresholds, variant: int = 0) -> tuple[str, str]:
    title, pool = MOBILITY[variant % len(MOBILITY)]
    hard = phase in ("build", "build2", "peak")
    items, used = [], 0.0
    for easy, harder in pool:
        it = harder if hard else easy
        if items and used + _item_s(it) > minutes * 60 * 1.05:
            break
        items.append(it)
        used += _item_s(it)
    import re
    i = 0
    while used < minutes * 60 * 0.85 and i < 3 * len(items):     # longer windows: one more round of each
        k = i % len(items)
        sets = int(re.match(r".*?(\d+)x", items[k]).group(1))
        if sets < 4:
            per = _item_s(items[k]) / sets
            items[k] = re.sub(r"(\d+)x", f"{sets + 1}x", items[k], count=1)
            used += per
        i += 1
    return title, "Mobility: " + " | ".join(items)


def mobility_variant(title: str) -> int:
    return next((i for i, (t, _) in enumerate(MOBILITY) if t == title), 0)


BUILDERS = {"run": run, "bike": bike, "swim": swim, "walk": walk}


def build(sport: str, kind: str, phase: str, minutes: float, th: Thresholds, variant: int = 0) -> tuple[str, str]:
    if sport == "gym":
        return gym(kind, phase, minutes, th, variant)
    if sport == "mobility":
        return mobility(kind, phase, minutes, th, variant)
    return BUILDERS[sport](kind, phase, minutes, th)


# ---------------------------------------------------------------- equipment
EQUIPMENT = ("gym", "home", "none")      # full gym · dumbbells + bands at home · nothing (hotel room, travel)
# exercise -> (home version, no-equipment version). Effort is kept through RPE: with lighter tools
# you slow the tempo or add reps until the set feels like the prescribed RPE.
SWAPS = {
    "Back Squat": ("Goblet Squat", "Split Squat"), "Pause Squat": ("Pause Goblet Squat", "Pause Split Squat"),
    "Front Squat": ("Goblet Squat", "Split Squat"), "Hack Squat": ("Goblet Squat", "Split Squat"),
    "Leg Press": ("Goblet Squat", "Split Squat"), "Single-Leg Leg Press": ("Step-ups", "Step-ups"),
    "Goblet Squat": ("Goblet Squat", "Bodyweight Squat"),
    "Romanian Deadlift": ("Dumbbell Romanian Deadlift", "Single-Leg Romanian Deadlift"),
    "Deficit Deadlift": ("Dumbbell Romanian Deadlift", "Single-Leg Romanian Deadlift"),
    "Deadlift": ("Dumbbell Romanian Deadlift", "Single-Leg Romanian Deadlift"),
    "Leg Curl": ("Swiss-Ball Hamstring Curl", "Slider Hamstring Curl"),
    "Hamstring Curl": ("Swiss-Ball Hamstring Curl", "Slider Hamstring Curl"),
    "Leg Extension": ("Band Leg Extension", "Spanish Squat"),
    "Eccentric Leg Extension": ("Band Leg Extension", "Spanish Squat"),
    "Bench Press": ("Dumbbell Floor Press", "Push-ups"), "Close-Grip Bench": ("Close-Grip Dumbbell Floor Press", "Diamond Push-ups"),
    "Dumbbell Bench Press": ("Dumbbell Floor Press", "Push-ups"), "Chest Press": ("Dumbbell Floor Press", "Push-ups"),
    "Overhead Press": ("Dumbbell Shoulder Press", "Pike Push-ups"), "Shoulder Press": ("Dumbbell Shoulder Press", "Pike Push-ups"),
    "Barbell Row": ("Dumbbell Row", "Backpack Row"), "Seated Cable Row": ("Band Row", "Backpack Row"),
    "Dumbbell Row": ("Dumbbell Row", "Backpack Row"),
    "Pull-ups": ("Band Pulldown", "Prone Y-T-W Raises"), "Lat Pulldown": ("Band Pulldown", "Prone Y-T-W Raises"),
    "Face Pulls": ("Band Face Pulls", "Prone Y-T-W Raises"), "Dips": ("Chair Dips", "Chair Dips"),
    "Hanging Leg Raises": ("Lying Leg Raises", "Lying Leg Raises"), "Ab Wheel": ("Body Saw", "Body Saw"),
    "Back Extension": ("Superman Hold", "Superman Hold"), "Calf Raises": ("Calf Raises", "Calf Raises"),
}
# home versions → no-equipment versions (a home session can still be swapped on the road)
for _home, _none in {"Dumbbell Romanian Deadlift": "Single-Leg Romanian Deadlift", "Dumbbell Floor Press": "Push-ups",
                     "Close-Grip Dumbbell Floor Press": "Diamond Push-ups", "Dumbbell Shoulder Press": "Pike Push-ups",
                     "Band Pulldown": "Prone Y-T-W Raises", "Band Row": "Backpack Row", "Band Face Pulls": "Prone Y-T-W Raises",
                     "Swiss-Ball Hamstring Curl": "Slider Hamstring Curl", "Band Leg Extension": "Spanish Squat",
                     "Pause Goblet Squat": "Pause Split Squat"}.items():
    SWAPS.setdefault(_home, (_home, _none))
_SWAP_RE = None


def for_equipment(text: str, equipment: str) -> str:
    """Rewrite a strength session for the equipment at hand ("gym" returns it unchanged)."""
    import re
    global _SWAP_RE
    if equipment not in ("home", "none") or not text.startswith("Gym:"):
        return text
    if _SWAP_RE is None:
        names = sorted(SWAPS, key=len, reverse=True)
        _SWAP_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in names) + r")(?=\s+\d+x)")
    k = 0 if equipment == "home" else 1
    out = _SWAP_RE.sub(lambda m: SWAPS[m.group(1)][k], text)
    if equipment == "none":
        out = re.sub(r"@\s*\d+(?:\.\d+)?\s*(?:kg|lb)", "@ RPE 8", out)
    return out


EQUIP_LABEL = {"home": "home", "none": "no equipment"}


# ---------------------------------------------------------------- fitting & deloads (text-level, so user routines work too)
def _exercises(text: str):
    import re
    head, body = text.split(":", 1)
    return head, [[e for e in block.split("+")] for block in body.split("|")], re


def fit_gym(text: str, minutes: float, th: Thresholds | None = None) -> str:
    """Trim a strength session to a time budget: fewer sets first (never below 2), then drop the last blocks."""
    import re

    from . import dsl
    from .load import estimate

    def dur(t: str) -> float:
        return estimate(dsl.parse(t), th).duration_s / 60

    t = text
    for _ in range(40):
        if dur(t) <= minutes * 1.1:          # same 10 % grace the scheduler gives a day
            return t
        m = max(re.finditer(r"(?<=\s)(\d+)x(?=\d)", t), key=lambda x: int(x.group(1)), default=None)
        if m and int(m.group(1)) > 2:
            t = t[:m.start()] + str(int(m.group(1)) - 1) + t[m.end() - 1:]
            continue
        head, body = t.split(":", 1)
        blocks = [x.strip() for x in body.split("|")]
        singles = [i for i, x in enumerate(blocks) if "+" not in x and not x.lower().startswith("superset")]
        if len(singles) == 1 and len(blocks) >= 2:
            i = singles[0]                       # join the lone straight set to its neighbour's superset
            k = i + 1 if i + 1 < len(blocks) else i - 1
            lo, hi = sorted((i, k))
            merged = " + ".join(x.split(":", 1)[1].strip() if x.lower().startswith("superset") else x
                                for x in (blocks[lo], blocks[hi]))
            blocks[lo] = f"Superset: {merged}"
            del blocks[hi]
            t = head + ": " + " | ".join(blocks)
            continue
        if len(singles) >= 2:
            # pair two straight sets into a superset: same work, half the rest — keeps every exercise
            i, j = singles[-2], singles[-1]
            blocks[i] = f"Superset: {blocks[i]} + {blocks[j]}"
            del blocks[j]
            t = head + ": " + " | ".join(blocks)
            continue
        if len(blocks) <= 1:
            return t
        t = head + ":" + "|".join(blocks[:-1]).rstrip()
    return t


def deload(text: str) -> str:
    """Deload week for a strength session: same lifts, ~60 % of the sets, two RPE points easier."""
    import math
    import re
    t = re.sub(r"(?<=\s)(\d+)x(?=\d)", lambda m: f"{max(2, math.ceil(int(m.group(1)) * 0.6))}x", text)
    return re.sub(r"RPE\s*(\d+(?:\.\d+)?)", lambda m: f"RPE {max(5, float(m.group(1)) - 2):g}", t)
