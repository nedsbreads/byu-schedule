"""Poll ESPN for BYU game status changes (kickoff, final) and push
spoiler-free notifications via ntfy.sh. Tracks already-notified games in
state/notified.json so each notification is only sent once.

GitHub's cron only fires every few hours, so instead of relying on it for
timing, each run *watches*: while a game is live or about to start, it polls
every POLL_SECONDS until it runs out of its time budget (GitHub kills jobs
at 6h). On exit it reports via $GITHUB_OUTPUT whether a game still needs
watching, and the workflow re-dispatches itself to continue the chain. A
scheduled run only has to land somewhere in the WATCH_AHEAD window before
kickoff to catch it.
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
from byu_schedule.espn import Game, fetch_schedule

STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "notified.json"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

POLL_SECONDS = 60
# Run budget, kept under the workflow's timeout-minutes.
BUDGET_SECONDS = int(os.environ.get("WATCH_BUDGET_SECONDS", 5 * 3600 + 30 * 60))
# Start watching this long before kickoff. Must exceed the longest gap
# between GitHub's scheduled runs (observed up to ~7h).
WATCH_AHEAD = timedelta(hours=12)
# Keep watching a still-"scheduled" game this long past its listed kickoff
# (TBD kickoff times, weather delays).
WATCH_LATE = timedelta(hours=24)


def load_state() -> dict[str, set[str]]:
    if STATE_PATH.exists():
        data = json.loads(STATE_PATH.read_text())
        return {
            "started": set(data.get("started", [])),
            "final": set(data.get("final", [])),
        }
    return {"started": set(), "final": set()}


def save_state(state: dict[str, set[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({k: sorted(v) for k, v in state.items()}, indent=2) + "\n"
    )


def needs_watching(games: list[Game], now: datetime) -> bool:
    for game in games:
        if game.status == "live":
            return True
        if game.status == "scheduled":
            kickoff = datetime.fromisoformat(game.date)
            if now - WATCH_LATE <= kickoff <= now + WATCH_AHEAD:
                return True
    return False


async def notify(client: httpx.AsyncClient, title: str, message: str) -> None:
    await client.post(
        NTFY_URL,
        content=message.encode(),
        headers={"Title": title, "Tags": "football"},
    )


async def check_once(client: httpx.AsyncClient, state: dict[str, set[str]]) -> list[Game]:
    games = await fetch_schedule()
    for game in games:
        versus = "vs" if game.home_away == "home" else "@"

        if game.status in ("live", "final") and game.id not in state["started"]:
            await notify(
                client, "BYU kickoff", f"BYU {versus} {game.opponent} has started"
            )
            state["started"].add(game.id)
            save_state(state)

        if game.status == "final" and game.id not in state["final"]:
            verb = "beat" if game.result == "W" else "lost to"
            await notify(client, f"BYU {game.result}", f"BYU {verb} {game.opponent}")
            state["final"].add(game.id)
            save_state(state)
    return games


async def main() -> None:
    state = load_state()
    deadline = time.monotonic() + BUDGET_SECONDS

    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                games = await check_once(client, state)
                # Log what the schedule vs live scoreboard feeds say, to
                # measure how far the schedule feed lags during games.
                stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                for line in espn.feed_comparison:
                    print(f"{stamp} feeds: {line}", flush=True)
            except httpx.HTTPError as exc:
                # Transient ESPN/ntfy failure: keep the watch alive.
                print(f"check failed: {exc!r}")
                games = None

            now = datetime.now(timezone.utc)
            watching = games is None or needs_watching(games, now)
            if not watching or time.monotonic() + POLL_SECONDS > deadline:
                break
            await asyncio.sleep(POLL_SECONDS)

    print(f"exiting; game still needs watching: {watching}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as f:
            f.write(f"rerun={'true' if watching else 'false'}\n")


if __name__ == "__main__":
    asyncio.run(main())
