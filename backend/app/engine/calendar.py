"""
Life calendar: turn an iCalendar feed (e.g. Google Calendar's secret iCal
address) into busy blocks, free training windows and exam days.

Recurring events (RRULE, EXDATE, moved instances) are expanded with
``recurring_ical_events`` so weekly classes and shifts are handled correctly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

EXAM_RE = re.compile(r"\b(exam\w*|intra\w*|final|finale|midterm|mi-session|quiz|test)\b", re.I)
CLASS_RE = re.compile(r"\b([A-Z]{3}-?\d{4}|cours|lecture|class|lab|TP|TD|séminaire|seminar)\b", re.I)
WORK_RE = re.compile(r"\b(travail|work|shift|quart|job|stage|internship)\b", re.I)
STUDY_RE = re.compile(r"\b(étud\w*|study|révision\w*|revision|devoir|homework)\b", re.I)


@dataclass
class Busy:
    start: datetime
    end: datetime
    title: str
    kind: str          # exam | class | work | study | other
    all_day: bool = False


@dataclass
class Window:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class DayAvailability:
    day: date
    total_min: int
    longest_min: int
    windows: list
    exam: bool = False


def classify(title: str) -> str:
    if EXAM_RE.search(title):
        return "exam"
    if CLASS_RE.search(title):
        return "class"
    if WORK_RE.search(title):
        return "work"
    if STUDY_RE.search(title):
        return "study"
    return "other"


def parse_ics(text: str, start: date, end: date, tz: str = "America/Toronto") -> list[Busy]:
    """Expand all events between ``start`` and ``end`` (inclusive) in local time."""
    zone = ZoneInfo(tz)
    cal = icalendar.Calendar.from_ical(text)
    out: list[Busy] = []
    for ev in recurring_ical_events.of(cal).between(start, end + timedelta(days=1)):
        if str(ev.get("TRANSP", "")).upper() == "TRANSPARENT" and not EXAM_RE.search(str(ev.get("SUMMARY", ""))):
            continue                                   # "show as free" events don't block time
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        s = ev.decoded("DTSTART")
        e = ev.decoded("DTEND") if ev.get("DTEND") else None
        title = str(ev.get("SUMMARY", "Busy"))
        if isinstance(s, datetime):
            s = s.astimezone(zone) if s.tzinfo else s.replace(tzinfo=zone)
            if e is None:
                e = s + (ev.decoded("DURATION") if ev.get("DURATION") else timedelta(hours=1))
            e = e.astimezone(zone) if e.tzinfo else e.replace(tzinfo=zone)
            out.append(Busy(s, e, title, classify(title)))
        else:                                          # all-day event (date, not datetime)
            e = e or s + timedelta(days=1)
            out.append(Busy(datetime.combine(s, time(0), zone), datetime.combine(e, time(0), zone),
                            title, classify(title), all_day=True))
    return sorted(out, key=lambda b: b.start)


def _hm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def availability(day: date, busy: Iterable[Busy], tz: str = "America/Toronto", day_start: str = "06:30",
                 day_end: str = "21:30", buffer_min: int = 15, min_window: int = 25) -> DayAvailability:
    """Free windows for training on ``day``.

    The usable day is [day_start, day_end] (keeps sleep protected). Each busy
    block is padded by ``buffer_min`` on both sides for travel/changing. All-day
    events don't block time (they're usually reminders) but all-day exams still
    flag the day.
    """
    zone = ZoneInfo(tz)
    lo = datetime.combine(day, _hm(day_start), zone)
    hi = datetime.combine(day, _hm(day_end), zone)
    pad = timedelta(minutes=buffer_min)
    blocks, exam = [], False
    for b in busy:
        if b.end <= lo - pad or b.start >= hi + pad:
            if b.all_day and b.kind == "exam" and b.start.date() <= day < b.end.date():
                exam = True
            continue
        if b.kind == "exam" and (b.all_day and b.start.date() <= day < b.end.date() or b.start.date() == day):
            exam = True
        if b.all_day:
            continue
        blocks.append((max(lo, b.start - pad), min(hi, b.end + pad)))
    blocks.sort()
    windows, cur = [], lo
    for s, e in blocks:
        if s > cur:
            windows.append(Window(cur, s))
        cur = max(cur, e)
    if cur < hi:
        windows.append(Window(cur, hi))
    windows = [w for w in windows if w.minutes >= min_window]
    total = sum(w.minutes for w in windows)
    longest = max((w.minutes for w in windows), default=0)
    return DayAvailability(day, total, longest, windows, exam)


def assign_slots(sessions: list, windows: list[Window], prefer: str = "any") -> dict:
    """Pick a start time for each session (longest/most important first).

    ``sessions``: objects with ``minutes`` and ``id`` (or any hashable). Returns
    {session: "HH:MM"}. ``prefer`` = morning | evening | any.
    """
    free = [Window(w.start, w.end) for w in windows]
    out = {}
    for s in sorted(sessions, key=lambda x: -x.minutes):
        fits = [w for w in free if w.minutes >= s.minutes]
        if not fits:
            continue
        if prefer == "evening":
            w = max(fits, key=lambda w: w.end)
            start = w.end - timedelta(minutes=s.minutes)
        elif prefer == "morning":
            w = min(fits, key=lambda w: w.start)
            start = w.start
        else:   # any: snuggest fit, earliest start
            w = min(fits, key=lambda w: (w.minutes - s.minutes, w.start))
            start = w.start
        start = start.replace(minute=(start.minute // 5) * 5, second=0, microsecond=0)
        if start < w.start:
            start += timedelta(minutes=5)
        out[s] = start.strftime("%H:%M")
        end = start + timedelta(minutes=s.minutes)
        free.remove(w)
        if start > w.start and (start - w.start).total_seconds() >= 1500:
            free.append(Window(w.start, start))
        if w.end > end and (w.end - end).total_seconds() >= 1500:
            free.append(Window(end, w.end))
    return out


def recovery_days(busy: Iterable[Busy], tz: str = "America/Toronto") -> set[date]:
    """Days after a night shift: an event of 6 h or more that runs past midnight and ends
    between 03:00 and 12:00. Sleep debt is high, so Taper keeps those days easy."""
    out: set[date] = set()
    z = ZoneInfo(tz)
    for b in busy:
        if b.all_day:
            continue
        s, e = b.start.astimezone(z), b.end.astimezone(z)
        if e - s >= timedelta(hours=6) and e.date() > s.date() and 3 <= e.hour < 12:
            out.add(e.date())
    return out


def exam_days(busy: Iterable[Busy]) -> set[date]:
    out = set()
    for b in busy:
        if b.kind != "exam":
            continue
        d = b.start.date()
        while d < max(b.end.date(), b.start.date() + timedelta(days=1)):
            out.add(d)
            d += timedelta(days=1)
            if not b.all_day:
                break
    return out


def exam_weeks(exams: Iterable[date], lookahead_days: int = 3) -> set[date]:
    """Mondays of weeks that contain an exam or sit just before one."""
    mondays = set()
    for d in exams:
        for back in range(0, lookahead_days + 1):
            x = d - timedelta(days=back)
            mondays.add(x - timedelta(days=x.weekday()))
    return mondays


def availability_range(start: date, end: date, busy: list[Busy], **kw) -> dict[date, DayAvailability]:
    out = {}
    d = start
    while d <= end:
        out[d] = availability(d, busy, **kw)
        d += timedelta(days=1)
    return out


def ics_summary(busy: list[Busy], start: date, days: int = 7) -> dict[str, int]:
    """Minutes per kind over a range — for the 'your week' header."""
    end = datetime.combine(start + timedelta(days=days), time(0), busy[0].start.tzinfo) if busy else None
    out: dict[str, int] = {}
    for b in busy:
        if b.all_day or (end and b.start >= end):
            continue
        out[b.kind] = out.get(b.kind, 0) + int((b.end - b.start).total_seconds() // 60)
    return out


def safe_parse(text: str, start: date, end: date, tz: str) -> tuple[list[Busy], Optional[str]]:
    try:
        return parse_ics(text, start, end, tz), None
    except Exception as e:  # noqa: BLE001 — malformed third-party data must not crash the app
        return [], f"Couldn't read this calendar: {e.__class__.__name__}"
