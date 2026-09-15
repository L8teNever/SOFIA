"""Shared Fernet encrypt/decrypt helpers, extracted out of the Untis-password
code path (backend/routes/timetable.py, backend/routes/classes.py each had
their own copy) now that Google OAuth tokens need the same treatment."""
from backend.config import settings
from cryptography.fernet import Fernet

def _fernet():
    key = settings.encryption_key
    return Fernet(key.encode()) if key else None

def encrypt_value(value: str) -> str:
    f = _fernet()
    return f.encrypt(value.encode()).decode() if f else value

def decrypt_value(value: str) -> str:
    f = _fernet()
    if not f:
        return value
    return f.decrypt(value.encode()).decode()
