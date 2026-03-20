import email
import imaplib


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