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
        await _migrate_columns(conn)

async def _migrate_columns(conn):
    from sqlalchemy import text
    result = await conn.execute(text("PRAGMA table_info(messages)"))
    existing = {row[1] for row in result.fetchall()}
    if "reply_to_id" not in existing:
        await conn.execute(text("ALTER TABLE messages ADD COLUMN reply_to_id INTEGER"))
    if "edited" not in existing:
        await conn.execute(text("ALTER TABLE messages ADD COLUMN edited BOOLEAN DEFAULT 0"))
    if "deleted" not in existing:
        await conn.execute(text("ALTER TABLE messages ADD COLUMN deleted BOOLEAN DEFAULT 0"))
    if "waveform" not in existing:
        await conn.execute(text("ALTER TABLE messages ADD COLUMN waveform JSON"))
    if "poll_data" not in existing:
        await conn.execute(text("ALTER TABLE messages ADD COLUMN poll_data JSON"))
        
    result_users = await conn.execute(text("PRAGMA table_info(users)"))
    existing_users = {row[1] for row in result_users.fetchall()}
    if "muted_room_ids" not in existing_users:
        await conn.execute(text("ALTER TABLE users ADD COLUMN muted_room_ids TEXT DEFAULT '[]'"))
    if "muted_user_ids" not in existing_users:
        await conn.execute(text("ALTER TABLE users ADD COLUMN muted_user_ids TEXT DEFAULT '[]'"))
    if "avatar_url" not in existing_users:
        await conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url TEXT"))
