from typing import Optional
from dotenv import load_dotenv
import os, json, base64

load_dotenv()

def _data_dir() -> str:
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/sofia.db")
    path = db_url.replace("sqlite+aiosqlite:///", "")
    return os.path.dirname(path) or "./data"

def _pem_to_base64url(pem_str: str) -> str:
    """Convert EC PEM private key to base64url raw bytes (what pywebpush expects)."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    key = load_pem_private_key(pem_str.encode(), password=None)
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _load_or_generate_vapid():
    priv = os.getenv("VAPID_PRIVATE_KEY", "")
    pub  = os.getenv("VAPID_PUBLIC_KEY",  "")
    if priv and pub:
        # Migrate PEM from env var if needed
        if priv.startswith("-----"):
            priv = _pem_to_base64url(priv)
        return priv, pub

    keys_file = os.path.join(_data_dir(), "vapid_keys.json")
    if os.path.exists(keys_file):
        with open(keys_file) as f:
            d = json.load(f)
        priv_key = d["private"]
        # Migrate old PEM format to base64url raw bytes
        if priv_key.startswith("-----"):
            priv_key = _pem_to_base64url(priv_key)
            with open(keys_file, "w") as f:
                json.dump({"private": priv_key, "public": d["public"]}, f)
        return priv_key, d["public"]

    # Auto-generate — store private key as base64url raw bytes (pywebpush standard format)
    from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key     = generate_private_key(SECP256R1())
    priv_raw = key.private_numbers().private_value.to_bytes(32, "big")
    priv_b64 = base64.urlsafe_b64encode(priv_raw).decode().rstrip("=")
    pub_raw  = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pub_b64  = base64.urlsafe_b64encode(pub_raw).decode().rstrip("=")

    os.makedirs(_data_dir(), exist_ok=True)
    with open(keys_file, "w") as f:
        json.dump({"private": priv_b64, "public": pub_b64}, f)

    return priv_b64, pub_b64

_vapid_priv, _vapid_pub = _load_or_generate_vapid()

class Settings:
    database_url:      str           = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/sofia.db")
    vapid_private_key: str           = _vapid_priv
    vapid_public_key:  str           = _vapid_pub
    vapid_claim_email: str           = os.getenv("VAPID_CLAIM_EMAIL", "mailto:admin@example.com")
    secret_key:        str           = os.getenv("SECRET_KEY", "dev-secret-key")
    encryption_key:    str           = os.getenv("ENCRYPTION_KEY", "")
    upload_dir:        str           = os.getenv("UPLOAD_DIR", "./uploads")
    max_file_size:     int           = int(os.getenv("MAX_FILE_SIZE", "52428800"))
    dev_email:         Optional[str] = os.getenv("DEV_EMAIL")

settings = Settings()
