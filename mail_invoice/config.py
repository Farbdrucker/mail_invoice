from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

_DEFAULT_SUBJECT_KEYWORDS: tuple[str, ...] = (
    "invoice",
    "rechnung",
    "bill",
    "quittung",
    "beleg",
    "faktura",
    "receipt",
    "statement",
    "zahlung",
    "mahnung",
)

_DEFAULT_BODY_KEYWORDS: tuple[str, ...] = (
    "invoice",
    "rechnung",
    "total amount",
    "gesamtbetrag",
)

_DEFAULT_ATTACHMENT_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
)


class MailConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    port: int = 993
    username: str
    password: str = ""
    use_ssl: bool = True
    folder: str = "INBOX"
    mark_as_read: bool = True
    lookback_days: int | None = None


class StorageConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_dir: Path
    year_subdirs: bool = True
    month_subdirs: bool = True


class DetectionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject_keywords: tuple[str, ...] = _DEFAULT_SUBJECT_KEYWORDS
    body_keywords: tuple[str, ...] = _DEFAULT_BODY_KEYWORDS
    attachment_extensions: tuple[str, ...] = _DEFAULT_ATTACHMENT_EXTENSIONS
    require_attachment: bool = False

    @field_validator("subject_keywords", "body_keywords", "attachment_extensions", mode="before")
    @classmethod
    def _coerce_to_tuple(cls, v: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(v, (list, tuple)):
            return tuple(v)
        msg = f"Expected list or tuple, got {type(v).__name__}"
        raise ValueError(msg)


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mail: MailConfig
    storage: StorageConfig
    detection: DetectionConfig = DetectionConfig()


def resolve_password(cfg: AppConfig) -> str:
    """Return password from config, falling back to MAIL_INVOICE_PASSWORD env var."""
    if cfg.mail.password:
        return cfg.mail.password
    env_val = os.environ.get("MAIL_INVOICE_PASSWORD", "")
    if not env_val:
        msg = (
            "No password configured. Set [mail] password in config.toml "
            "or the MAIL_INVOICE_PASSWORD environment variable."
        )
        raise ValueError(msg)
    return env_val


def load_config(config_path: Path) -> AppConfig:
    """Load and validate AppConfig from a TOML file."""
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    return AppConfig.model_validate(raw)


EXAMPLE_CONFIG_TOML = """\
# mail-invoice configuration
# Copy to config.toml and fill in your credentials.

[mail]
host = "imap.gmx.net"
port = 993
username = "user@gmx.de"
# Leave password empty to use MAIL_INVOICE_PASSWORD environment variable instead
password = ""
use_ssl = true
folder = "INBOX"
# Mark emails as read on the server after successfully saving them
mark_as_read = true
# Only check emails from the last N days (leave unset to check all unread).
# Can be overridden per-run with --since-days on the CLI.
# lookback_days = 30

[storage]
target_dir = "/home/user/invoices"
year_subdirs = true
month_subdirs = true

[detection]
subject_keywords = [
    "invoice", "rechnung", "bill", "quittung", "beleg",
    "faktura", "receipt", "statement", "zahlung", "mahnung",
]
body_keywords = [
    "invoice", "rechnung", "total amount", "gesamtbetrag",
]
# Only save attachments with these extensions
attachment_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".tiff"]
# Set true to skip keyword-matched emails that have no valid attachment
require_attachment = false
"""
