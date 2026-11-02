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
    """One followed team. (Named Sport for history; it's really team+sport.)"""

    key: str  # URL slug and state key
    title: str  # page heading, e.g. "BYU Football"
    tab_label: str
    espn_path: str
    team_slug: str  # ESPN team URL slug, e.g. "byu", "utah"
    team_abbr: str  # ESPN abbreviation identifying our team in a game
    team_name: str  # used in notifications: "BYU W", "Jazz beat ..."
    # Periods in regulation. Live display is capped here: showing an
    # overtime period would reveal a tie at the end of regulation.
    regulation_periods: int
    period_label: Callable[[int], str]
    start_word: str  # for notifications: "kickoff", "tip-off"
    ntfy_env: str  # env var holding this team's ntfy topic

    def schedule_url(self, season_type: int) -> str:
        return (
            f"{_BASE}/{self.espn_path}/teams/{self.team_slug}/schedule"
            f"?seasontype={season_type}"
        )

    def scoreboard_event_url(self, event_id: str) -> str:
        # Single-game scoreboard feed (~15-25 KB). The team schedule feed's
        # live status lags during games; this is the feed ESPN's own
        # scoreboard uses, so live status/results come from here.
        return f"{_BASE}/{self.espn_path}/scoreboard/{event_id}"


def _quarters(p: int) -> str:
    return f"Q{p}"


def _halves(p: int) -> str:
    return {1: "1st Half", 2: "2nd Half"}[p]


SPORTS: dict[str, Sport] = {
    s.key: s
    for s in [
        Sport(
            key="football",
            title="BYU Football",
            tab_label="BYU Football",
            espn_path="football/college-football",
            team_slug="byu",
            team_abbr="BYU",
            team_name="BYU",
            regulation_periods=4,
            period_label=_quarters,
            start_word="kickoff",
            ntfy_env="NTFY_TOPIC",
        ),
        Sport(
            key="mens-basketball",
            title="BYU Men's Basketball",
            tab_label="BYU Basketball",
            espn_path="basketball/mens-college-basketball",
            team_slug="byu",
            team_abbr="BYU",
            team_name="BYU",
            regulation_periods=2,  # men's college basketball plays halves
            period_label=_halves,
            start_word="tip-off",
            ntfy_env="NTFY_TOPIC_MBB",
        ),
        Sport(
            key="jazz",
            title="Utah Jazz",
            tab_label="Utah Jazz",
            espn_path="basketball/nba",
            team_slug="utah",
            team_abbr="UTAH",
            team_name="Jazz",
            regulation_periods=4,
            period_label=_quarters,
            start_word="tip-off",
            ntfy_env="NTFY_TOPIC_JAZZ",
        ),
    ]
}

# ESPN's team schedule only returns one season type per request, and the
# default excludes the postseason (bowl games, March Madness, NBA playoffs).
_SEASON_TYPES = (2, 3)  # regular season, postseason
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
    ours = next(
        c
        for c in competition["competitors"]
        if c["team"]["abbreviation"] == sport.team_abbr
    )
    result: Literal["W", "L"] | None = None
    if bucket == "final":
        result = "W" if ours.get("winner") else "L"
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


async def _get_schedule(
    client: httpx.AsyncClient, sport: Sport, season_type: int
) -> list[dict]:
    response = await client.get(sport.schedule_url(season_type))
    response.raise_for_status()
    return response.json().get("events", [])


async def fetch_schedule(sport: Sport = DEFAULT_SPORT) -> list[Game]:
    cached = _cache.get(sport.key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    async with httpx.AsyncClient(timeout=10) as client:
        regular, post = await asyncio.gather(
            *(_get_schedule(client, sport, t) for t in _SEASON_TYPES),
            return_exceptions=True,
        )
        if isinstance(regular, BaseException):
            raise regular
        events = list(regular)
        if isinstance(post, BaseException):
            # Postseason is a bonus; don't lose the whole page over it.
            print(f"{sport.key} postseason schedule failed: {post!r}")
        else:
            seen = {e["id"] for e in events}
            events += [e for e in post if e["id"] not in seen]
        events.sort(key=lambda e: e["date"])

        games: list[Game] = []
        schedule_details: dict[str, str | None] = {}
        for event in events:
            competition = event["competitions"][0]
            ours = next(
                c
                for c in competition["competitors"]
                if c["team"]["abbreviation"] == sport.team_abbr
            )
            opponent = next(c for c in competition["competitors"] if c is not ours)
            games.append(
                Game(
                    id=event["id"],
                    week=event.get("week", {}).get("number"),
                    date=event["date"],
                    opponent=opponent["team"]["displayName"],
                    home_away=ours["homeAway"],
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
