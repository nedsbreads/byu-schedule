from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from byu_schedule.espn import DEFAULT_SPORT, SPORTS, Sport, fetch_schedule

app = FastAPI(title="BYU Schedule (Spoiler-Free)")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Remembers the last team viewed, so "/" (the home-screen app's start URL)
# reopens to it.
TEAM_COOKIE = "team"


async def _render(request: Request, sport: Sport) -> HTMLResponse:
    games = await fetch_schedule(sport)
    now = datetime.now(timezone.utc)
    # Auto-reload while a game is live or about to start.
    auto_refresh = any(g.needs_live_check(now) for g in games)
    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "games": games,
            "sport": sport,
            "sports": SPORTS.values(),
            "auto_refresh": auto_refresh,
        },
    )
    # Never serve a stale copy from the browser cache.
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        TEAM_COOKIE, sport.key, max_age=400 * 24 * 3600, samesite="lax"
    )
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    sport = SPORTS.get(request.cookies.get(TEAM_COOKIE, ""), DEFAULT_SPORT)
    return await _render(request, sport)


@app.get("/{sport_key}", response_class=HTMLResponse)
async def sport_page(request: Request, sport_key: str) -> HTMLResponse:
    sport = SPORTS.get(sport_key)
    if sport is None:
        raise HTTPException(status_code=404)
    return await _render(request, sport)


def main() -> None:
    import uvicorn

    uvicorn.run("byu_schedule.main:app", host="0.0.0.0", port=8000, reload=True)
