from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./sofia.db")
    vapid_private_key: str = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_public_key: str = os.getenv("VAPID_PUBLIC_KEY", "")
    vapid_claim_email: str = os.getenv("VAPID_CLAIM_EMAIL", "mailto:admin@example.com")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-key")
    encryption_key: str = os.getenv("ENCRYPTION_KEY", "")
    upload_dir: str = os.getenv("UPLOAD_DIR", "./uploads")
    max_file_size: int = int(os.getenv("MAX_FILE_SIZE", "52428800"))
    dev_email: Optional[str] = os.getenv("DEV_EMAIL")

settings = Settings()
