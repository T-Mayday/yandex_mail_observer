import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class ProcessedMessageStorage:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def _connect(self):
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
                CREATE INDEX IF NOT EXISTS idx_processed_messages_message_id
                ON processed_messages(message_id)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processed_messages_mailbox
                ON processed_messages(mailbox)
                """
            )

    def load_processed_uids(self, mailbox: str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_uid
                FROM processed_messages
                WHERE mailbox = ?
                """
                ,
                (mailbox,)
            ).fetchall()

        return {row["message_uid"] for row in rows}

    def count_messages(self, mailbox: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM processed_messages
                WHERE mailbox = ?
                """
                ,
                (mailbox,)
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
        now_iso = datetime.now(timezone.utc).isoformat()

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
                """
                ,
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