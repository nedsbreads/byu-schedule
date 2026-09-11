"""Poll ESPN for BYU game status changes (kickoff, final) and push
spoiler-free notifications via ntfy.sh. Meant to run periodically (see
.github/workflows/notify.yml); tracks already-notified games in
state/notified.json so each notification is only sent once.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from byu_schedule.espn import fetch_schedule

STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "notified.json"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"


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


async def notify(client: httpx.AsyncClient, title: str, message: str) -> None:
    await client.post(
        NTFY_URL,
        content=message.encode(),
        headers={"Title": title, "Tags": "football"},
    )


async def main() -> None:
    games = await fetch_schedule()
    state = load_state()
    started, final = state["started"], state["final"]
    new_started, new_final = set(started), set(final)

    async with httpx.AsyncClient(timeout=10) as client:
        for game in games:
            versus = "vs" if game.home_away == "home" else "@"

            if game.status in ("live", "final") and game.id not in started:
                await notify(
                    client, "BYU kickoff", f"BYU {versus} {game.opponent} has started"
                )
                new_started.add(game.id)

            if game.status == "final" and game.id not in final:
                verb = "beat" if game.result == "W" else "lost to"
                await notify(
                    client, f"BYU {game.result}", f"BYU {verb} {game.opponent}"
                )
                new_final.add(game.id)

    if new_started != started or new_final != final:
        save_state({"started": new_started, "final": new_final})


if __name__ == "__main__":
    asyncio.run(main())
