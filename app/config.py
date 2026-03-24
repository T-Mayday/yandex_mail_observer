import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    yandex_email: str
    yandex_app_password: str
    imap_host: str
    imap_port: int
    check_interval: int
    sqlite_db: str
    bootstrap_existing: bool
    log_level: str

    bitrix_webhook_url: str
    bitrix_chat_id: str
    bitrix_admin_id_1: str
    bitrix_admin_id_2: str
    bitrix_address: str
    bitrix_enabled: bool

    public_base_url: str
    web_host: str
    web_port: int
    mail_link_secret: str

    web_session_secret: str
    admin_login_code_ttl_seconds: int
    admin_session_ttl_seconds: int
    admin_max_code_attempts: int
    cookie_secure: bool


settings = Settings(
    yandex_email=os.getenv("YANDEX_EMAIL", "").strip(),
    yandex_app_password=os.getenv("YANDEX_APP_PASSWORD", "").strip(),
    imap_host=os.getenv("IMAP_HOST", "imap.yandex.com").strip(),
    imap_port=int(os.getenv("IMAP_PORT", "993")),
    check_interval=int(os.getenv("CHECK_INTERVAL", "10")),
    sqlite_db=os.getenv("SQLITE_DB", "/app/data/mail_observer.db").strip(),
    bootstrap_existing=to_bool(os.getenv("BOOTSTRAP_EXISTING"), True),
    log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),

    bitrix_webhook_url=os.getenv("BITRIX_WEBHOOK_URL", "").strip(),
    bitrix_chat_id=os.getenv("BITRIX_CHAT_ID", "").strip(),
    bitrix_admin_id_1=os.getenv("BITRIX_ADMIN_ID_1", "").strip(),
    bitrix_admin_id_2=os.getenv("BITRIX_ADMIN_ID_2", "").strip(),
    bitrix_address=os.getenv("BITRIX_ADDRESS", "").strip(),
    bitrix_enabled=to_bool(os.getenv("BITRIX_ENABLED"), False),

    public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip(),
    web_host=os.getenv("WEB_HOST", "0.0.0.0").strip(),
    web_port=int(os.getenv("WEB_PORT", "8080")),
    mail_link_secret=os.getenv("MAIL_LINK_SECRET", "").strip(),

    web_session_secret=os.getenv("WEB_SESSION_SECRET", "").strip(),
    admin_login_code_ttl_seconds=int(os.getenv("ADMIN_LOGIN_CODE_TTL_SECONDS", "300")),
    admin_session_ttl_seconds=int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "1800")),
    admin_max_code_attempts=int(os.getenv("ADMIN_MAX_CODE_ATTEMPTS", "5")),
    cookie_secure=to_bool(os.getenv("COOKIE_SECURE"), False),
)