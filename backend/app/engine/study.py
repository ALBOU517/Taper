"""
Study planner: turn deadlines (exams, assignments, quizzes) into revision blocks placed in the
free time of a real calendar.

Pure functions, no database. The service layer feeds it the busy blocks from the person's
calendar plus the training sessions Taper already placed, and stores what comes back.

How it decides
--------------
* Every deadline needs a number of hours (estimated from its weight if not given).
* Days are walked in order. Each free window is cut into blocks (default 90 min, minimum
  45 min, a 10-minute break between two blocks).
* Each block goes to the most urgent deadline still open after that block ends:
      urgency = weight x hours still missing / days left until it's due
  so a heavy exam that is close always wins, and a far one still gets steady time.
* The day before an exam, and the morning of one, that exam goes first.
* The same course doesn't get more than two blocks in a row (switching courses helps memory).
* A block always ends at least ``exam_gap_min`` before its exam starts, and before an
  assignment's due time.
* A daily cap keeps it sustainable, and a slot big enough for the day's training is kept free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

BREAK_MIN = 10


@dataclass
class Deadline:
    id: int
    course: str
    title: str
    kind: str                 # exam | assignment | quiz
    due: datetime             # aware, local time; start of the exam, or due time of an assignment
    hours: float              # hours still needed
    weight: float = 0.0       # % of the course grade


@dataclass
class Block:
    deadline_id: int
    start: datetime
    minutes: int

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)


@dataclass
class Interval:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class Options:
    block_min: int = 90
    min_block: int = 45
    max_per_day_min: int = 360
    exam_gap_min: int = 60            # finish revising this long before an exam starts
    final_review_min: int = 90        # extra time for a last look on the eve / morning of an exam
    keep_for_training: dict = field(default_factory=dict)   # date -> minutes to keep free in one piece


def default_hours(kind: str, weight: float) -> float:
    """A starting estimate. ~0.6 h of revision per % for an exam, 0.4 h per % for an assignment,
    never less than one hour. The person can always change it."""
    w = max(0.0, weight or 0.0)
    if kind == "exam":
        return round(min(30.0, max(2.0, 0.6 * w)), 1)
    if kind == "quiz":
        return round(min(4.0, max(1.0, 0.4 * w)), 1)
    return round(min(20.0, max(1.0, 0.4 * w)), 1)


def free_intervals(lo: datetime, hi: datetime, busy: Iterable[Interval]) -> list[Interval]:
    out, cur = [], lo
    for b in sorted(busy, key=lambda x: x.start):
        if b.end <= cur or b.start >= hi:
            continue
        if b.start > cur:
            out.append(Interval(cur, b.start))
        cur = max(cur, b.end)
    if cur < hi:
        out.append(Interval(cur, hi))
    return out


def _round_up(t: datetime, step: int = 5) -> datetime:
    t = t.replace(second=0, microsecond=0)
    r = t.minute % step
    return t + timedelta(minutes=step - r) if r else t


def plan(deadlines: list[Deadline], days: dict[date, tuple[datetime, datetime, list[Interval]]],
         opts: Optional[Options] = None, now: Optional[datetime] = None) -> list[Block]:
    """``days``: date -> (day start, day end, busy intervals that day). Returns the blocks in order."""
    opts = opts or Options()
    left = {d.id: max(0.0, d.hours) * 60 for d in deadlines}
    # a last look on the eve / morning of an exam, on top of its hours
    bonus = {d.id: float(opts.final_review_min) if d.kind == "exam" else 0.0 for d in deadlines}
    morning = {d.id: 120.0 if d.kind == "exam" else 0.0 for d in deadlines}   # warm-up on the day itself
    by_id = {d.id: d for d in deadlines}
    out: list[Block] = []

    free: dict[date, list[Interval]] = {}
    for day in sorted(days):
        lo, hi, busy = days[day]
        if now and hi <= now:
            continue
        if now and lo < now:
            lo = _round_up(now)
        free[day] = [w for w in free_intervals(lo, hi, busy) if w.minutes >= opts.min_block]

    def capacity(t: datetime, until: datetime, used_today: int) -> float:
        """Study minutes still available between t and until (daily cap and training slot respected)."""
        total = 0.0
        for day, ws in free.items():
            if day < t.date() or ws and ws[0].start >= until:
                continue
            m = sum(max(0, (min(w.end, until) - max(w.start, t)).total_seconds() // 60) for w in ws)
            cap = opts.max_per_day_min - (used_today if day == t.date() else 0)
            total += max(0, min(m - opts.keep_for_training.get(day, 0) * 0.5, cap))
        return total

    for day in sorted(free):
        windows = free[day]
        keep = opts.keep_for_training.get(day, 0)
        used, streak, last_course = 0, 0, None
        taken: list[Interval] = []
        for w in windows:
            t = _round_up(w.start)
            while True:
                room = int((w.end - t).total_seconds() // 60)
                cap = opts.max_per_day_min - used
                if room < opts.min_block or cap < opts.min_block:
                    break
                length = min(opts.block_min, room, cap)
                if 0 < room - length < opts.min_block + BREAK_MIN:
                    length = min(room, cap, 120)          # don't leave a crumb too small to use
                length -= length % 5
                if keep and not _training_fits(windows, taken, t, length, keep):
                    break
                pick = _pick(deadlines, left, bonus, morning, t, length, day, opts, last_course, streak,
                             lambda until, t=t, used=used: capacity(t, until, used))
                if pick is None and length > opts.min_block:
                    length = opts.min_block
                    pick = _pick(deadlines, left, bonus, morning, t, length, day, opts, last_course, streak,
                                 lambda until, t=t, used=used: capacity(t, until, used))
                if pick is None:
                    t += timedelta(minutes=30)
                    continue
                d = by_id[pick]
                on_day = d.kind == "exam" and d.due.date() == day and morning[pick] >= opts.min_block
                pool = morning if on_day else left if left[pick] > 0 else bonus
                length = int(min(length, max(opts.min_block, -(-pool[pick] // 15) * 15)))
                out.append(Block(pick, t, length))
                taken.append(Interval(t, t + timedelta(minutes=length)))
                pool[pick] -= length
                used += length
                streak = streak + 1 if d.course == last_course else 1
                last_course = d.course
                t = t + timedelta(minutes=length + BREAK_MIN)
    return out


def _training_fits(windows: list[Interval], taken: list[Interval], t: datetime, length: int, keep: int) -> bool:
    """Would the day still have one free stretch of ``keep`` minutes after this block?"""
    busy = taken + [Interval(t, t + timedelta(minutes=length))]
    return any(p.minutes >= keep for w in windows for p in free_intervals(w.start, w.end, busy))


def _pick(deadlines, left, bonus, morning, start: datetime, length: int, day: date, opts: Options,
          last_course: Optional[str], streak: int, capacity) -> Optional[int]:
    end = start + timedelta(minutes=length)

    def limit(d: Deadline) -> datetime:
        return d.due - timedelta(minutes=opts.exam_gap_min if d.kind == "exam" else 0)

    open_ = [d for d in deadlines if left[d.id] > 0 and end <= limit(d)]

    # 0) morning of an exam: that exam only
    # (a last look is fine until 15 minutes before, e.g. between two exams the same day)
    same_day = [d for d in deadlines if d.kind == "exam" and d.due.date() == day
                and end <= d.due - timedelta(minutes=min(15, opts.exam_gap_min)) and morning[d.id] >= opts.min_block]
    if same_day:
        return min(same_day, key=limit).id

    # 1) feasibility: if the work due by some date nearly fills the free time before it,
    #    the earliest of those deadlines goes first (earliest-deadline-first is optimal there)
    need = 0.0
    for d in sorted(open_, key=limit):
        need += left[d.id]
        if need >= 0.95 * capacity(limit(d)):
            first = min((x for x in open_ if limit(x) <= limit(d)), key=limit)
            return first.id

    # 2) a quiz or assignment due within two days: get it done (they're short and the mark is certain)
    soon = [d for d in open_ if d.kind != "exam" and (d.due - start) <= timedelta(hours=48)]
    if soon:
        return min(soon, key=limit).id

    # 3) eve and morning of an exam: that exam, including a short final review
    eve = [d for d in deadlines if d.kind == "exam" and end <= limit(d) and (d.due.date() - day).days <= 1
           and (left[d.id] > 0 or bonus[d.id] >= opts.min_block)]
    if eve:
        return min(eve, key=limit).id

    # 4) otherwise the most urgent: hours missing per day left, weighted by importance
    best, best_score = None, 0.0
    for d in open_:
        days_left = max(0.5, (limit(d) - start).total_seconds() / 86400)
        score = (max(d.weight, 5.0) ** 0.5) * (left[d.id] / 60) / days_left
        if d.course == last_course and streak >= 2:
            score *= 0.35                      # switch subject after two blocks in a row
        if score > best_score:
            best, best_score = d.id, score
    return best


def coverage(deadlines: list[Deadline], blocks: list[Block]) -> dict[int, float]:
    """Minutes planned per deadline."""
    out = {d.id: 0.0 for d in deadlines}
    for b in blocks:
        out[b.deadline_id] = out.get(b.deadline_id, 0) + b.minutes
    return out


def parse_lines(text: str, year: int) -> tuple[list[dict], list[str]]:
    """Bulk import, one deadline per line, fields separated by | or tabs:

        2026-12-11 18:30 | Intelligence artificielle | Examen final | 30
        Dec 13 13:30 | Algo & structures de données | Examen final | 35 | 20h

    Date (ISO or "Dec 13" / "13 déc"), optional time, course, title, optional weight %, optional hours.
    The kind is guessed from the title (examen/exam/final/intra -> exam, quiz/questionnaire -> quiz).
    """
    import re
    months = {"jan": 1, "janv": 1, "feb": 2, "fev": 2, "fév": 2, "févr": 2, "mar": 3, "mars": 3, "apr": 4, "avr": 4,
              "may": 5, "mai": 5, "jun": 6, "juin": 6, "jul": 7, "juil": 7, "aug": 8, "aou": 8, "aoû": 8, "sep": 9,
              "sept": 9, "oct": 10, "nov": 11, "dec": 12, "déc": 12}
    rows, errors = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"\s*[|\t]\s*", line) if p.strip()]
        if len(parts) < 3:
            errors.append(f"Line {n}: needs at least a date, a course and a title")
            continue
        when = parts[0].lower().replace(",", " ")
        d, rest = None, ""
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", when)
        if m:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            rest = when[m.end():]
        else:
            m = re.match(r"^(?:[a-zé]+\.?\s+)?(\d{1,2})\s+([a-zéû]+)\.?|^(?:[a-zé]+\.?\s+)?([a-zéû]+)\.?\s+(\d{1,2})\b", when)
            if m:
                day_s, mon_s = (m[1], m[2]) if m[1] else (m[4], m[3])
                mon = months.get(mon_s[:4]) or months.get(mon_s[:3])
                if mon:
                    d = date(year, mon, int(day_s))
                rest = when[m.end():]
        if not d:
            errors.append(f"Line {n}: couldn't read the date '{parts[0]}'")
            continue
        hm = re.search(r"(\d{1,2})\s*[h:]\s*(\d{2})?", rest)
        time_s = f"{int(hm[1]):02d}:{int(hm[2] or 0):02d}" if hm else "23:59"
        course, title = parts[1], parts[2]
        weight = hours = None
        for p in parts[3:]:
            mh = re.match(r"^(\d+(?:[.,]\d+)?)\s*h(?:ours?|eures?)?$", p, re.I)
            mw = re.match(r"^(\d+(?:[.,]\d+)?)\s*%?$", p)
            if mh:
                hours = float(mh[1].replace(",", "."))
            elif mw:
                weight = float(mw[1].replace(",", "."))
        t = title.lower()
        kind = "exam" if re.search(r"exam|final|intra|mi-session|midterm", t) else \
            "quiz" if re.search(r"quiz|questionnaire", t) else "assignment"
        rows.append({"course": course, "title": title, "kind": kind, "due_date": d.isoformat(), "due_time": time_s,
                     "weight": weight, "hours": hours})
    return rows, errors
