import os
from sqlalchemy import event
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

if settings.database_url.startswith("sqlite"):
    # WAL lets readers keep going while a write is in progress instead of
    # locking the whole file — matters once more than one person is using
    # the app at the same time. synchronous=NORMAL is the standard pairing
    # with WAL (still crash-safe, just skips an fsync WAL already covers).
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    from backend.models import (
        user, class_group, subject, calendar_event, homework, homework_solution,
        grade, shared_file, notification, meal_plan,
        push_subscription, notification_setting, sent_notification_log,
        subject_weighting, audit_log, manual_timetable_entry, drive_file, drive_topic,
        google_account, google_sync_map
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)

async def _migrate_columns(conn):
    from sqlalchemy import text
    import json
    result_users = await conn.execute(text("PRAGMA table_info(users)"))
    existing_users = {row[1] for row in result_users.fetchall()}
    if "avatar_url" not in existing_users:
        await conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url TEXT"))

    result_hw = await conn.execute(text("PRAGMA table_info(homework)"))
    existing_hw = {row[1] for row in result_hw.fetchall()}
    if "file_url" not in existing_hw:
        await conn.execute(text("ALTER TABLE homework ADD COLUMN file_url TEXT"))
    if "file_type" not in existing_hw:
        await conn.execute(text("ALTER TABLE homework ADD COLUMN file_type TEXT"))
    if "attachments" not in existing_hw:
        await conn.execute(text("ALTER TABLE homework ADD COLUMN attachments JSON DEFAULT '[]'"))

    result_cal = await conn.execute(text("PRAGMA table_info(calendar_events)"))
    existing_cal = {row[1] for row in result_cal.fetchall()}
    if "end_date" not in existing_cal:
        await conn.execute(text("ALTER TABLE calendar_events ADD COLUMN end_date TEXT"))
    if "description" not in existing_cal:
        await conn.execute(text("ALTER TABLE calendar_events ADD COLUMN description TEXT"))

    result_gr = await conn.execute(text("PRAGMA table_info(grades)"))
    existing_gr = {row[1] for row in result_gr.fetchall()}
    if "weight_type" not in existing_gr:
        await conn.execute(text("ALTER TABLE grades ADD COLUMN weight_type TEXT DEFAULT 'exam'"))
    if "weight" not in existing_gr:
        await conn.execute(text("ALTER TABLE grades ADD COLUMN weight FLOAT DEFAULT 1.0"))

    result_cls = await conn.execute(text("PRAGMA table_info(class_groups)"))
    existing_cls = {row[1] for row in result_cls.fetchall()}
    if "timetable_source" not in existing_cls:
        await conn.execute(text("ALTER TABLE class_groups ADD COLUMN timetable_source TEXT DEFAULT 'untis'"))

    result_df = await conn.execute(text("PRAGMA table_info(drive_files)"))
    existing_df = {row[1] for row in result_df.fetchall()}
    if "is_lecture_notes" not in existing_df:
        await conn.execute(text("ALTER TABLE drive_files ADD COLUMN is_lecture_notes BOOLEAN DEFAULT 0"))

    # Migrate legacy users.push_subscription into push_subscriptions table
    try:
        legacy_users = await conn.execute(text("SELECT id, push_subscription FROM users WHERE push_subscription IS NOT NULL"))
        for row in legacy_users.fetchall():
            u_id, raw_sub = row[0], row[1]
            if raw_sub:
                try:
                    data = json.loads(raw_sub)
                    endpoint = data.get("endpoint")
                    if endpoint:
                        # Check if already in push_subscriptions
                        existing = await conn.execute(
                            text("SELECT id FROM push_subscriptions WHERE endpoint = :ep"),
                            {"ep": endpoint}
                        )
                        if not existing.fetchone():
                            await conn.execute(
                                text("INSERT INTO push_subscriptions (user_id, endpoint, subscription_json) VALUES (:u, :ep, :js)"),
                                {"u": u_id, "ep": endpoint, "js": raw_sub}
                            )
                except Exception:
                    pass
    except Exception:
        pass

    await _run_once(conn, "2026-09-15_disable_lesson_lead_reminders_default", _disable_lesson_lead_reminders)

async def _run_once(conn, name: str, action):
    """Runs `action(conn)` exactly once, ever, tracked in a tiny
    app_migrations table — for one-off DATA fixes (as opposed to the
    schema/column checks above) that must not re-apply on every restart,
    since that would silently stomp on a user's own later changes to the
    same fields."""
    from sqlalchemy import text
    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    ))
    already_applied = (await conn.execute(text("SELECT 1 FROM app_migrations WHERE name = :n"), {"n": name})).scalar_one_or_none()
    if already_applied:
        return
    await action(conn)
    await conn.execute(text("INSERT INTO app_migrations (name) VALUES (:n)"), {"n": name})

async def _disable_lesson_lead_reminders(conn):
    from sqlalchemy import text
    await conn.execute(text(
        "UPDATE user_notification_settings SET "
        "timetable_before_first_lesson = 0, timetable_before_lesson_end = 0, "
        "timetable_before_break = 0, timetable_before_break_end = 0"
    ))

