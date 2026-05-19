from __future__ import annotations

import email
import email.message
import email.parser
import email.policy
import email.utils
import imaplib
import logging
import re
import unicodedata
from contextlib import suppress
from datetime import UTC, date, datetime
from types import TracebackType

from mail_invoice.config import MailConfig
from mail_invoice.models import EmailAttachment, FetchedEmail

logger = logging.getLogger(__name__)


class IMAPClient:
    """Manages a single IMAP session. Use as a context manager."""

    def __init__(self, cfg: MailConfig, password: str) -> None:
        self._cfg = cfg
        self._password = password
        self._conn: imaplib.IMAP4_SSL | imaplib.IMAP4 | None = None

    def __enter__(self) -> IMAPClient:
        logger.debug("Connecting to %s:%d", self._cfg.host, self._cfg.port)
        if self._cfg.use_ssl:
            self._conn = imaplib.IMAP4_SSL(self._cfg.host, self._cfg.port)
        else:
            self._conn = imaplib.IMAP4(self._cfg.host, self._cfg.port)
        self._conn.login(self._cfg.username, self._password)
        logger.debug("Logged in as %s", self._cfg.username)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if self._conn is not None:
            with suppress(Exception):
                self._conn.close()
            with suppress(Exception):
                self._conn.logout()
            self._conn = None
        return False

    @property
    def _connection(self) -> imaplib.IMAP4_SSL | imaplib.IMAP4:
        if self._conn is None:
            msg = "IMAPClient used outside of context manager"
            raise RuntimeError(msg)
        return self._conn

    def select_folder(self, folder: str) -> int:
        """Select a mailbox folder. Returns message count."""
        status, data = self._connection.select(f'"{folder}"')
        if status != "OK":
            msg = f"SELECT {folder!r} failed: {data}"
            raise imaplib.IMAP4.error(msg)
        return int(data[0]) if data[0] else 0

    def fetch_uids(self, since: date | None = None, *, unseen_only: bool = True) -> list[str]:
        """Return UIDs matching the search criteria without marking messages as read.

        If `unseen_only` is True (default), only UNSEEN messages are returned.
        If `since` is given, only messages on or after that date are returned.
        """
        criteria: list[str] = ["UNSEEN" if unseen_only else "ALL"]
        if since is not None:
            criteria += ["SINCE", _imap_date(since)]
        status, data = self._connection.uid("SEARCH", *criteria)  # type: ignore[arg-type]
        if status != "OK":
            msg = f"UID SEARCH failed: {data}"
            raise imaplib.IMAP4.error(msg)
        raw = data[0]
        if not raw:
            return []
        return [u for u in raw.decode().split() if u]

    def fetch_email_by_uid(self, uid: str, folder: str) -> FetchedEmail:
        """Fetch a single email using BODY.PEEK[] (does not auto-mark as read)."""
        status, data = self._connection.uid("FETCH", uid, "(BODY.PEEK[])")  # type: ignore[arg-type]
        if status != "OK":
            msg = f"FETCH uid={uid} failed: {data}"
            raise imaplib.IMAP4.error(msg)
        raw_bytes = _extract_raw_email(data)
        if raw_bytes is None:
            msg = f"Empty FETCH response for UID {uid}"
            raise ValueError(msg)
        return _parse_email(raw_bytes, uid, folder)

    def mark_as_read(self, uid: str) -> None:
        """Set the \\Seen flag on the given UID. Logs a warning on failure."""
        try:
            status, data = self._connection.uid("STORE", uid, "+FLAGS", r"(\Seen)")  # type: ignore[arg-type]
            if status != "OK":
                logger.warning("Failed to mark UID %s as read: %s", uid, data)
        except Exception as exc:
            logger.warning("Exception marking UID %s as read: %s", uid, exc)


_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_date(d: date) -> str:
    """Format a date as DD-Mon-YYYY for IMAP SEARCH SINCE, locale-independently."""
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


def _extract_raw_email(fetch_data: list[object]) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _parse_email(raw_bytes: bytes, uid: str, folder: str) -> FetchedEmail:
    """Parse raw email bytes into a FetchedEmail dataclass."""
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(raw_bytes)

    date_str = msg["date"]
    try:
        parsed_date = email.utils.parsedate_to_datetime(str(date_str))
    except Exception:
        parsed_date = datetime.now(tz=UTC)
        logger.warning("Could not parse date header for UID %s, using now()", uid)

    from_header = str(msg["from"] or "")
    sender_display, sender_addr = email.utils.parseaddr(from_header)
    sender_addr = sender_addr.lower().strip() or from_header.strip()

    subject = str(msg["subject"] or "(no subject)")
    message_id = str(msg["message-id"] or f"uid-{uid}-{folder}").strip()

    body_plain_part = msg.get_body(preferencelist=("plain",))  # type: ignore[union-attr]
    body_html_part = msg.get_body(preferencelist=("html",))  # type: ignore[union-attr]

    body_plain = _safe_get_text(body_plain_part) if body_plain_part else ""
    body_html = _safe_get_text(body_html_part) if body_html_part else ""

    attachments = tuple(
        _parse_attachment(part)
        for part in msg.iter_attachments()  # type: ignore[union-attr]
        if part.get_filename()
    )

    return FetchedEmail(
        uid=uid,
        folder=folder,
        message_id=message_id,
        date=parsed_date,
        sender=sender_addr,
        sender_display=sender_display,
        subject=subject,
        body_plain=body_plain,
        body_html=body_html,
        attachments=attachments,
    )


def _safe_get_text(part: email.message.Message) -> str:
    """Decode a text message part, tolerating unknown or malformed charset labels."""
    raw = part.get_payload(decode=True)
    if not isinstance(raw, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _parse_attachment(part: email.message.Message) -> EmailAttachment:
    original_filename = part.get_filename() or "attachment"
    content_type = part.get_content_type()
    main_type = part.get_content_maintype()
    if main_type == "text":
        content = _safe_get_text(part).encode("utf-8", errors="replace")
    else:
        raw = part.get_payload(decode=True)
        content = raw if isinstance(raw, bytes) else b""
    return EmailAttachment(
        filename=_safe_filename(original_filename),
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=len(content),
        content=content,
    )


def _safe_filename(name: str, max_len: int = 80) -> str:
    """Sanitize a filename for filesystem use, preserving the extension."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    stem, _, ext = name.rpartition(".")
    if stem:
        stem = re.sub(r"[^\w\s\-]", "_", stem)
        stem = re.sub(r"[\s_]+", "_", stem).strip("_.-")
        ext_clean = re.sub(r"[^\w]", "", ext).lower()
        result = f"{stem}.{ext_clean}" if ext_clean else stem
    else:
        result = re.sub(r"[^\w\s\-.]", "_", name)
        result = re.sub(r"[\s_]+", "_", result).strip("_.-")
    return result[:max_len] or "attachment"
