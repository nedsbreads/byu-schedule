# byu-schedule

Spoiler-free BYU football schedule. The core rule: never expose anything
that hints at the score — no scores, no game clock, and no overtime period
numbers (live games cap at Q4; see `_spoiler_free_period` in `espn.py`).

## Multi-machine setup

This folder lives in OneDrive and is worked on from two Windows machines:
a laptop and a desktop (`DESKTOP-8VC3R01`).

- The `.venv/` in this folder belongs to the **laptop** (its `pyvenv.cfg`
  points at the laptop's uv Python). It does not run on the desktop.
- On the **desktop**, the environment lives outside OneDrive so the two
  machines don't clobber each other. Set this before any `uv` command there:
  `UV_PROJECT_ENVIRONMENT=$LOCALAPPDATA/venvs/byu-schedule`
- Desktop tools (installed 2026-11-02 via winget): `uv` 0.13.6 and GitHub
  CLI `gh` (logged in as nedsbreads). If they aren't on PATH yet in a
  session, they're at
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe`
  and `C:\Program Files\GitHub CLI\gh.exe`.

## Notifications

`scripts/notify.py` runs from `.github/workflows/notify.yml` and commits
`state/notified.json` back to the repo, so `git pull` before committing.

GitHub throttles the `*/15` cron to every ~3-7h, so timing doesn't rely on
it: a run that finds a game live or within 12h of kickoff stays running,
polling every 60s, and re-dispatches itself before the 6h job limit until
the game is final. On game weekends that means near-continuous runs, which
is why the repo is public (unlimited Actions minutes; it was private with a
2,000 min/month cap). A `concurrency` group prevents overlapping watchers.
