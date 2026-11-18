"""
Workout prescription DSL.

A tiny language for writing structured multi-sport sessions in plain text and
compiling them into a tree that the UI renders as interactive workout cards and
the load model turns into a Training Stress Score.

Grammar (informal)
------------------
    workout   := SPORT ':' body
    body      := segment ('->' segment)*                 # endurance sports
               | block ('|' block)*                      # gym
    segment   := INT 'x' '(' segment (',' segment)* ')'  # grouped repeat
               | INT 'x' step                            # shorthand repeat: 8x100m ...
               | step
    step      := [KEYWORD] QUANTITY [label] ['@' target] ['(' INT 's' 'rest' ')']
    target    := 'Z'1-5 | mm:ss'/km' | mm:ss'/100m' | INT'W' | INT'%FTP' | 'RPE' NUM
    block     := ['Superset:'] exercise ('+' exercise)*
    exercise  := NAME INT 'x' INT ['@' ('RPE' NUM | NUM 'kg'|'lb')]

Units: s, sec, min, h, m, km, mi.  A bare ``m`` means *meters* for swims and
for values >= 100; otherwise (e.g. ``Warmup 15m`` on a run) it means minutes.
Durations may also be written ``1:30:00`` or ``45:00``.

Examples
--------
    Run: Warmup 15m @ Z2 -> 5x(1km @ 4:15/km, Rest 90s) -> Cooldown 10m
    Swim: 400m Easy -> 8x100m Freestyle @ 1:45/100m (20s rest) -> 200m Pull Buoy
    Bike: Warmup 15min @ Z2 -> 3x(12min @ 90%FTP, Rest 5min) -> Cooldown 10min
    Gym: Back Squat 4x6 @ RPE 8 | Superset: Romanian Deadlift 3x10 + Hanging Leg Raises 3x15
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional, Union

SPORTS = {"run", "bike", "swim", "gym", "walk", "mobility"}
SET_BASED = {"gym", "mobility"}     # written as exercises (sets x reps), not as timed steps
SPORT_ALIASES = {
    "running": "run", "ride": "bike", "cycling": "bike", "cycle": "bike",
    "swimming": "swim", "strength": "gym", "weights": "gym", "lift": "gym",
    "walking": "walk", "hike": "walk", "hiking": "walk",
    "yoga": "mobility", "stretch": "mobility", "stretching": "mobility", "mobilité": "mobility",
}

KEYWORDS = {
    "warmup": "warmup", "warm-up": "warmup", "wu": "warmup",
    "cooldown": "cooldown", "cool-down": "cooldown", "cd": "cooldown",
    "rest": "rest", "recovery": "easy",
    "easy": "easy", "long": "long", "race": "race", "tempo": "work",
    "steady": "work",
}


class DSLError(ValueError):
    """Raised with a human-readable message pointing at the bad fragment."""


# --------------------------------------------------------------------------- #
# Tree nodes
# --------------------------------------------------------------------------- #
@dataclass
class Target:
    kind: str                 # zone | pace | power | ftp_pct | rpe
    value: float              # zone number, sec per unit, watts, pct, rpe
    unit: Optional[str] = None  # 'km' or '100m' for pace

    def label(self) -> str:
        if self.kind == "zone":
            return f"Z{int(self.value)}"
        if self.kind == "pace":
            m, s = divmod(int(round(self.value)), 60)
            return f"{m}:{s:02d}/{self.unit}"
        if self.kind == "power":
            return f"{int(self.value)}W"
        if self.kind == "ftp_pct":
            return f"{int(self.value)}%FTP"
        return f"RPE {self.value:g}"


@dataclass
class Step:
    type: str = "step"
    role: str = "work"                  # warmup | cooldown | rest | easy | work | long | race
    label: str = ""
    duration_s: Optional[float] = None
    distance_m: Optional[float] = None
    target: Optional[Target] = None


@dataclass
class Repeat:
    count: int
    steps: list
    type: str = "repeat"


@dataclass
class Exercise:
    name: str
    sets: int
    reps: int
    rpe: Optional[float] = None
    hold_s: Optional[int] = None        # timed holds: "Side Plank 3x30s" → 3 sets of 30 s
    load_kg: Optional[float] = None
    type: str = "exercise"


@dataclass
class Superset:
    exercises: list
    type: str = "superset"


Node = Union[Step, Repeat, Exercise, Superset]


@dataclass
class Workout:
    sport: str
    steps: list = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _split_top(text: str, sep: str) -> list[str]:
    """Split on ``sep`` only when not inside parentheses."""
    parts, depth, buf, i = [], 0, [], 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise DSLError(f"Unbalanced ')' in: {text!r}")
        if depth == 0 and text.startswith(sep, i):
            parts.append("".join(buf).strip())
            buf = []
            i += len(sep)
            continue
        buf.append(ch)
        i += 1
    if depth != 0:
        raise DSLError(f"Unbalanced '(' in: {text!r}")
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


_CLOCK = re.compile(r"^(\d+):(\d{2})(?::(\d{2}))?$")
_QTY = re.compile(r"^(\d+(?:\.\d+)?)\s*(km|mi|min|mins|sec|s|h|hr|m|')?$", re.I)


def _parse_quantity(tok: str, sport: str) -> tuple[Optional[float], Optional[float]]:
    """Return (duration_s, distance_m) for a single token, or (None, None)."""
    tok = tok.strip()
    m = _CLOCK.match(tok)
    if m:
        a, b, c = m.groups()
        if c is not None:
            return int(a) * 3600 + int(b) * 60 + int(c), None
        return int(a) * 60 + int(b), None
    m = _QTY.match(tok)
    if not m:
        return None, None
    val, unit = float(m.group(1)), (m.group(2) or "").lower()
    if unit in ("km",):
        return None, val * 1000
    if unit == "mi":
        return None, val * 1609.34
    if unit in ("min", "mins", "'"):
        return val * 60, None
    if unit in ("s", "sec"):
        return val, None
    if unit in ("h", "hr"):
        return val * 3600, None
    if unit == "m":
        if sport == "swim" or val >= 100:
            return None, val
        return val * 60, None
    if unit == "" and sport == "swim":
        return None, val
    return None, None


_TARGET_PATTERNS = [
    (re.compile(r"^z([1-5])$", re.I), lambda m: Target("zone", int(m.group(1)))),
    (re.compile(r"^(\d+):(\d{2})\s*/\s*(km|100m|mi)$", re.I),
     lambda m: Target("pace", int(m.group(1)) * 60 + int(m.group(2)), m.group(3).lower())),
    (re.compile(r"^(\d+)\s*%\s*ftp$", re.I), lambda m: Target("ftp_pct", int(m.group(1)))),
    (re.compile(r"^(\d+)\s*w$", re.I), lambda m: Target("power", int(m.group(1)))),
    (re.compile(r"^rpe\s*(\d+(?:\.\d+)?)$", re.I), lambda m: Target("rpe", float(m.group(1)))),
]


def _parse_target(text: str) -> Target:
    t = text.strip()
    for pat, build in _TARGET_PATTERNS:
        m = pat.match(t)
        if m:
            return build(m)
    raise DSLError(f"Unknown target '@ {t}'. Use Z1-Z5, 4:15/km, 1:45/100m, 250W, 90%FTP or RPE 7.")


_REST_SUFFIX = re.compile(r"\((\d+(?:\.\d+)?\s*(?:s|sec|min|m))\s*rest\)\s*$", re.I)
_GROUP_REPEAT = re.compile(r"^(\d+)\s*x\s*\((.*)\)$", re.I | re.S)
_SHORT_REPEAT = re.compile(r"^(\d+)\s*x\s*(\d.*)$", re.I)


def _parse_step(text: str, sport: str) -> Step:
    original = text
    target = None
    if "@" in text:
        text, tgt = text.split("@", 1)
        target = _parse_target(tgt)
    words = text.split()
    if not words:
        raise DSLError(f"Empty step in: {original!r}")

    role = "work"
    duration = distance = None
    label_words = []
    for w in words:
        key = w.lower().rstrip(",")
        if key in KEYWORDS and role == "work":
            role = KEYWORDS[key]
            label_words.append(w)
            continue
        if duration is None and distance is None:
            d, dist = _parse_quantity(w, sport)
            if d is not None or dist is not None:
                duration, distance = d, dist
                continue
        label_words.append(w)

    if duration is None and distance is None:
        raise DSLError(f"Step needs a duration or distance (e.g. 10min, 1km, 400m): {original!r}")
    label = " ".join(label_words)
    # 'Easy' as a label implies an easy role even if not first
    if role == "work" and re.search(r"\beasy\b", label, re.I):
        role = "easy"
    return Step(role=role, label=label, duration_s=duration, distance_m=distance, target=target)


def _parse_segment(text: str, sport: str) -> Node:
    text = text.strip()
    m = _GROUP_REPEAT.match(text)
    if m:
        count = int(m.group(1))
        inner = [_parse_segment(p, sport) for p in _split_top(m.group(2), ",")]
        return Repeat(count=count, steps=inner)

    rest = None
    rm = _REST_SUFFIX.search(text)
    if rm:
        rd, _ = _parse_quantity(rm.group(1).replace(" ", ""), "run")
        rest = Step(role="rest", label="Rest", duration_s=rd)
        text = text[: rm.start()].strip()

    m = _SHORT_REPEAT.match(text)
    if m:
        count = int(m.group(1))
        body = _parse_step(m.group(2), sport)
        steps = [body] + ([rest] if rest else [])
        return Repeat(count=count, steps=steps)

    step = _parse_step(text, sport)
    if rest:
        return Repeat(count=1, steps=[step, rest])
    return step


_EXERCISE = re.compile(
    r"^(?P<name>.+?)\s+(?P<sets>\d+)\s*x\s*(?P<reps>\d+)\s*(?P<hold>s|sec)?(?:\s*(?:/|per)\s*side)?"
    r"(?:\s*@\s*(?:(?:rpe\s*(?P<rpe>\d+(?:\.\d+)?))|(?:(?P<load>\d+(?:\.\d+)?)\s*(?P<lu>kg|lb))))?\s*$",
    re.I,
)


def _parse_exercise(text: str) -> Exercise:
    m = _EXERCISE.match(text.strip())
    if not m:
        raise DSLError(f"Exercise should look like 'Back Squat 4x6 @ RPE 8' or 'Side Plank 3x30s': {text!r}")
    load = None
    if m.group("load"):
        load = float(m.group("load"))
        if m.group("lu").lower() == "lb":
            load = round(load * 0.4536, 1)
    return Exercise(
        name=m.group("name").strip(),
        sets=int(m.group("sets")),
        reps=1 if m.group("hold") else int(m.group("reps")),
        hold_s=int(m.group("reps")) if m.group("hold") else None,
        rpe=float(m.group("rpe")) if m.group("rpe") else None,
        load_kg=load,
    )


def _parse_gym(body: str) -> list:
    nodes = []
    for block in _split_top(body, "|"):
        is_superset = False
        if block.lower().startswith("superset:"):
            is_superset = True
            block = block.split(":", 1)[1]
        parts = [p.strip() for p in block.split("+") if p.strip()]
        exercises = [_parse_exercise(p) for p in parts]
        if is_superset or len(exercises) > 1:
            nodes.append(Superset(exercises=exercises))
        else:
            nodes.append(exercises[0])
    return nodes


def parse(text: str) -> Workout:
    """Parse a DSL string into a :class:`Workout`. Raises :class:`DSLError`."""
    if ":" not in text:
        raise DSLError("Start with a sport, e.g. 'Run: ...', 'Swim: ...', 'Bike: ...', 'Gym: ...'")
    head, body = text.split(":", 1)
    sport = head.strip().lower()
    sport = SPORT_ALIASES.get(sport, sport)
    if sport not in SPORTS:
        raise DSLError(f"Unknown sport '{head.strip()}'. Use Run, Bike, Swim, Walk, Gym or Mobility.")
    body = body.strip()
    if not body:
        raise DSLError("Workout body is empty.")
    if sport in SET_BASED:
        steps = _parse_gym(body)
    else:
        steps = [_parse_segment(seg, sport) for seg in _split_top(body, "->")]
    return Workout(sport=sport, steps=steps, source=text.strip())


# --------------------------------------------------------------------------- #
# Flattening (used by stress model, ICS export and UI)
# --------------------------------------------------------------------------- #
def flatten(nodes: list) -> list[Step]:
    """Expand repeats into a linear list of steps."""
    out: list[Step] = []
    for n in nodes:
        if isinstance(n, Repeat):
            for _ in range(n.count):
                out.extend(flatten(n.steps))
        elif isinstance(n, Step):
            out.append(n)
    return out


def describe(workout: Workout) -> list[str]:
    """Human-readable lines for notifications / calendar descriptions."""
    lines = []

    def step_txt(s: Step) -> str:
        q = ""
        if s.distance_m:
            q = f"{s.distance_m / 1000:g} km" if s.distance_m >= 1000 else f"{s.distance_m:g} m"
        elif s.duration_s:
            q = f"{s.duration_s / 60:g} min" if s.duration_s >= 60 else f"{s.duration_s:g} s"
        t = f" @ {s.target.label()}" if s.target else ""
        lbl = f" {s.label}" if s.label else ""
        return f"{q}{lbl}{t}".strip()

    for n in workout.steps:
        if isinstance(n, Repeat):
            inner = ", ".join(step_txt(s) for s in n.steps if isinstance(s, Step))
            lines.append(f"{n.count}× ({inner})")
        elif isinstance(n, Step):
            lines.append(step_txt(n))
        elif isinstance(n, Exercise):
            extra = f" @ RPE {n.rpe:g}" if n.rpe else (f" @ {n.load_kg:g} kg" if n.load_kg else "")
            lines.append(f"{n.name} {n.sets}×{n.reps}{extra}")
        elif isinstance(n, Superset):
            lines.append("Superset: " + " + ".join(f"{e.name} {e.sets}×{e.reps}" for e in n.exercises))
    return lines
