# byu-schedule

Spoiler-free BYU football schedule: wins and losses only, no scores. Pulls
live data from ESPN's public scoreboard API (no key required).

## Run locally

```
uv run uvicorn byu_schedule.main:app --reload
```

Then open http://127.0.0.1:8000

## Deploy (Render, free tier)

1. Push this repo to GitHub.
2. In Render: New -> Blueprint, point it at the repo. `render.yaml` in this
   repo defines the service (build: `uv sync`, start: `uv run uvicorn ...`).
3. Once deployed, Render gives you a public URL (e.g.
   `https://byu-schedule.onrender.com`) — open that from your phone or
   computer.

Note: Render's free web services spin down after ~15 min of inactivity and
take a few seconds to wake back up on the next request.

## How it works

- `src/byu_schedule/espn.py` fetches
  `site.api.espn.com/.../teams/byu/schedule`, and for each game keeps only:
  opponent, date, home/away, and whether the game is scheduled / live /
  final. For final games it reads ESPN's `winner` boolean to show `W`/`L` —
  it never reads or displays the score.
- Results are cached in memory for 30 seconds to avoid hitting ESPN on every
  page load.
- Live games show only the quarter (no clock), capped at Q4 so overtime
  doesn't reveal a tie.

## Notifications

`scripts/notify.py` sends spoiler-free kickoff and W/L push notifications via
[ntfy.sh](https://ntfy.sh) (topic set in the `NTFY_TOPIC` repo secret). It
runs from GitHub Actions: when a game is live or within 12h of kickoff, the
run stays up polling ESPN every minute and re-dispatches itself until the
game is final, so notifications arrive within about a minute.
