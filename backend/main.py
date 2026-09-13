from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.exceptions import HTTPException
from contextlib import asynccontextmanager
from backend.database import init_db
from backend.config import settings
from backend.auth import get_current_user
from backend.models.user import User
from backend.routes import auth_routes, users, classes, subjects, calendar, homework, grades, files, vapid, admin, timetable, mealplan
from backend.routes.timetable import poll_cancelled_lessons_loop
from backend.version import get_version_info
import os, time, mimetypes, asyncio, logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BUILD_TS = str(int(time.time()))

# Some platforms (notably Windows) don't have .webp registered in their
# MIME registry, which makes StaticFiles fall back to text/plain and
# breaks <img> rendering for compressed avatars.
mimetypes.add_type("image/webp", ".webp")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    poll_task = asyncio.create_task(poll_cancelled_lessons_loop())
    yield
    poll_task.cancel()

app = FastAPI(title="Sofia", lifespan=lifespan)

# API routes
for r in [auth_routes, users, classes, subjects, calendar, homework, grades, files, vapid, admin, timetable, mealplan]:
    app.include_router(r.router)

# Static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if os.path.exists("uploads"):
    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Legal contact info, kept out of the repo and set per-deployment via .env
@app.get("/api/v1/config/impressum", include_in_schema=False)
async def impressum_config():
    return {
        "business_name": settings.impressum_business_name,
        "name": settings.impressum_name,
        "address": settings.impressum_address,
        "phone": settings.impressum_phone,
        "email": settings.impressum_email,
    }

# App version & build metadata endpoint
@app.get("/api/v1/version", include_in_schema=False)
async def app_version():
    return get_version_info(BUILD_TS)

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
    with open("static/sw.js", "r", encoding="utf-8") as f:
        content = f.read().replace("__BUILD__", BUILD_TS)
    return HTMLResponse(content, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"})

# Favicon handler
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    if os.path.exists("static/icons/favicon.ico"):
        return FileResponse("static/icons/favicon.ico")
    return HTMLResponse(content="", status_code=204)

# Apple touch icon root endpoints for iOS Home Screen
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
@app.get("/apple-touch-icon-180x180.png", include_in_schema=False)
@app.get("/apple-touch-icon-180x180-precomposed.png", include_in_schema=False)
async def apple_touch_icon():
    for p in ["static/icons/apple-touch-icon.png", "static/icons/icon-192.png"]:
        if os.path.exists(p):
            return FileResponse(p, media_type="image/png")
    raise HTTPException(status_code=404)

# Serve frontend SPA — inject build timestamp for cache busting
@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str, request: Request):
    with open("pages/index.html", "r", encoding="utf-8") as f:
        html = f.read().replace("__BUILD__", BUILD_TS)
    return HTMLResponse(html)
