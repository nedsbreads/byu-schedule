# byu-schedule

Spoiler-free schedules (BYU football, BYU men's basketball, Utah Jazz). The
core rule: never expose anything that hints at the score — no scores, no
game clock, and no overtime period numbers (live display caps at each
sport's last regulation period; see `Sport.regulation_periods` and
`_live_label` in `espn.py`). Men's college basketball plays halves, not
quarters.

## Sports

Each followed team is a `Sport` entry in `SPORTS` (`espn.py`): URL slug,
ESPN path and team slug/abbreviation (not all teams are BYU), titles,
period labels, and the env var holding its ntfy topic. The site has a tab
per sport at `/{key}`; `/` shows the last-viewed team (cookie). Notifier
state in `state/notified.json` is keyed by sport; a sport new to the state
file has its already-started/finished games recorded silently (no flood).

## Data sources

`espn.py` reads the season from ESPN's team schedule feed, then, for games
live or near kickoff, overlays status/result from the single-game
scoreboard feed (`scoreboard/{event_id}`), because the schedule feed lags
during games. The notifier logs both feeds' status each poll ("feeds:"
lines in the Actions log) to measure that lag.

## Multi-machine setup

This folder is synced between two Windows machines (a laptop and a
desktop), so anything in it, including `.venv/`, is shared.

- The `.venv/` in this folder belongs to the **laptop** (its `pyvenv.cfg`
  points at the laptop's uv Python). It does not run on the desktop.
- On the **desktop**, the environment lives outside the synced folder so the
  two machines don't clobber each other. Set this before any `uv` command
  there: `UV_PROJECT_ENVIRONMENT=$LOCALAPPDATA/venvs/byu-schedule`
- The desktop also has `uv` and the GitHub CLI (`gh`) installed.

## Notifications

`scripts/notify.py` runs from `.github/workflows/notify.yml` and commits
`state/notified.json` back to the repo, so `git pull` before committing.

GitHub throttles the `*/15` cron to every ~3-7h, so timing doesn't rely on
it: a run that finds a game live or within 12h of kickoff stays running,
polling every 60s, and re-dispatches itself before the 6h job limit until
the game is final. On game weekends that means near-continuous runs, which
is why the repo is public (unlimited Actions minutes; it was private with a
2,000 min/month cap). A `concurrency` group prevents overlapping watchers.
