from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

import httpx

_BASE = "https://site.api.espn.com/apis/site/v2/sports"


@dataclass(frozen=True)
class Sport:
    key: str  # URL slug and state key
    name: str
    espn_path: str
    # Periods in regulation. Live display is capped here: showing an
    # overtime period would reveal a tie at the end of regulation.
    regulation_periods: int
    period_label: Callable[[int], str]
    start_word: str  # for notifications: "kickoff", "tip-off"
    ntfy_env: str  # env var holding this sport's ntfy topic

    @property
    def schedule_url(self) -> str:
        return f"{_BASE}/{self.espn_path}/teams/byu/schedule"

    def scoreboard_event_url(self, event_id: str) -> str:
        # Single-game scoreboard feed (~15 KB). The team schedule feed's live
        # status lags during games; this is the feed ESPN's own scoreboard
        # uses, so live status/results come from here.
        return f"{_BASE}/{self.espn_path}/scoreboard/{event_id}"


SPORTS: dict[str, Sport] = {
    s.key: s
    for s in [
        Sport(
            key="football",
            name="Football",
            espn_path="football/college-football",
            regulation_periods=4,
            period_label=lambda p: f"Q{p}",
            start_word="kickoff",
            ntfy_env="NTFY_TOPIC",
        ),
        Sport(
            key="mens-basketball",
            name="Men's Basketball",
            espn_path="basketball/mens-college-basketball",
            regulation_periods=2,  # men's college basketball plays halves
            period_label=lambda p: {1: "1st Half", 2: "2nd Half"}[p],
            start_word="tip-off",
            ntfy_env="NTFY_TOPIC_MBB",
        ),
    ]
}
DEFAULT_SPORT = SPORTS["football"]

_CACHE_TTL_SECONDS = 10
_cache: dict[str, tuple[float, list["Game"]]] = {}

# A scheduled game is checked against the live feed from shortly before its
# listed start until this long after (covers TBD placeholders and delays).
_LIVE_CHECK_BEFORE = timedelta(minutes=15)
_LIVE_CHECK_AFTER = timedelta(hours=24)

# Per sport, from the latest refresh: what each feed reported for games
# checked live, e.g. "401856849 schedule='11/7 - 12:00 PM EST' live='7:42 -
# 3rd'". Used by the notifier to log feed lag; never displayed on the site.
feed_comparison: dict[str, list[str]] = {}

Status = Literal["scheduled", "live", "final"]


@dataclass
class Game:
    id: str
    week: int | None
    date: str
    opponent: str
    home_away: str
    neutral: bool
    status: Status
    result: Literal["W", "L"] | None
    # Spoiler-safe progress for live games: "Q3", "2nd Half", "Halftime".
    # Never past the last regulation period; the clock is never exposed.
    live_label: str | None = None
    # Start time not announced yet; `date` then holds ESPN's placeholder
    # (midnight Eastern on game day, e.g. 05:00Z).
    time_tbd: bool = False

    @property
    def versus(self) -> str:
        return "@" if self.home_away == "away" and not self.neutral else "vs"

    def needs_live_check(self, now: datetime) -> bool:
        if self.status == "live":
            return True
        if self.status == "scheduled":
            start = datetime.fromisoformat(self.date)
            return start - _LIVE_CHECK_BEFORE <= now <= start + _LIVE_CHECK_AFTER
        return False


def _status_bucket(status_type: dict) -> Status:
    state = status_type.get("state")
    if state == "post":
        return "final"
    if state == "in":
        return "live"
    return "scheduled"


def _live_label(sport: Sport, status: dict) -> str | None:
    if status["type"].get("name") == "STATUS_HALFTIME":
        return "Halftime"
    period = status.get("period")
    if not period:
        return None
    return sport.period_label(min(period, sport.regulation_periods))


def _live_fields(sport: Sport, competition: dict) -> dict:
    """Status, result, and live label from an ESPN competition object."""
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
        "live_label": _live_label(sport, status) if bucket == "live" else None,
    }


def _detail(competition: dict) -> str | None:
    return competition["status"]["type"].get("shortDetail")


async def _refresh_live(
    client: httpx.AsyncClient, sport: Sport, game: Game, schedule_detail: str | None
) -> None:
    response = await client.get(sport.scoreboard_event_url(game.id))
    response.raise_for_status()
    competition = response.json()["competitions"][0]
    for field, value in _live_fields(sport, competition).items():
        setattr(game, field, value)
    feed_comparison[sport.key].append(
        f"{game.id} schedule={schedule_detail!r} live={_detail(competition)!r}"
    )


async def fetch_schedule(sport: Sport = DEFAULT_SPORT) -> list[Game]:
    cached = _cache.get(sport.key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(sport.schedule_url)
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
                    neutral=bool(competition.get("neutralSite")),
                    time_tbd=event.get("timeValid") is False,
                    **_live_fields(sport, competition),
                )
            )
            schedule_details[event["id"]] = _detail(competition)

        # Overlay live status from the scoreboard feed for games in progress
        # or near their start. On failure, keep the schedule feed's status.
        now = datetime.now(timezone.utc)
        feed_comparison[sport.key] = []
        hot = [g for g in games if g.needs_live_check(now)]
        results = await asyncio.gather(
            *(_refresh_live(client, sport, g, schedule_details[g.id]) for g in hot),
            return_exceptions=True,
        )
        for game, result in zip(hot, results):
            if isinstance(result, Exception):
                feed_comparison[sport.key].append(f"{game.id} live feed failed: {result!r}")

    _cache[sport.key] = (time.monotonic(), games)
    return games
