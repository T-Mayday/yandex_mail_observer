import imaplib
import logging
import time

from app.config import settings
from app.filters import is_mailing
from app.formatter import decode_mime, format_notification, format_sender
from app.imap_client import (
    build_message_view_link,
    connect_mail,
    extract_text_content,
    extract_text_preview,
    fetch_full_message_by_uid,
    fetch_headers_by_uid,
    get_all_uids,
)
from app.notifier_bitrix import Bitrix24WebhookConnector
from app.notifier_console import notify_console
from app.storage import ProcessedMessageStorage

logger = logging.getLogger("mail_observer")


def setup_logging():
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


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


def build_setup_link(storage: ProcessedMessageStorage) -> str:
    token = storage.get_or_create_setup_token()
    return f"{settings.public_base_url.rstrip('/')}/setup/{token}"


def ensure_setup_link_sent(storage: ProcessedMessageStorage, bx: Bitrix24WebhookConnector):
    if storage.is_runtime_config_ready():
        return

    if storage.is_setup_link_sent():
        return

    if not settings.public_base_url:
        logger.warning("PUBLIC_BASE_URL не задан — ссылку админу отправить нельзя.")
        return

    if not bx.is_ready():
        logger.warning("Bitrix не настроен — ссылку админу отправить нельзя.")
        return

    link = build_setup_link(storage)
    text = (
        "Настройка Mail Observer\n"
        "Откройте ссылку и заполните Яндекс-почту и получателей:\n"
        f"{link}"
    )

    ok, dbg = bx.send_msg_admin(text)
    logger.info("Отправка setup-ссылки админу: ok=%s dbg=%s", ok, dbg)

    if ok:
        storage.mark_setup_link_sent()


def wait_for_runtime_config(storage: ProcessedMessageStorage, bx: Bitrix24WebhookConnector) -> dict:
    while True:
        cfg = storage.get_runtime_config()
        if cfg["yandex_email"] and cfg["yandex_app_password"]:
            return cfg

        ensure_setup_link_sent(storage, bx)
        logger.info("Ожидаю заполнение Yandex-настроек через web UI...")
        time.sleep(10)


def build_letter_link(uid: str) -> str | None:
    """
    Собирает подписанную ссылку на просмотр письма.
    Если не хватает настроек — возвращает None, и formatter даст fallback на mail.yandex.ru
    """
    if not settings.public_base_url:
        logger.warning("PUBLIC_BASE_URL не задан — ссылка на письмо не будет построена.")
        return None

    if not getattr(settings, "mail_link_secret", ""):
        logger.warning("MAIL_LINK_SECRET не задан — ссылка на письмо не будет построена.")
        return None

    try:
        return build_message_view_link(
            base_url=settings.public_base_url,
            uid=uid,
            secret=settings.mail_link_secret,
            mailbox="INBOX",
            ttl_seconds=86400,
        )
    except Exception:
        logger.exception("Не удалось собрать ссылку на письмо uid=%s", uid)
        return None


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

    recipients = storage.list_recipients(active_only=True)

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

        full_message = fetch_full_message_by_uid(mail, uid)
        text_source = full_message if full_message is not None else message
        body_text = extract_text_content(text_source)
        body_preview = extract_text_preview(body_text)

        letter_link = build_letter_link(uid)

        text = format_notification(
            message,
            preview_text=body_preview,
            letter_link=letter_link,
        )
        notify_console(text)

        delivery_status = "console_notified"

        if bx and bx.is_ready() and recipients:
            total = len(recipients)
            success = 0

            for r in recipients:
                ok, dbg = bx.send_msg_user(r["bitrix_user_id"], text)
                logger.info(
                    "Отправка в Bitrix user_id=%s fio=%r ok=%s dbg=%s",
                    r["bitrix_user_id"],
                    r["fio"],
                    ok,
                    dbg,
                )
                if ok:
                    success += 1

            if success == total:
                delivery_status = "bitrix_sent_all"
            elif success > 0:
                delivery_status = "bitrix_sent_partial"
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
    storage = ProcessedMessageStorage(settings.sqlite_db)
    storage.init_db()

    bx = Bitrix24WebhookConnector()

    cfg = wait_for_runtime_config(storage, bx)

    logger.info("Подключаюсь к Яндекс Почте...")
    mail = connect_mail(
        settings.imap_host,
        settings.imap_port,
        cfg["yandex_email"],
        cfg["yandex_app_password"],
    )

    saved_count = storage.count_messages(cfg["yandex_email"])

    if saved_count == 0 and settings.bootstrap_existing:
        known_uids = bootstrap_existing_messages(mail, storage, cfg["yandex_email"])
    else:
        known_uids = storage.load_processed_uids(cfg["yandex_email"])

    logger.info("Наблюдатель запущен. Уже известных UID: %s", len(known_uids))

    try:
        while True:
            try:
                mail.select("INBOX")
                known_uids = process_new_uids(
                    mail=mail,
                    storage=storage,
                    mailbox=cfg["yandex_email"],
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

                cfg = wait_for_runtime_config(storage, bx)
                mail = connect_mail(
                    settings.imap_host,
                    settings.imap_port,
                    cfg["yandex_email"],
                    cfg["yandex_app_password"],
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