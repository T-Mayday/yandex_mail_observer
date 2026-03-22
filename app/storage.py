import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ProcessedMessageStorage:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mailbox TEXT NOT NULL,
                    message_uid TEXT NOT NULL,
                    message_id TEXT,
                    subject TEXT,
                    sender TEXT,
                    received_at_raw TEXT,
                    processed_at TEXT NOT NULL,
                    delivery_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(mailbox, message_uid)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bitrix_user_id TEXT NOT NULL UNIQUE,
                    fio TEXT,
                    email TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processed_messages_mailbox
                ON processed_messages(mailbox)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recipients_active
                ON recipients(active)
                """
            )

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---------- processed_messages ----------

    def load_processed_uids(self, mailbox: str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_uid
                FROM processed_messages
                WHERE mailbox = ?
                """,
                (mailbox,),
            ).fetchall()
        return {row["message_uid"] for row in rows}

    def count_messages(self, mailbox: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM processed_messages
                WHERE mailbox = ?
                """,
                (mailbox,),
            ).fetchone()
        return int(row["cnt"])

    def save_processed_message(
        self,
        mailbox: str,
        message_uid: str,
        message_id: str | None,
        subject: str | None,
        sender: str | None,
        received_at_raw: str | None,
        delivery_status: str,
    ) -> bool:
        now_iso = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO processed_messages (
                    mailbox,
                    message_uid,
                    message_id,
                    subject,
                    sender,
                    received_at_raw,
                    processed_at,
                    delivery_status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mailbox,
                    message_uid,
                    message_id,
                    subject,
                    sender,
                    received_at_raw,
                    now_iso,
                    delivery_status,
                    now_iso,
                ),
            )
        return cursor.rowcount > 0

    # ---------- app_settings ----------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ? LIMIT 1",
                (key,),
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        now_iso = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now_iso),
            )

    def get_or_create_setup_token(self) -> str:
        token = self.get_setting("setup_token")
        if token:
            return token
        token = uuid.uuid4().hex
        self.set_setting("setup_token", token)
        return token

    def is_setup_link_sent(self) -> bool:
        return bool(self.get_setting("setup_link_sent_at"))

    def mark_setup_link_sent(self) -> None:
        self.set_setting("setup_link_sent_at", self._now())

    def get_runtime_config(self) -> dict:
        return {
            "yandex_email": (self.get_setting("yandex_email") or "").strip(),
            "yandex_app_password": (self.get_setting("yandex_app_password") or "").strip(),
        }

    def save_runtime_config(self, yandex_email: str, yandex_app_password: str) -> None:
        self.set_setting("yandex_email", (yandex_email or "").strip())
        self.set_setting("yandex_app_password", (yandex_app_password or "").strip())

    def is_runtime_config_ready(self) -> bool:
        cfg = self.get_runtime_config()
        return bool(cfg["yandex_email"] and cfg["yandex_app_password"])

    # ---------- recipients ----------

    def list_recipients(self, active_only: bool = True):
        query = "SELECT * FROM recipients"
        params = []
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY fio COLLATE NOCASE ASC"

        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    def upsert_recipient(self, bitrix_user_id: str, fio: str, email: str | None) -> None:
        now_iso = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recipients (
                    bitrix_user_id, fio, email, active, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(bitrix_user_id) DO UPDATE SET
                    fio = excluded.fio,
                    email = excluded.email,
                    active = 1,
                    updated_at = excluded.updated_at
                """,
                (str(bitrix_user_id), fio, email, now_iso, now_iso),
            )

    def delete_recipient(self, bitrix_user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM recipients WHERE bitrix_user_id = ?",
                (str(bitrix_user_id),),
            )