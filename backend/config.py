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

def _load_or_generate_encryption_key() -> str:
    """Same pattern as _load_or_generate_vapid(): use ENCRYPTION_KEY from the
    environment if it's actually usable, otherwise fall back to a key saved
    on the persisted data volume (generating one on first run). A Fernet key
    has to be exactly 32 url-safe base64-encoded bytes — an env var set to
    anything else (wrong length, not base64, ...) used to only surface as a
    crash the moment someone tried to save Untis credentials, since nothing
    validated it up front. Validating here means a bad env var degrades to
    "ignored, self-healed" instead of "silently broken until someone
    notices the 500 in the logs"."""
    from cryptography.fernet import Fernet

    key = os.getenv("ENCRYPTION_KEY", "")
    if key:
        try:
            Fernet(key.encode())
            return key
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "ENCRYPTION_KEY is set but isn't a valid Fernet key (must be "
                "32 url-safe base64-encoded bytes) — ignoring it and using/"
                "generating a local key on the data volume instead."
            )

    key_file = os.path.join(_data_dir(), "encryption_key.txt")
    if os.path.exists(key_file):
        with open(key_file) as f:
            saved = f.read().strip()
        if saved:
            return saved

    new_key = Fernet.generate_key().decode()
    os.makedirs(_data_dir(), exist_ok=True)
    with open(key_file, "w") as f:
        f.write(new_key)
    return new_key

_encryption_key = _load_or_generate_encryption_key()

def _load_or_generate_internal_token() -> str:
    """Same self-healing pattern as the VAPID/encryption keys above — used
    to authenticate the MCP bridge container's server-to-server calls back
    into this API (see backend/auth.py). That container has no
    Cf-Access-Authenticated-User-Email header to present (it calls over the
    private Docker network, never through the public Cloudflare hostname),
    so it proves itself with this shared secret instead. Persisted on the
    same ./data volume the MCP container mounts read-only, so both sides
    always agree without needing the token copied into an env var by hand."""
    import secrets

    token = os.getenv("INTERNAL_SERVICE_TOKEN", "")
    if token:
        return token

    token_file = os.path.join(_data_dir(), "internal_service_token.txt")
    if os.path.exists(token_file):
        with open(token_file) as f:
            saved = f.read().strip()
        if saved:
            return saved

    new_token = secrets.token_urlsafe(32)
    os.makedirs(_data_dir(), exist_ok=True)
    with open(token_file, "w") as f:
        f.write(new_token)
    return new_token

_internal_service_token = _load_or_generate_internal_token()

class Settings:
    database_url:      str           = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/sofia.db")
    vapid_private_key: str           = _vapid_priv
    vapid_public_key:  str           = _vapid_pub
    vapid_claim_email: str           = os.getenv("VAPID_CLAIM_EMAIL", "mailto:admin@example.com")
    secret_key:        str           = os.getenv("SECRET_KEY", "dev-secret-key")
    encryption_key:    str           = _encryption_key
    internal_service_token: str      = _internal_service_token
    mcp_default_user_email: str      = os.getenv("MCP_DEFAULT_USER_EMAIL", "l8tenever@gmail.com")
    upload_dir:        str           = os.getenv("UPLOAD_DIR", "./uploads")
    # Deliberately NOT under upload_dir: that whole tree is served publicly
    # and unauthenticated at /uploads/... (see backend/main.py's StaticFiles
    # mount) — a Drive file's raw bytes must only ever be reachable through
    # the API routes in backend/routes/drive.py, which check the requesting
    # user's class membership before serving anything. Reuses the same
    # directory the self-healing keys already live in (backend/config.py's
    # _data_dir()) since that volume is already persisted but never mounted
    # as a static route anywhere.
    drive_storage_dir: str           = os.getenv("DRIVE_STORAGE_DIR", os.path.join(_data_dir(), "drive_files"))
    # Same reasoning as drive_storage_dir above — chat attachments (images,
    # files, voice notes) must only ever be reachable through the
    # participant-checked routes in backend/routes/chat.py, never a public
    # static mount. Note: uploads/chat/ already exists on disk as an unused
    # leftover directory — deliberately not reused, since it sits under the
    # publicly-mounted upload_dir.
    chat_storage_dir:  str           = os.getenv("CHAT_STORAGE_DIR", os.path.join(_data_dir(), "chat_files"))
    max_file_size:     int           = int(os.getenv("MAX_FILE_SIZE", "1073741824"))  # 1 GB
    dev_email:         Optional[str] = os.getenv("DEV_EMAIL")
    gemini_api_key:    str           = os.getenv("GEMINI_API_KEY", "")
    gemini_model:      str           = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    # Chat's GIF picker (GIPHY). Must come from a free key a human creates
    # on developers.giphy.com — no way to self-generate one. (Tenor was the
    # original choice here but stopped accepting new API clients in January
    # 2026.) The route checks for an empty string and reports "not
    # configured" rather than failing, same convention as the Gemini/
    # Google-OAuth keys above.
    giphy_api_key:     str           = os.getenv("GIPHY_API_KEY", "")
    # Google OAuth ("Google Sync": push calendar events / homework to each
    # user's own Google Calendar & Tasks). Must come from a Google Cloud
    # Console project a human sets up — there's no way to self-generate a
    # valid OAuth client, unlike the self-healing keys above. Every route
    # that needs these checks for an empty string and reports "not
    # configured" instead of failing, same as the Gemini key.
    google_client_id:     str        = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str        = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_redirect_uri:  str        = os.getenv("GOOGLE_REDIRECT_URI", "")
    impressum_business_name: str     = os.getenv("IMPRESSUM_BUSINESS_NAME", "Sofia Schulbegleiter PWA")
    impressum_name:    str           = os.getenv("IMPRESSUM_NAME", "Max Mustermann")
    impressum_address: str           = os.getenv("IMPRESSUM_ADDRESS", "Musterstraße 123<br>12345 Musterstadt")
    impressum_phone:   str           = os.getenv("IMPRESSUM_PHONE", "+49 (0) 123 456789")
    impressum_email:   str           = os.getenv("IMPRESSUM_EMAIL", "support@sofia.schule")

settings = Settings()
