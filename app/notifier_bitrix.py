import logging
from typing import Optional

import requests

from app.config import settings

logger = logging.getLogger("mail_observer.bitrix")


class Bitrix24WebhookConnector:
    """
    Коннектор к Bitrix24 через входящий webhook.
    Настройки берутся из .env через app.config.settings
    """

    def __init__(self):
        self.webhook_url = settings.bitrix_webhook_url.rstrip("/") + "/" if settings.bitrix_webhook_url else ""
        self.chat_id = settings.bitrix_chat_id
        self.chatadm_id_1 = settings.bitrix_admin_id_1
        self.chatadm_id_2 = settings.bitrix_admin_id_2
        self.address = settings.bitrix_address
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
            return {
                "error": "bitrix_not_configured",
                "error_description": "BITRIX_WEBHOOK_URL не задан",
            }

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
                    "BX24 im.message.add failed: DIALOG_ID=%s err=%s raw=%s",
                    dial,
                    res.get("error_description") or res.get("error"),
                    str(res.get("raw") or "")[:200],
                )
                continue

            logger.info("BX24 im.message.add ok: DIALOG_ID=%s result=%s", dial, res.get("result"))
            return True, f"OK: {dial}"

        return False, f"FAILED: dialog_id={dialog_id}"

    def send_msg(self, msg: str) -> tuple[bool, str]:
        logger.info("send_msg -> %s", msg.replace("\n", " | "))
        if not self.chat_id:
            return False, "BITRIX_CHAT_ID не задан"
        return self._send_im_message(self.chat_id, msg, system="N")

    def send_msg_error(self, msg: str) -> tuple[bool, str]:
        logger.error("send_msg_error -> %s", msg.replace("\n", " | "))
        if not self.chat_id:
            return False, "BITRIX_CHAT_ID не задан"
        return self._send_im_message(self.chat_id, msg, system="N")

    def send_msg_adm(self, msg: str) -> list[tuple[str, bool, str]]:
        results = []

        for admin_id in [self.chatadm_id_1, self.chatadm_id_2]:
            if not admin_id:
                continue
            ok, dbg = self._send_im_message(admin_id, msg, system="N")
            results.append((admin_id, ok, dbg))

        return results

    def send_msg_user(self, user_id: str | int, msg: str) -> tuple[bool, str]:
        return self._send_im_message(user_id, msg, system="N")

    def get_address(self) -> str:
        return self.address