from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx

_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
SCHEDULE_URL = f"{_BASE}/teams/byu/schedule"
# Single-game scoreboard feed (~18 KB). The team schedule feed's live status
# is reported to lag minutes behind during games; this is the feed ESPN's
# own scoreboard uses, so live status/results come from here.
SCOREBOARD_EVENT_URL = f"{_BASE}/scoreboard/{{event_id}}"

_CACHE_TTL_SECONDS = 10
_cache: dict[str, tuple[float, list["Game"]]] = {}

# A scheduled game is checked against the live feed from shortly before its
# listed kickoff until this long after (covers TBD placeholders and delays).
_LIVE_CHECK_BEFORE = timedelta(minutes=15)
_LIVE_CHECK_AFTER = timedelta(hours=24)

# Per refresh, what each feed reported for games checked live, e.g.
# "401856849 schedule='11/7 - 12:00 PM EST' live='7:42 - 3rd'". Used by the
# notifier to log feed lag; never displayed on the site.
feed_comparison: list[str] = []

Status = Literal["scheduled", "live", "final"]


@dataclass
class Game:
    id: str
    week: int | None
    date: str
    opponent: str
    home_away: str
    status: Status
    result: Literal["W", "L"] | None
    # Capped at 4: showing an overtime period would reveal a tie at the end of
    # regulation. The game clock is deliberately not exposed.
    period: int | None = None
    halftime: bool = False
    # Kickoff time not announced yet; `date` then holds ESPN's placeholder
    # (midnight Eastern on game day, e.g. 05:00Z).
    time_tbd: bool = False

    def needs_live_check(self, now: datetime) -> bool:
        if self.status == "live":
            return True
        if self.status == "scheduled":
            kickoff = datetime.fromisoformat(self.date)
            return kickoff - _LIVE_CHECK_BEFORE <= now <= kickoff + _LIVE_CHECK_AFTER
        return False


def _spoiler_free_period(status: dict) -> int | None:
    period = status.get("period")
    if not period:
        return None
    return min(period, 4)


def _status_bucket(status_type: dict) -> Status:
    state = status_type.get("state")
    if state == "post":
        return "final"
    if state == "in":
        return "live"
    return "scheduled"


def _live_fields(competition: dict) -> dict:
    """Status, result, and period from an ESPN competition object."""
    status = competition["status"]
    bucket = _status_bucket(status["type"])
    byu = next(
        c for c in competition["competitors"] if c["team"]["abbreviation"] == "BYU"
    )
    result: Literal["W", "L"] | None = None
    if bucket == "final":
        result = "W" if byu.get("winner") else "L"
    return {
        "status": bucket,
        "result": result,
        "period": _spoiler_free_period(status) if bucket == "live" else None,
        "halftime": bucket == "live" and status["type"].get("name") == "STATUS_HALFTIME",
    }


def _detail(competition: dict) -> str | None:
    return competition["status"]["type"].get("shortDetail")


async def _refresh_live(client: httpx.AsyncClient, game: Game, schedule_detail: str | None) -> None:
    response = await client.get(SCOREBOARD_EVENT_URL.format(event_id=game.id))
    response.raise_for_status()
    competition = response.json()["competitions"][0]
    for field, value in _live_fields(competition).items():
        setattr(game, field, value)
    feed_comparison.append(
        f"{game.id} schedule={schedule_detail!r} live={_detail(competition)!r}"
    )


async def fetch_schedule() -> list[Game]:
    cached = _cache.get("schedule")
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(SCHEDULE_URL)
        response.raise_for_status()
        data = response.json()

        games: list[Game] = []
        schedule_details: dict[str, str | None] = {}
        for event in data.get("events", []):
            competition = event["competitions"][0]
            byu = next(
                c for c in competition["competitors"] if c["team"]["abbreviation"] == "BYU"
            )
            opponent = next(
                c for c in competition["competitors"] if c["team"]["abbreviation"] != "BYU"
            )
            games.append(
                Game(
                    id=event["id"],
                    week=event.get("week", {}).get("number"),
                    date=event["date"],
                    opponent=opponent["team"]["displayName"],
                    home_away=byu["homeAway"],
                    time_tbd=event.get("timeValid") is False,
                    **_live_fields(competition),
                )
            )
            schedule_details[event["id"]] = _detail(competition)

        # Overlay live status from the scoreboard feed for games in progress
        # or near kickoff. On failure, keep the schedule feed's status.
        now = datetime.now(timezone.utc)
        feed_comparison.clear()
        hot = [g for g in games if g.needs_live_check(now)]
        results = await asyncio.gather(
            *(_refresh_live(client, g, schedule_details[g.id]) for g in hot),
            return_exceptions=True,
        )
        for game, result in zip(hot, results):
            if isinstance(result, Exception):
                feed_comparison.append(f"{game.id} live feed failed: {result!r}")

    _cache["schedule"] = (time.monotonic(), games)
    return games
