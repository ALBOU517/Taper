"""Calendar parsing — fixtures are hand-written RFC 5545 text in the format Google Calendar exports."""
from datetime import date

from app.engine import calendar as C

TZ = "America/Toronto"
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
UID:class-1@google.com
DTSTART;TZID=America/Toronto:20261102T083000
DTEND;TZID=America/Toronto:20261102T113000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
EXDATE;TZID=America/Toronto:20261118T083000
SUMMARY:IFT-2008 Cours
END:VEVENT
BEGIN:VEVENT
UID:work-1@google.com
DTSTART:20261117T180000Z
DTEND:20261117T220000Z
SUMMARY:Quart Lanterne Rouge
END:VEVENT
BEGIN:VEVENT
UID:exam-1@google.com
DTSTART;TZID=America/Toronto:20261204T130000
DTEND;TZID=America/Toronto:20261204T160000
SUMMARY:Examen final IFT-2003
END:VEVENT
BEGIN:VEVENT
UID:free-1@google.com
DTSTART;TZID=America/Toronto:20261117T070000
DTEND;TZID=America/Toronto:20261117T080000
TRANSP:TRANSPARENT
SUMMARY:Reminder: call home
END:VEVENT
BEGIN:VEVENT
UID:allday-1@google.com
DTSTART;VALUE=DATE:20261119
DTEND;VALUE=DATE:20261120
SUMMARY:Birthday
END:VEVENT
END:VCALENDAR
"""


def test_recurring_classes_expand_and_respect_exdate():
    busy = C.parse_ics(ICS, date(2026, 11, 16), date(2026, 11, 22), TZ)
    classes = [b for b in busy if b.kind == "class"]
    assert [b.start.date() for b in classes] == [date(2026, 11, 16)]        # Wed 18 excluded
    assert classes[0].start.hour == 8 and classes[0].start.minute == 30


def test_utc_events_converted_to_local_time():
    busy = C.parse_ics(ICS, date(2026, 11, 17), date(2026, 11, 17), TZ)
    work = next(b for b in busy if b.kind == "work")
    assert work.start.hour == 13 and work.end.hour == 17                    # 18:00Z = 13:00 EST


def test_transparent_events_do_not_block():
    busy = C.parse_ics(ICS, date(2026, 11, 17), date(2026, 11, 17), TZ)
    assert not any("Reminder" in b.title for b in busy)


def test_free_windows_with_buffers():
    busy = C.parse_ics(ICS, date(2026, 11, 16), date(2026, 11, 16), TZ)
    av = C.availability(date(2026, 11, 16), busy, TZ, day_start="06:30", day_end="21:30", buffer_min=15)
    # class 08:30-11:30 padded to 08:15-11:45 → windows 06:30-08:15 (105) and 11:45-21:30 (585)
    assert [w.minutes for w in av.windows] == [105, 585]
    assert av.longest_min == 585 and av.total_min == 690


def test_all_day_events_dont_block_time():
    busy = C.parse_ics(ICS, date(2026, 11, 19), date(2026, 11, 19), TZ)
    av = C.availability(date(2026, 11, 19), busy, TZ)
    assert av.total_min == 15 * 60


def test_exam_detection_and_exam_weeks():
    busy = C.parse_ics(ICS, date(2026, 11, 30), date(2026, 12, 6), TZ)
    ex = C.exam_days(busy)
    assert ex == {date(2026, 12, 4)}
    assert C.availability(date(2026, 12, 4), busy, TZ).exam
    assert C.exam_weeks(ex) == {date(2026, 11, 30)}
    assert C.exam_weeks({date(2026, 12, 7)}) == {date(2026, 12, 7), date(2026, 11, 30)}   # Monday exam → prior week too


def test_assign_slots_prefers_evening_and_never_overlaps():
    busy = C.parse_ics(ICS, date(2026, 11, 17), date(2026, 11, 17), TZ)
    av = C.availability(date(2026, 11, 17), busy, TZ)

    class S:
        def __init__(self, m):
            self.minutes = m
    a, b = S(90), S(45)
    slots = C.assign_slots([a, b], av.windows, prefer="evening")
    assert slots[a] == "20:00"                  # ends at 21:30
    assert slots[b] < "20:00"


def test_malformed_calendar_is_reported_not_raised():
    busy, err = C.safe_parse("not a calendar", date(2026, 1, 1), date(2026, 1, 7), TZ)
    assert busy == [] and err
