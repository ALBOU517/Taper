"""iCalendar (RFC 5545) feed so workouts + reminders appear on any phone calendar."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from . import dsl

EMOJI = {"run": "🏃", "bike": "🚴", "swim": "🏊", "gym": "🏋️"}


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 octets must be folded."""
    out, b = [], line.encode()
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 sequence
            cut -= 1
        out.append(b[:cut].decode())
        b = b[cut:]
    out.append(b.decode())
    return "\r\n ".join(out)


def _when(d: date, start: str | None, minutes: int) -> list[str]:
    """Timed event when the planner picked a slot (floating local time), all-day otherwise."""
    if not start:
        return [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
    h, m = map(int, start.split(":"))
    t0 = datetime(d.year, d.month, d.day, h, m)
    return [f"DTSTART:{t0:%Y%m%dT%H%M%S}", f"DTEND:{t0 + timedelta(minutes=max(minutes, 5)):%Y%m%dT%H%M%S}"]


def build_calendar(sessions: Iterable[dict], name: str = "Taper training", reminder_hour: int = 7,
                   weekly_targets: dict | None = None) -> str:
    """``sessions``: dicts with id, day (date), sport, title, dsl, minutes, tss, status."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Taper//Training Engine//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(name)}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for s in sessions:
        if s.get("status") == "missed":
            continue
        d: date = s["day"]
        try:
            steps = dsl.describe(dsl.parse(s["dsl"]))
        except dsl.DSLError:
            steps = [s["dsl"]]
        desc = "\n".join([f"{s['minutes']} min · {s['tss']:.0f} TSS", ""] + [f"• {x}" for x in steps])
        if weekly_targets and s["sport"] in weekly_targets:
            desc += f"\n\nWeekly {s['sport']} target: {weekly_targets[s['sport']]}"
        title = f"{EMOJI.get(s['sport'], '')} {s['title']}".strip()
        if s.get("status") == "done":
            title = "✅ " + title
        lines += [
            "BEGIN:VEVENT",
            f"UID:taper-session-{s['id']}@taper.app",
            f"DTSTAMP:{now}",
            *_when(d, s.get("start_time"), s["minutes"]),
            f"SUMMARY:{_esc(title)}",
            f"DESCRIPTION:{_esc(desc)}",
            f"CATEGORIES:{s['sport'].upper()}",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_esc('Today: ' + s['title'])}",
            "TRIGGER:-PT60M" if s.get("start_time") else f"TRIGGER:PT{reminder_hour}H",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
