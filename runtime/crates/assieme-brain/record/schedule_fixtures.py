"""Records what Python's schedule.py answers for each time case: `tests/data/schedule.json`.

Run from the repository root with the repo's own Python:  .venv/bin/python runtime/crates/assieme-brain/record/schedule_fixtures.py
"""
import datetime as dt
import json
import os
import sys
import zoneinfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
sys.path.insert(0, ROOT)
from assieme import schedule  # noqa: E402


def z(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))


def iso(t):
    return None if t is None else schedule._iso(t)


ZONES = ['UTC', 'Europe/Lisbon', 'America/New_York', 'Asia/Kolkata', 'Australia/Lord_Howe']
EVERY = [
    {'kind': 'day', 'at': '02:00'}, {'kind': 'day', 'at': '00:00'}, {'kind': 'day', 'at': '23:59'},
    {'kind': 'day', 'at': '01:30'}, {'kind': 'day', 'at': '02:30'},
    {'kind': 'week', 'day': 0, 'at': '09:00'}, {'kind': 'week', 'day': 6, 'at': '01:30'}, {'kind': 'week', 'day': 3, 'at': '12:00'},
    {'kind': 'hours', 'n': 1}, {'kind': 'hours', 'n': 6}, {'kind': 'hours', 'n': 168},
    {'kind': 'minutes', 'n': 5}, {'kind': 'minutes', 'n': 90},
]
CREATED = ['2026-01-10T08:15:00Z', '2026-03-28T23:00:00Z', '2026-10-24T22:30:00Z']
# Around the clock changes of 2026: Lisbon and New York spring forward and fall back; Lord Howe moves by 30 minutes.
NOWS = ['2026-01-10T08:15:00Z', '2026-01-10T08:15:01Z', '2026-01-10T09:00:00Z', '2026-01-11T02:00:00Z',
        '2026-03-08T07:30:00Z', '2026-03-08T10:30:00Z', '2026-03-29T00:59:59Z', '2026-03-29T01:00:00Z',
        '2026-03-29T01:30:00Z', '2026-03-30T12:00:00Z', '2026-04-04T14:30:00Z', '2026-10-25T00:30:00Z',
        '2026-10-25T01:30:00Z', '2026-10-25T02:30:00Z', '2026-11-01T05:30:00Z', '2026-11-01T06:30:00Z',
        '2026-11-21T14:45:00Z', '2026-12-31T23:59:59Z']
slots = []
for zone in ZONES:
    tz = zoneinfo.ZoneInfo(zone)
    for every in EVERY:
        for created in CREATED:
            for now in NOWS:
                c, n = z(created), z(now)
                slots.append({'zone': zone, 'every': every, 'created': created, 'now': now,
                              'latest': iso(schedule.latest(every, c, n, tz)), 'next': iso(schedule.next_time(every, c, n, tz))})

words = []
for every in EVERY + [{'kind': 'hours', 'n': 2}, {'kind': 'minutes', 'n': 1}]:
    words.append({'every': every, 'when': schedule.when_words(every)})

days = []
now = z('2026-11-21T14:45:00Z')
for zone in ['UTC', 'Europe/Lisbon', 'Asia/Kolkata']:
    tz = zoneinfo.ZoneInfo(zone)
    for t in ['2026-11-21T14:45:00Z', '2026-11-21T23:30:00Z', '2026-11-22T00:00:00Z', '2026-11-20T23:59:00Z', '2026-11-19T10:00:00Z',
              '2026-11-25T03:05:00Z', '2026-11-27T22:59:00Z', '2026-11-28T00:30:00Z', '2026-12-05T09:09:00Z', '2026-01-02T00:00:00Z']:
        days.append({'zone': zone, 'now': '2026-11-21T14:45:00Z', 'time': t, 'words': schedule._day_words(z(t), now, tz)})

CHECKS = [
    {'kind': 'day', 'at': '02:00'}, {'kind': 'day', 'at': '2:00'}, {'kind': 'day', 'at': '24:00'}, {'kind': 'day', 'at': '23:60'},
    {'kind': 'day'}, {'kind': 'day', 'at': 200}, {'kind': 'week', 'at': '09:00'}, {'kind': 'week', 'at': '09:00', 'day': 7},
    {'kind': 'week', 'at': '09:00', 'day': -1}, {'kind': 'week', 'at': '09:00', 'day': True}, {'kind': 'week', 'at': '09:00', 'day': 6},
    {'kind': 'week', 'at': '09:00', 'day': '3'}, {'kind': 'hours', 'n': 1}, {'kind': 'hours', 'n': 0}, {'kind': 'hours', 'n': 168},
    {'kind': 'hours', 'n': 169}, {'kind': 'hours', 'n': True}, {'kind': 'hours', 'n': 2.0}, {'kind': 'hours', 'n': '2'},
    {'kind': 'hours'}, {'kind': 'minutes', 'n': 4}, {'kind': 'minutes', 'n': 5}, {'kind': 'minutes', 'n': 1440},
    {'kind': 'minutes', 'n': 1441}, {'kind': 'month'}, {'kind': None}, {}, [], 'day', None, 3,
    {'kind': 'day', 'at': '02:00', 'extra': 1},
]
checks = []
for raw in CHECKS:
    try:
        checks.append({'raw': raw, 'ok': schedule._every(raw)})
    except schedule.ScheduleError as e:
        checks.append({'raw': raw, 'error': str(e)})

path = os.path.join(os.path.dirname(__file__), '..', 'tests', 'data', 'schedule.json')
with open(path, 'w') as f:
    json.dump({'slots': slots, 'words': words, 'days': days, 'checks': checks}, f, indent=0, ensure_ascii=False)
    f.write('\n')
print(len(slots), 'slots,', len(days), 'days,', len(checks), 'checks')
