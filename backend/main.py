from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.exceptions import HTTPException
from contextlib import asynccontextmanager
from backend.database import init_db
from backend.config import settings
from backend.auth import get_current_user
from backend.models.user import User
from backend.routes import auth_routes, users, classes, subjects, calendar, homework, grades, messages, files, vapid, admin, timetable
import os, time

BUILD_TS = str(int(time.time()))

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Sofia", lifespan=lifespan)

# API routes
for r in [auth_routes, users, classes, subjects, calendar, homework, grades, messages, files, vapid, admin, timetable]:
    app.include_router(r.router)

# Static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if os.path.exists("uploads"):
    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Page fragments — only accessible when authenticated
@app.get("/pages/{page_name}.html", include_in_schema=False)
async def serve_page(page_name: str, _: User = Depends(get_current_user)):
    path = f"pages/{page_name}.html"
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="Page not found")

# Service Worker at root so it controls all pages (default scope = /)
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})

# Favicon handler to prevent catching by wildcard SPA route
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return HTMLResponse(content="", status_code=204)

# Serve frontend SPA — inject build timestamp for cache busting
@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str, request: Request):
    with open("pages/index.html", "r", encoding="utf-8") as f:
        html = f.read().replace("__BUILD__", BUILD_TS)
    return HTMLResponse(html)
