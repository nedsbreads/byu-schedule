# byu-schedule

Spoiler-free schedules for BYU football, BYU men's basketball, and the Utah
Jazz: wins and losses only, no scores. Pulls live data from ESPN's public
API (no key required).

## Run locally

```
uv run uvicorn byu_schedule.main:app --reload
```

Then open http://127.0.0.1:8000 (`/football`, `/mens-basketball`, `/jazz`; `/`
shows whichever team you viewed last).

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

- Teams are defined in `SPORTS` in `src/byu_schedule/espn.py`. For each,
  it fetches ESPN's `teams/{team}/schedule` feed (regular season and
  postseason, so bowl games and playoffs appear) and keeps only: opponent,
  date, home/away/neutral, and whether the game is scheduled / live / final.
  For final games it reads ESPN's `winner` boolean to show `W`/`L` — it
  never reads or displays the score.
- For games live or about to start, status comes from ESPN's single-game
  scoreboard feed instead, which updates faster during games.
- Live games show only the period, never the clock, capped at the last
  regulation period (football and NBA Q4, men's college basketball 2nd
  Half) so overtime doesn't reveal a tie. Halftime shows as "Halftime".
- Results are cached in memory for 10 seconds. During a live game the page
  reloads itself every 30 seconds.

## Notifications

`scripts/notify.py` sends spoiler-free start and W/L push notifications via
[ntfy.sh](https://ntfy.sh), one topic per team, set in repo secrets:
`NTFY_TOPIC` (football), `NTFY_TOPIC_MBB` (men's basketball), and
`NTFY_TOPIC_JAZZ` (Jazz). A team whose secret isn't set is skipped. It runs
from GitHub Actions: when a game is live or within 12h of its start, the
run stays up polling ESPN every minute and re-dispatches itself until the
game is final, so notifications arrive within about a minute.
