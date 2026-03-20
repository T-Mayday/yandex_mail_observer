# Форматы сообщения

from datetime import datetime
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime


def decode_mime(value: str) -> str:
    if not value:
        return ""

    parts = decode_header(value)
    result = []

    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)

    return "".join(result).strip()


def normalize_spaces(text: str) -> str:
    return " ".join((text or "").split()).strip()


def format_sender(from_header: str) -> str:
    decoded = decode_mime(from_header)
    name, addr = parseaddr(decoded)

    name = normalize_spaces(name)
    addr = normalize_spaces(addr)

    if name and addr:
        return f"{name} <{addr}>"
    if addr:
        return addr
    return decoded or "(неизвестный отправитель)"


def format_date_ru(date_str: str) -> str:
    if not date_str:
        return "Дата неизвестна"

    months = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }

    try:
        dt = parsedate_to_datetime(date_str)

        if dt.tzinfo is not None:
            dt = dt.astimezone()
        else:
            dt = dt.astimezone()

        now = datetime.now().astimezone()
        today = now.date()
        msg_date = dt.date()

        time_part = dt.strftime("%H:%M")
        full_part = f"{dt.day} {months[dt.month]} {dt.year} в {time_part}"

        if msg_date == today:
            return f"Сегодня в {time_part}"
        if (today - msg_date).days == 1:
            return f"Вчера в {time_part}"

        return full_part

    except Exception:
        return date_str


def format_notification(message) -> str:
    subject = decode_mime(message.get("Subject")) or "(без темы)"
    sender = format_sender(message.get("From", ""))
    date_ru = format_date_ru(message.get("Date", ""))

    return (
        "📩 Новое письмо\n"
        f"Тема: {subject}\n"
        f"От: {sender}\n"
        f"Когда: {date_ru}\n"
        "Открыть почту: https://mail.yandex.ru"
    )