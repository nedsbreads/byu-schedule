from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from byu_schedule.espn import fetch_schedule

app = FastAPI(title="BYU Schedule (Spoiler-Free)")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    games = await fetch_schedule()
    now = datetime.now(timezone.utc)
    # Auto-reload while a game is live or about to kick off.
    auto_refresh = any(g.needs_live_check(now) for g in games)
    response = templates.TemplateResponse(
        request, "index.html", {"games": games, "auto_refresh": auto_refresh}
    )
    # Never serve a stale copy from the browser cache.
    response.headers["Cache-Control"] = "no-store"
    return response


def main() -> None:
    import uvicorn

    uvicorn.run("byu_schedule.main:app", host="0.0.0.0", port=8000, reload=True)
