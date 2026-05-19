from __future__ import annotations

import re

from mail_invoice.config import DetectionConfig
from mail_invoice.models import DetectionResult, FetchedEmail


def _build_keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern[str]:
    escaped = sorted((re.escape(k) for k in keywords), key=len, reverse=True)
    return re.compile("|".join(escaped), re.IGNORECASE)


def _find_keyword_matches(text: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    return tuple(sorted({m.group().lower() for m in pattern.finditer(text)}))


def html_to_text(html: str) -> str:
    """Strip HTML tags and decode common entities. No external dependencies."""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(br|p|div|tr|li|h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = (
        html.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"\n{3,}", "\n\n", html).strip()


def detect_invoice(fetched_email: FetchedEmail, cfg: DetectionConfig) -> DetectionResult:
    """Determine whether an email is an invoice or bill.

    Pure function: given the same inputs, always returns the same result.
    """
    subject_pattern = _build_keyword_pattern(cfg.subject_keywords)
    body_pattern = _build_keyword_pattern(cfg.body_keywords)

    subject_matches = _find_keyword_matches(fetched_email.subject, subject_pattern)

    body_text = fetched_email.body_plain.strip() or html_to_text(fetched_email.body_html)
    body_matches = _find_keyword_matches(body_text, body_pattern)

    valid_attachment_names = tuple(
        att.filename
        for att in fetched_email.attachments
        if any(att.filename.lower().endswith(ext) for ext in cfg.attachment_extensions)
    )

    subject_matched = len(subject_matches) > 0
    body_matched = len(body_matches) > 0
    has_valid_attachments = len(valid_attachment_names) > 0

    keyword_detected = subject_matched or body_matched
    attachment_ok = has_valid_attachments if cfg.require_attachment else True

    return DetectionResult(
        is_invoice=keyword_detected and attachment_ok,
        subject_matched=subject_matched,
        body_matched=body_matched,
        matched_keywords=subject_matches + body_matches,
        has_valid_attachments=has_valid_attachments,
        valid_attachment_names=valid_attachment_names,
    )
