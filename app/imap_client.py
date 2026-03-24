import email
import hashlib
import hmac
import html
import imaplib
import re
import time
from email.header import decode_header
from email.utils import parseaddr
from urllib.parse import quote, quote_plus


def connect_mail(
    host: str,
    port: int,
    user_email: str,
    app_password: str,
    mailbox: str = "INBOX",
    readonly: bool = True,
):
    mail = imaplib.IMAP4_SSL(host, port, timeout=30)
    mail.login(user_email, app_password)
    mail.select(mailbox, readonly=readonly)
    return mail


def get_all_uids(mail) -> set[str]:
    status, data = mail.uid("search", None, "ALL")
    if status != "OK":
        return set()

    raw = data[0] or b""
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")

    return set(x for x in raw.split() if x)


def fetch_headers_by_uid(mail, uid: str):
    status, msg_data = mail.uid("fetch", uid, "(BODY.PEEK[HEADER])")
    if status != "OK" or not msg_data:
        return None

    header_bytes = None
    for item in msg_data:
        if isinstance(item, tuple):
            header_bytes = item[1]
            break

    if not header_bytes:
        return None

    return email.message_from_bytes(header_bytes)


def fetch_full_message_by_uid(mail, uid: str):
    status, msg_data = mail.uid("fetch", uid, "(BODY.PEEK[])")
    if status != "OK" or not msg_data:
        return None

    raw_email = None
    for item in msg_data:
        if isinstance(item, tuple):
            raw_email = item[1]
            break

    if not raw_email:
        return None

    return email.message_from_bytes(raw_email)


def _decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")


def _strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</p>", "\n", value)
    value = re.sub(r"(?is)<.*?>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip()


def extract_text_content(message) -> str:
    text_plain_parts = []
    text_html_parts = []

    if message.is_multipart():
        for part in message.walk():
            content_type = (part.get_content_type() or "").lower()
            disposition = (part.get("Content-Disposition") or "").lower()

            if "attachment" in disposition:
                continue

            if content_type == "text/plain":
                text = _decode_payload(part)
                if text.strip():
                    text_plain_parts.append(text)

            elif content_type == "text/html":
                text = _decode_payload(part)
                if text.strip():
                    text_html_parts.append(text)
    else:
        content_type = (message.get_content_type() or "").lower()

        if content_type == "text/plain":
            text_plain_parts.append(_decode_payload(message))
        elif content_type == "text/html":
            text_html_parts.append(_decode_payload(message))

    if text_plain_parts:
        text = "\n\n".join(text_plain_parts)
    elif text_html_parts:
        text = "\n\n".join(_strip_html(x) for x in text_html_parts)
    else:
        text = ""

    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_text_preview(text: str, max_len: int = 400) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    if len(text) <= max_len:
        return text

    return text[:max_len].rstrip() + "…"


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""

    parts = decode_header(value)
    result = []

    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            decoded = None
            for charset in (enc, "utf-8", "cp1251", "koi8-r", "latin1"):
                if not charset:
                    continue
                try:
                    decoded = chunk.decode(charset, errors="replace")
                    break
                except Exception:
                    pass
            if decoded is None:
                decoded = chunk.decode("utf-8", errors="replace")
            result.append(decoded)
        else:
            result.append(chunk)

    return "".join(result).strip()


def decode_address_header(value: str | None) -> str:
    if not value:
        return ""

    name, addr = parseaddr(value)
    name = decode_mime_header(name)

    if name and addr:
        return f"{name} <{addr}>"
    return addr or name or ""


def extract_message_meta(message, uid: str = "") -> dict:
    subject = decode_mime_header(message.get("Subject"))
    from_value = decode_address_header(message.get("From"))
    to_value = decode_address_header(message.get("To"))
    date_value = decode_mime_header(message.get("Date"))
    message_id = decode_mime_header(message.get("Message-ID"))
    text = extract_text_content(message)
    preview = extract_text_preview(text, max_len=500)

    return {
        "uid": uid,
        "subject": subject,
        "from": from_value,
        "to": to_value,
        "date": date_value,
        "message_id": message_id,
        "text": text,
        "preview": preview,
    }


def _build_signature(secret: str, uid: str, mailbox: str, expires: int) -> str:
    payload = f"{uid}|{mailbox}|{expires}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def build_message_view_link(
    base_url: str,
    uid: str,
    secret: str,
    mailbox: str = "INBOX",
    ttl_seconds: int = 86400,
) -> str:
    base_url = (base_url or "").rstrip("/")
    expires = int(time.time()) + int(ttl_seconds)
    sig = _build_signature(secret, uid, mailbox, expires)

    return (
        f"{base_url}/mail/{quote(str(uid), safe='')}"
        f"?mb={quote_plus(str(mailbox))}&e={expires}&s={sig}"
    )


def verify_message_view_link(
    uid: str,
    mailbox: str,
    expires: str,
    signature: str,
    secret: str,
) -> bool:
    if not str(uid).isdigit():
        return False

    if mailbox != "INBOX":
        return False

    try:
        expires_int = int(expires)
    except Exception:
        return False

    if expires_int < int(time.time()):
        return False

    expected = _build_signature(secret, uid, mailbox, expires_int)
    return hmac.compare_digest(expected, signature or "")