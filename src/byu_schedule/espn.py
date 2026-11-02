from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import httpx

SCHEDULE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
    "/teams/byu/schedule"
)

_CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, list["Game"]]] = {}

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


async def fetch_schedule() -> list[Game]:
    cached = _cache.get("schedule")
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(SCHEDULE_URL)
        response.raise_for_status()
        data = response.json()

    games: list[Game] = []
    for event in data.get("events", []):
        competition = event["competitions"][0]
        status = competition["status"]
        bucket = _status_bucket(status["type"])

        byu = next(
            c for c in competition["competitors"] if c["team"]["abbreviation"] == "BYU"
        )
        opponent = next(
            c for c in competition["competitors"] if c["team"]["abbreviation"] != "BYU"
        )

        result: Literal["W", "L"] | None = None
        if bucket == "final":
            result = "W" if byu.get("winner") else "L"

        games.append(
            Game(
                id=event["id"],
                week=event.get("week", {}).get("number"),
                date=event["date"],
                opponent=opponent["team"]["displayName"],
                home_away=byu["homeAway"],
                status=bucket,
                result=result,
                period=_spoiler_free_period(status) if bucket == "live" else None,
            )
        )

    _cache["schedule"] = (time.monotonic(), games)
    return games
