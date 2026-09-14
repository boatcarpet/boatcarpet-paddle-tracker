"""
Determine the paddle tennis start time for an upcoming Sunday, based on the
Detroit Lions kickoff time that day.

Rules (set by Kevin):
  Lions kick off at 1:00 PM ET      -> paddle at 4:30 PM
  Lions kick off at 4:00-4:30 PM ET -> paddle at 3:00 PM
  Lions play Sunday night (8-ish)   -> paddle at 4:00 PM
  No Lions game that Sunday         -> paddle at 4:00 PM

Data source: ESPN's public NFL scoreboard/schedule JSON (no API key required).
"""

from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
ESPN_SCHEDULE = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/det/schedule"
)

DEFAULT_START = "4:00 PM"


def next_sunday(today=None):
    """The upcoming Sunday. If today IS Sunday, returns today."""
    today = today or datetime.now(ET).date()
    return today + timedelta(days=(6 - today.weekday()) % 7)


def lions_kickoff(sunday, timeout=15):
    """
    Return a timezone-aware ET datetime for the Lions kickoff on `sunday`,
    or None if they don't play that day.

    Raises requests.RequestException / ValueError if ESPN can't be reached
    or returns something unexpected — callers should decide the fallback.
    """
    season = sunday.year if sunday.month >= 3 else sunday.year - 1
    resp = requests.get(
        ESPN_SCHEDULE,
        params={"season": season, "seasontype": 2},
        timeout=timeout,
        headers={"User-Agent": "paddle-tracker/1.0"},
    )
    resp.raise_for_status()
    events = resp.json().get("events", [])
    if not events:
        raise ValueError("ESPN returned no events for season %s" % season)

    for ev in events:
        # ESPN gives UTC ISO timestamps like 2026-10-11T20:25Z
        raw = ev.get("date")
        if not raw:
            continue
        utc = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        local = utc.astimezone(ET)
        if local.date() != sunday:
            continue

        # Late-season Sunday kickoffs aren't locked until the NFL flexes them.
        # Until then ESPN shows a PLACEHOLDER (usually 1:00 PM) with
        # timeValid=false. Taking that at face value would have us announce
        # 4:30 for a game that later moves to 4:25 — so treat it as unknown.
        comps = ev.get("competitions") or [{}]
        time_valid = comps[0].get("timeValid", ev.get("timeValid", True))
        return local, bool(time_valid)
    return None, True


def start_time_for(kickoff, time_valid=True):
    """
    Map a kickoff datetime (or None) to (start_time, reason, confident).

    confident=False means we are NOT sure of the time — either the Lions
    kickoff is still a placeholder, or something unexpected showed up.
    The email should hedge rather than state a time as fact.
    """
    if kickoff is None:
        return DEFAULT_START, "no Lions game", True

    hour, minute = kickoff.hour, kickoff.minute
    kick_label = kickoff.strftime("%-I:%M %p").replace(" 0", " ")

    if not time_valid:
        return (
            DEFAULT_START,
            "Lions kickoff not set yet (NFL hasn't flexed the time)",
            False,
        )

    if hour == 13:                      # 1:00 PM window
        return "4:30 PM", "Lions at %s" % kick_label, True
    if hour == 16 or (hour == 15 and minute >= 55):   # 4:05 / 4:25 window
        return "3:00 PM", "Lions at %s" % kick_label, True
    if hour >= 19:                      # Sunday night football
        return DEFAULT_START, "Lions at %s (night game)" % kick_label, True

    # Anything unexpected (an early London game, say) -> default, flagged.
    return DEFAULT_START, "Lions at %s (unusual kickoff)" % kick_label, False


def paddle_start(sunday=None):
    """
    Returns (start_time, reason, confident).
    confident=False means the email should hedge — either ESPN couldn't be
    reached, or the kickoff time isn't locked in yet.
    """
    sunday = sunday or next_sunday()
    try:
        kickoff, time_valid = lions_kickoff(sunday)
    except Exception as e:                      # network, parse, anything
        return DEFAULT_START, "could not reach NFL schedule (%s)" % type(e).__name__, False
    return start_time_for(kickoff, time_valid)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])
    else:
        target = next_sunday()
    start, reason, ok = paddle_start(target)
    flag = "" if ok else "  [FALLBACK]"
    print("%s  ->  paddle at %-8s (%s)%s" % (target, start, reason, flag))
