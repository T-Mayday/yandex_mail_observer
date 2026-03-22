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
    imap_host: str
    imap_port: int
    check_interval: int
    sqlite_db: str
    bootstrap_existing: bool
    log_level: str

    bitrix_webhook_url: str
    bitrix_admin_id_1: str
    bitrix_enabled: bool

    public_base_url: str
    web_host: str
    web_port: int


settings = Settings(
    imap_host=os.getenv("IMAP_HOST", "imap.yandex.com").strip(),
    imap_port=int(os.getenv("IMAP_PORT", "993")),
    check_interval=int(os.getenv("CHECK_INTERVAL", "10")),
    sqlite_db=os.getenv("SQLITE_DB", "/app/data/mail_observer.db").strip(),
    bootstrap_existing=to_bool(os.getenv("BOOTSTRAP_EXISTING"), True),
    log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),

    bitrix_webhook_url=os.getenv("BITRIX_WEBHOOK_URL", "").strip(),
    bitrix_admin_id_1=os.getenv("BITRIX_ADMIN_ID_1", "").strip(),
    bitrix_enabled=to_bool(os.getenv("BITRIX_ENABLED"), False),

    public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip(),
    web_host=os.getenv("WEB_HOST", "0.0.0.0").strip(),
    web_port=int(os.getenv("WEB_PORT", "8080")),
)