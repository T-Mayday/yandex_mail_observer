import logging
from typing import Optional

import requests

from app.config import settings

logger = logging.getLogger("mail_observer.bitrix")


class Bitrix24WebhookConnector:
    def __init__(self):
        self.webhook_url = settings.bitrix_webhook_url.rstrip("/") + "/" if settings.bitrix_webhook_url else ""
        self.admin_id_1 = settings.bitrix_admin_id_1
        self.enabled = settings.bitrix_enabled

    def is_ready(self) -> bool:
        return self.enabled and bool(self.webhook_url)

    @staticmethod
    def _is_flat_payload(payload: dict) -> bool:
        for v in (payload or {}).values():
            if isinstance(v, (dict, list, tuple)):
                return False
        return True

    def _call(self, method: str, params: Optional[dict] = None) -> dict:
        if not self.webhook_url:
            return {"error": "bitrix_not_configured", "error_description": "BITRIX_WEBHOOK_URL не задан"}

        url = f"{self.webhook_url}{method}"
        payload = params or {}
        last_err = None

        try:
            res = requests.post(url, json=payload, timeout=30)
            status = res.status_code
            try:
                data = res.json()
            except ValueError:
                data = None

            if 200 <= status < 300 and isinstance(data, dict):
                return data

            last_err = {
                "error": "http_error" if status >= 300 else "invalid_json",
                "error_description": f"HTTP {status}" if status >= 300 else "Response is not valid JSON",
                "status_code": status,
                "raw": res.text[:2000],
            }
        except requests.RequestException as e:
            last_err = {
                "error": "request_failed",
                "error_description": str(e),
                "status_code": None,
            }

        if self._is_flat_payload(payload):
            try:
                res = requests.post(url, data=payload, timeout=30)
                status = res.status_code
                try:
                    data = res.json()
                except ValueError:
                    data = None

                if 200 <= status < 300 and isinstance(data, dict):
                    return data

                last_err = {
                    "error": "http_error" if status >= 300 else "invalid_json",
                    "error_description": f"HTTP {status}" if status >= 300 else "Response is not valid JSON",
                    "status_code": status,
                    "raw": res.text[:2000],
                }
            except requests.RequestException as e:
                last_err = {
                    "error": "request_failed",
                    "error_description": str(e),
                    "status_code": None,
                }

        return last_err or {"error": "unknown", "error_description": "Unknown error"}

    def _dialog_candidates(self, dialog_id: str | int) -> list[str]:
        s = str(dialog_id).strip()
        if not s:
            return []

        cands = [s]

        if s.isdigit():
            cands.append(f"user{s}")
            cands.append(f"chat{s}")
        else:
            if s.startswith("user") and s[4:].isdigit():
                cands.append(s[4:])
            if s.startswith("chat") and s[4:].isdigit():
                cands.append(s[4:])

        out = []
        seen = set()
        for x in cands:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _send_im_message(self, dialog_id: str | int, msg: str, *, system: str = "N") -> tuple[bool, str]:
        if not self.is_ready():
            return False, "Bitrix отключён или не настроен"

        for dial in self._dialog_candidates(dialog_id):
            res = self._call(
                "im.message.add",
                {
                    "DIALOG_ID": dial,
                    "MESSAGE": msg,
                    "SYSTEM": system,
                    "URL_PREVIEW": "N",
                },
            )

            if res.get("error"):
                logger.error(
                    "BX24 im.message.add failed: DIALOG_ID=%s error=%s error_description=%s status_code=%s raw=%s",
                    dial,
                    res.get("error"),
                    res.get("error_description"),
                    res.get("status_code"),
                    str(res.get("raw") or "")[:500],
                )
                continue

            logger.info("BX24 im.message.add ok: DIALOG_ID=%s result=%s", dial, res.get("result"))
            return True, f"OK: {dial}"

        return False, f"FAILED: dialog_id={dialog_id}"

    def send_msg_user(self, user_id: str | int, msg: str) -> tuple[bool, str]:
        return self._send_im_message(user_id, msg, system="N")

    def send_msg_admin(self, msg: str) -> tuple[bool, str]:
        if not self.admin_id_1:
            return False, "BITRIX_ADMIN_ID_1 не задан"
        return self.send_msg_user(self.admin_id_1, msg)

    def search_users_by_fio(self, query: str, limit: int = 10) -> list[dict]:
        q = (query or "").strip()
        if not q:
            return []

        # Сначала пробуем user.search
        res = self._call("user.search", {"FIND": q})
        items = res.get("result") or []

        # Fallback на user.get по частям ФИО
        if not items:
            parts = q.split()
            payload = {"filter": {}, "select": ["ID", "NAME", "LAST_NAME", "SECOND_NAME", "EMAIL", "ACTIVE", "WORK_POSITION"]}
            if len(parts) >= 1:
                payload["filter"]["LAST_NAME"] = parts[0]
            if len(parts) >= 2:
                payload["filter"]["NAME"] = parts[1]
            if len(parts) >= 3:
                payload["filter"]["SECOND_NAME"] = parts[2]

            res = self._call("user.get", payload)
            items = res.get("result") or []

        out = []
        for u in items:
            if str(u.get("ACTIVE", "")).upper() not in {"Y", "1", "TRUE"}:
                continue

            fio = " ".join(
                x for x in [
                    str(u.get("LAST_NAME") or "").strip(),
                    str(u.get("NAME") or "").strip(),
                    str(u.get("SECOND_NAME") or "").strip(),
                ] if x
            ).strip()

            out.append(
                {
                    "id": str(u.get("ID")),
                    "fio": fio or f"ID {u.get('ID')}",
                    "email": str(u.get("EMAIL") or "").strip(),
                    "position": str(u.get("WORK_POSITION") or "").strip(),
                }
            )

        return out[:limit]