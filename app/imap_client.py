import email
import html
import imaplib
import re


def connect_mail(host: str, port: int, user_email: str, app_password: str):
    mail = imaplib.IMAP4_SSL(host, port, timeout=30)
    mail.login(user_email, app_password)
    mail.select("INBOX")
    return mail


def get_all_uids(mail) -> set[str]:
    status, data = mail.uid("search", None, "ALL")
    if status != "OK":
        return set()

    raw = data[0] or b""
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")

    return set(raw.split())


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
    status, msg_data = mail.uid("fetch", uid, "(RFC822)")
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