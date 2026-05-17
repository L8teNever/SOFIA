import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from backend.config import settings
import os

os.makedirs(settings.upload_dir, exist_ok=True)

# Ensure DB directory exists (for SQLite file path)
_db_path = settings.database_url.replace("sqlite+aiosqlite:///", "").replace("sqlite+aiosqlite://", "")
if _db_path and not _db_path.startswith(":"):
    os.makedirs(os.path.dirname(os.path.abspath(_db_path)), exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    from backend.models import user, class_group, subject, calendar_event, homework, grade, message, shared_file, notification
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
