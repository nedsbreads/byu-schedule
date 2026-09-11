from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from byu_schedule.espn import fetch_schedule

app = FastAPI(title="BYU Schedule (Spoiler-Free)")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    games = await fetch_schedule()
    return templates.TemplateResponse(request, "index.html", {"games": games})


def main() -> None:
    import uvicorn

    uvicorn.run("byu_schedule.main:app", host="0.0.0.0", port=8000, reload=True)
