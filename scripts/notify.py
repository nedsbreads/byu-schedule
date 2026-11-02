"""Poll ESPN for BYU game status changes (start, final) and push
spoiler-free notifications via ntfy.sh, one topic per sport (env var named
by each Sport's `ntfy_env`; sports without a topic are skipped). Tracks
already-notified games per sport in state/notified.json so each
notification is only sent once.

GitHub's cron only fires every few hours, so instead of relying on it for
timing, each run *watches*: while any game is live or about to start, it
polls every POLL_SECONDS until it runs out of its time budget (GitHub kills
jobs at 6h). On exit it reports via $GITHUB_OUTPUT whether a game still
needs watching, and the workflow re-dispatches itself to continue the chain.
A scheduled run only has to land somewhere in the WATCH_AHEAD window before
a game starts to catch it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from byu_schedule import espn
from byu_schedule.espn import SPORTS, Game, Sport, fetch_schedule

STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "notified.json"

POLL_SECONDS = 60
# Run budget, kept under the workflow's timeout-minutes.
BUDGET_SECONDS = int(os.environ.get("WATCH_BUDGET_SECONDS", 5 * 3600 + 30 * 60))
# Start watching this long before a game. Must exceed the longest gap
# between GitHub's scheduled runs (observed up to ~7h).
WATCH_AHEAD = timedelta(hours=12)
# Keep watching a still-"scheduled" game this long past its listed start
# (TBD start times, weather delays).
WATCH_LATE = timedelta(hours=24)

# {sport_key: {"started": {ids}, "final": {ids}}}
State = dict[str, dict[str, set[str]]]


def load_state() -> State:
    if not STATE_PATH.exists():
        return {}
    data = json.loads(STATE_PATH.read_text())
    if "started" in data or "final" in data:
        # Pre-multi-sport format: flat lists were football only.
        data = {"football": data}
    return {
        key: {
            "started": set(entry.get("started", [])),
            "final": set(entry.get("final", [])),
        }
        for key, entry in data.items()
    }


def save_state(state: State) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                key: {k: sorted(v) for k, v in entry.items()}
                for key, entry in sorted(state.items())
            },
            indent=2,
        )
        + "\n"
    )


def topic_for(sport: Sport) -> str | None:
    return os.environ.get(sport.ntfy_env, "").strip() or None


def needs_watching(games: list[Game], now: datetime) -> bool:
    for game in games:
        if game.status == "live":
            return True
        if game.status == "scheduled":
            start = datetime.fromisoformat(game.date)
            if now - WATCH_LATE <= start <= now + WATCH_AHEAD:
                return True
    return False


async def notify(
    client: httpx.AsyncClient, sport: Sport, topic: str, title: str, message: str
) -> None:
    await client.post(
        f"https://ntfy.sh/{topic}",
        content=message.encode(),
        # ntfy emoji tag: "football" -> 🏈, "basketball" -> 🏀
        headers={"Title": title, "Tags": sport.espn_path.split("/")[0]},
    )


async def check_sport(
    client: httpx.AsyncClient, sport: Sport, topic: str, state: State
) -> list[Game]:
    games = await fetch_schedule(sport)

    if sport.key not in state:
        # First time seeing this sport: record games already under way or
        # over without notifying, so adding a sport can't flood the phone.
        state[sport.key] = {
            "started": {g.id for g in games if g.status in ("live", "final")},
            "final": {g.id for g in games if g.status == "final"},
        }
        save_state(state)
    seen = state[sport.key]

    for game in games:
        if game.status in ("live", "final") and game.id not in seen["started"]:
            await notify(
                client,
                sport,
                topic,
                f"BYU {sport.start_word}",
                f"BYU {game.versus} {game.opponent} has started",
            )
            seen["started"].add(game.id)
            save_state(state)

        if game.status == "final" and game.id not in seen["final"]:
            verb = "beat" if game.result == "W" else "lost to"
            await notify(
                client, sport, topic, f"BYU {game.result}", f"BYU {verb} {game.opponent}"
            )
            seen["final"].add(game.id)
            save_state(state)
    return games


async def main() -> None:
    state = load_state()
    deadline = time.monotonic() + BUDGET_SECONDS
    sports = [(s, t) for s in SPORTS.values() if (t := topic_for(s))]
    for s in SPORTS.values():
        if not topic_for(s):
            print(f"skipping {s.key}: {s.ntfy_env} not set")

    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            now = datetime.now(timezone.utc)
            watching = False
            for sport, topic in sports:
                try:
                    games = await check_sport(client, sport, topic, state)
                    # Log what the schedule vs live scoreboard feeds say, to
                    # measure how far the schedule feed lags during games.
                    stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                    for line in espn.feed_comparison.get(sport.key, []):
                        print(f"{stamp} {sport.key} feeds: {line}", flush=True)
                    watching |= needs_watching(games, now)
                except httpx.HTTPError as exc:
                    # Transient ESPN/ntfy failure: keep the watch alive.
                    print(f"{sport.key} check failed: {exc!r}", flush=True)
                    watching = True

            if not watching or time.monotonic() + POLL_SECONDS > deadline:
                break
            await asyncio.sleep(POLL_SECONDS)

    print(f"exiting; game still needs watching: {watching}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as f:
            f.write(f"rerun={'true' if watching else 'false'}\n")


if __name__ == "__main__":
    asyncio.run(main())
