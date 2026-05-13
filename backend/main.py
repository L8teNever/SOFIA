from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.exceptions import HTTPException
from contextlib import asynccontextmanager
from backend.database import init_db
from backend.config import settings
from backend.auth import get_current_user
from backend.models.user import User
from backend.routes import auth_routes, users, classes, subjects, calendar, homework, grades, messages, files, vapid, admin, timetable
import os

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

# Serve frontend SPA — catch-all returns index.html for all non-API paths
@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str, request: Request):
    return FileResponse("pages/index.html")
