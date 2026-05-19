from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    original_filename: str
    content_type: str
    size_bytes: int
    content: bytes = field(repr=False, compare=False)


@dataclass(frozen=True)
class FetchedEmail:
    uid: str
    folder: str
    message_id: str
    date: datetime
    sender: str
    sender_display: str
    subject: str
    body_plain: str
    body_html: str
    attachments: tuple[EmailAttachment, ...]


@dataclass(frozen=True)
class DetectionResult:
    is_invoice: bool
    subject_matched: bool
    body_matched: bool
    matched_keywords: tuple[str, ...]
    has_valid_attachments: bool
    valid_attachment_names: tuple[str, ...]


@dataclass(frozen=True)
class SaveResult:
    email: FetchedEmail
    detection: DetectionResult
    saved_dir: Path
    saved_files: tuple[str, ...]
    is_duplicate: bool


@dataclass(frozen=True)
class RunStats:
    total_unread: int
    already_processed: int
    not_invoices: int
    detected_invoices: int
    saved_successfully: int
    errors: int
    dry_run: bool
