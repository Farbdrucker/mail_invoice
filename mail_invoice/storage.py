from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from mail_invoice.config import StorageConfig
from mail_invoice.models import DetectionResult, FetchedEmail, SaveResult

logger = logging.getLogger(__name__)

DB_FILENAME = ".invoice_db.sqlite"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS processed_emails (
    uid TEXT NOT NULL,
    folder TEXT NOT NULL,
    message_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    saved_to TEXT
);
CREATE INDEX IF NOT EXISTS idx_uid_folder ON processed_emails (uid, folder);
"""


def init_db(db_path: Path) -> None:
    """Create the SQLite database and schema if they don't exist."""
    with _open_db(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)


@contextmanager
def _open_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def is_processed(db_path: Path, message_id: str) -> bool:
    """Return True if this message_id is already recorded in the database."""
    with _open_db(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None


def mark_processed(db_path: Path, fetched_email: FetchedEmail, saved_to: Path | None) -> None:
    """Record an email as processed in the SQLite database. Idempotent."""
    now_iso = datetime.now(tz=UTC).isoformat()
    with _open_db(db_path) as conn, conn:
        conn.execute(
            """INSERT OR IGNORE INTO processed_emails
               (uid, folder, message_id, processed_at, saved_to)
               VALUES (?, ?, ?, ?, ?)""",
            (
                fetched_email.uid,
                fetched_email.folder,
                fetched_email.message_id,
                now_iso,
                str(saved_to) if saved_to else None,
            ),
        )


def list_processed(db_path: Path, limit: int = 10) -> list[sqlite3.Row]:
    """Return the most recently processed emails, newest first."""
    with _open_db(db_path) as conn:
        return conn.execute(
            """SELECT uid, folder, message_id, processed_at, saved_to
               FROM processed_emails
               ORDER BY processed_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()


def resolve_db_path(cfg: StorageConfig) -> Path:
    """Return the path to the SQLite tracking database."""
    return Path(cfg.target_dir) / DB_FILENAME


def make_invoice_dir(fetched_email: FetchedEmail, cfg: StorageConfig) -> Path:
    """Compute the target directory path for one email's saved files.

    Does NOT create the directory.
    """
    base = Path(cfg.target_dir)
    if cfg.year_subdirs:
        base = base / fetched_email.date.strftime("%Y")
    if cfg.month_subdirs:
        base = base / fetched_email.date.strftime("%m")
    slug = _make_dir_slug(fetched_email.date, fetched_email.sender, fetched_email.subject)
    return _unique_path(base, slug)


def _make_dir_slug(date: datetime, sender: str, subject: str) -> str:
    date_part = date.strftime("%Y-%m-%d")
    domain = sender.split("@")[1].split(".")[0] if "@" in sender else sender
    domain = re.sub(r"[^\w]", "", domain)[:20].lower()
    subj = unicodedata.normalize("NFKD", subject).encode("ascii", "ignore").decode("ascii")
    subj = re.sub(r"[^\w\s]", "", subj)
    subj = re.sub(r"[\s_]+", "_", subj).strip("_").lower()[:40]
    return f"{date_part}_{domain}_{subj}".rstrip("_")


def _unique_path(parent: Path, slug: str) -> Path:
    candidate = parent / slug
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = parent / f"{slug}_{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_email(
    fetched_email: FetchedEmail,
    detection: DetectionResult,
    cfg: StorageConfig,
) -> SaveResult:
    """Save email.md and valid attachments to a new directory.

    Creates the full directory tree. Raises OSError on write failure.
    """
    invoice_dir = make_invoice_dir(fetched_email, cfg)
    invoice_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []

    md_path = invoice_dir / "email.md"
    md_path.write_text(_generate_markdown(fetched_email, detection), encoding="utf-8")
    saved_files.append("email.md")
    logger.debug("Wrote %s", md_path)

    valid_names = set(detection.valid_attachment_names)
    for att in fetched_email.attachments:
        if att.filename not in valid_names:
            continue
        att_path = invoice_dir / att.filename
        if not att_path.exists():
            att_path.write_bytes(att.content)
            logger.debug("Wrote %s (%d bytes)", att_path, att.size_bytes)
        saved_files.append(att.filename)

    return SaveResult(
        email=fetched_email,
        detection=detection,
        saved_dir=invoice_dir,
        saved_files=tuple(saved_files),
        is_duplicate=False,
    )


def _generate_markdown(fetched_email: FetchedEmail, detection: DetectionResult) -> str:
    """Generate YAML-frontmattered Markdown content for an email. Pure function."""
    date_iso = fetched_email.date.isoformat()
    subject_escaped = fetched_email.subject.replace('"', '\\"')

    lines: list[str] = [
        "---",
        f'uid: "{fetched_email.uid}"',
        f'message_id: "{fetched_email.message_id}"',
        f'date: "{date_iso}"',
        f'sender: "{fetched_email.sender}"',
        f'subject: "{subject_escaped}"',
    ]

    valid_atts = [
        att for att in fetched_email.attachments if att.filename in detection.valid_attachment_names
    ]
    if valid_atts:
        lines.append("attachments:")
        for att in valid_atts:
            lines.append(f'  - name: "{att.filename}"')
            lines.append(f"    size_bytes: {att.size_bytes}")
    else:
        lines.append("attachments: []")

    lines += ["tags: []", "---", ""]

    lines.append(f"# {fetched_email.subject}")
    lines.append("")
    lines += [
        "| Field | Value |",
        "|-------|-------|",
        f"| Date | {date_iso} |",
        f"| From | {fetched_email.sender_display} <{fetched_email.sender}> |",
        f"| UID | {fetched_email.uid} |",
        f"| Message-ID | {fetched_email.message_id} |",
    ]
    if detection.matched_keywords:
        lines.append(f"| Detection Keywords | {', '.join(detection.matched_keywords)} |")
    lines.append("")

    if valid_atts:
        lines.append("## Attachments")
        lines.append("")
        for att in valid_atts:
            lines.append(f"- [{att.filename}](./{att.filename}) ({att.size_bytes:,} bytes)")
        lines.append("")

    lines.append("## Message Body")
    lines.append("")
    body = fetched_email.body_plain.strip() or "(no plain text body)"
    lines.append(body)
    lines.append("")

    return "\n".join(lines)
