"""Poll ESPN for BYU games that just went final and push a spoiler-free
result (win/loss only) to ntfy.sh. Meant to run periodically (see
.github/workflows/notify.yml), tracking already-notified game ids in
state/notified.json so each result is only sent once.
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


def load_notified() -> set[str]:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text()))
    return set()


def save_notified(ids: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(ids), indent=2) + "\n")


async def main() -> None:
    games = await fetch_schedule()
    notified = load_notified()
    updated = set(notified)

    async with httpx.AsyncClient(timeout=10) as client:
        for game in games:
            if game.status != "final" or game.id in notified:
                continue

            verb = "beat" if game.result == "W" else "lost to"
            message = f"BYU {verb} {game.opponent}"
            await client.post(
                NTFY_URL,
                content=message.encode(),
                headers={"Title": f"BYU {game.result}", "Tags": "football"},
            )
            updated.add(game.id)

    if updated != notified:
        save_notified(updated)


if __name__ == "__main__":
    asyncio.run(main())
