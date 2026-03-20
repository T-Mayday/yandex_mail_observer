# фильтры писем, чтобы не было рассылок

from app.formatter import format_sender


def is_mailing(message) -> bool:
    if message.get("List-Unsubscribe"):
        return True

    if message.get("List-Id"):
        return True

    precedence = (message.get("Precedence") or "").lower().strip()
    if precedence in {"bulk", "list", "junk"}:
        return True

    auto_submitted = (message.get("Auto-Submitted") or "").lower().strip()
    if auto_submitted and auto_submitted != "no":
        return True

    sender = format_sender(message.get("From", "")).lower()

    sender_markers = [
        "noreply",
        "no-reply",
        "do-not-reply",
        "donotreply",
        "mailer-daemon",
        "newsletter",
        "news@",
        "notifications@",
    ]

    if any(marker in sender for marker in sender_markers):
        return True

    return False