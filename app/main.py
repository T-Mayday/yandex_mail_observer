import logging
import time
import imaplib

from config import settings
from filters import is_mailing
from formatter import decode_mime, format_notification, format_sender
from imap_client import connect_mail, fetch_headers_by_uid, get_all_uids
from notifier_console import notify_console
from notifier_bitrix import Bitrix24WebhookConnector
from storage import ProcessedMessageStorage


logger = logging.getLogger("mail_observer")


def setup_logging():
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def validate_settings():
    if not settings.yandex_email:
        raise ValueError("YANDEX_EMAIL не задан в .env")
    if not settings.yandex_app_password:
        raise ValueError("YANDEX_APP_PASSWORD не задан в .env")


def build_message_meta(message):
    return {
        "message_id": decode_mime(message.get("Message-ID", "")) or None,
        "subject": decode_mime(message.get("Subject", "")) or None,
        "sender": format_sender(message.get("From", "")) or None,
        "received_at_raw": message.get("Date", "") or None,
    }


def bootstrap_existing_messages(mail, storage: ProcessedMessageStorage, mailbox: str):
    current_uids = sorted(get_all_uids(mail), key=int)

    if not current_uids:
        logger.info("Во входящих нет писем для bootstrap.")
        return set()

    logger.info("Первый запуск: заношу текущие письма в SQLite без уведомлений...")

    processed = set()
    for uid in current_uids:
        message = fetch_headers_by_uid(mail, uid)
        if message:
            meta = build_message_meta(message)
            storage.save_processed_message(
                mailbox=mailbox,
                message_uid=uid,
                message_id=meta["message_id"],
                subject=meta["subject"],
                sender=meta["sender"],
                received_at_raw=meta["received_at_raw"],
                delivery_status="bootstrap",
            )
        else:
            storage.save_processed_message(
                mailbox=mailbox,
                message_uid=uid,
                message_id=None,
                subject=None,
                sender=None,
                received_at_raw=None,
                delivery_status="bootstrap_no_headers",
            )

        processed.add(uid)

    logger.info("Bootstrap завершён. Занесено писем: %s", len(processed))
    return processed


def process_new_uids(
    mail,
    storage: ProcessedMessageStorage,
    mailbox: str,
    known_uids: set[str],
    bx: Bitrix24WebhookConnector | None = None,
) -> set[str]:
    current_uids = get_all_uids(mail)
    new_uids = current_uids - known_uids

    if not new_uids:
        return known_uids

    for uid in sorted(new_uids, key=int):
        message = fetch_headers_by_uid(mail, uid)

        known_uids.add(uid)

        if not message:
            storage.save_processed_message(
                mailbox=mailbox,
                message_uid=uid,
                message_id=None,
                subject=None,
                sender=None,
                received_at_raw=None,
                delivery_status="fetch_error",
            )
            logger.warning("Не удалось получить заголовки письма uid=%s", uid)
            continue

        meta = build_message_meta(message)

        if is_mailing(message):
            storage.save_processed_message(
                mailbox=mailbox,
                message_uid=uid,
                message_id=meta["message_id"],
                subject=meta["subject"],
                sender=meta["sender"],
                received_at_raw=meta["received_at_raw"],
                delivery_status="skipped_mailing",
            )
            logger.info("Пропущена рассылка uid=%s subject=%r", uid, meta["subject"])
            continue

        text = format_notification(message)
        notify_console(text)

        delivery_status = "console_notified"

        if bx and bx.is_ready():
            ok, dbg = bx.send_msg(text)
            logger.info("Отправка в Bitrix: ok=%s dbg=%s", ok, dbg)

            if ok:
                delivery_status = "bitrix_sent"
            else:
                delivery_status = "bitrix_failed"

        storage.save_processed_message(
            mailbox=mailbox,
            message_uid=uid,
            message_id=meta["message_id"],
            subject=meta["subject"],
            sender=meta["sender"],
            received_at_raw=meta["received_at_raw"],
            delivery_status=delivery_status,
        )

        logger.info("Обработано новое письмо uid=%s subject=%r", uid, meta["subject"])

    return known_uids


def watch_mail():
    validate_settings()

    storage = ProcessedMessageStorage(settings.sqlite_db)
    storage.init_db()

    bx = Bitrix24WebhookConnector()

    logger.info("Подключаюсь к Яндекс Почте...")
    mail = connect_mail(
        settings.imap_host,
        settings.imap_port,
        settings.yandex_email,
        settings.yandex_app_password,
    )

    saved_count = storage.count_messages(settings.yandex_email)

    if saved_count == 0 and settings.bootstrap_existing:
        known_uids = bootstrap_existing_messages(mail, storage, settings.yandex_email)
    else:
        known_uids = storage.load_processed_uids(settings.yandex_email)

    logger.info("Наблюдатель запущен. Уже известных UID: %s", len(known_uids))

    try:
        while True:
            try:
                mail.select("INBOX")
                known_uids = process_new_uids(
                    mail=mail,
                    storage=storage,
                    mailbox=settings.yandex_email,
                    known_uids=known_uids,
                    bx=bx,
                )
                time.sleep(settings.check_interval)

            except imaplib.IMAP4.abort:
                logger.warning("Соединение с IMAP оборвалось. Переподключаюсь...")
                try:
                    mail.logout()
                except Exception:
                    pass

                time.sleep(3)
                mail = connect_mail(
                    settings.imap_host,
                    settings.imap_port,
                    settings.yandex_email,
                    settings.yandex_app_password,
                )

            except Exception as e:
                logger.exception("Ошибка в цикле наблюдения: %s", e)
                time.sleep(settings.check_interval)

    except KeyboardInterrupt:
        logger.info("Наблюдатель остановлен пользователем.")

    finally:
        try:
            mail.close()
        except Exception:
            pass

        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    setup_logging()
    watch_mail()