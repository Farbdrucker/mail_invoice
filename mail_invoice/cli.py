from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mail_invoice.config import EXAMPLE_CONFIG_TOML, AppConfig, load_config, resolve_password
from mail_invoice.detection import detect_invoice
from mail_invoice.mail_client import IMAPClient
from mail_invoice.models import RunStats
from mail_invoice.storage import (
    init_db,
    is_processed,
    list_processed,
    mark_processed,
    resolve_db_path,
    save_email,
)

app = typer.Typer(
    name="mail-invoice",
    help="Fetch and archive invoice emails from IMAP.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console(stderr=True)
out = Console()

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )


def _load_config_or_exit(config_path: Path) -> AppConfig:
    if not config_path.exists():
        console.print(
            f"[red]Config file not found:[/red] {config_path}\n"
            "Run [bold]mail-invoice init-config[/bold] to create one."
        )
        raise typer.Exit(code=1)
    try:
        return load_config(config_path)
    except Exception as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("run")
def cmd_run(
    config: Path = typer.Option(Path("config.toml"), "--config", "-c", help="Path to config.toml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Detect but do not save or mark emails"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
    since_days: int | None = typer.Option(
        None,
        "--since-days",
        help="Only check emails from the last N days (overrides lookback_days in config).",
        min=1,
    ),
    all_mail: bool = typer.Option(
        False,
        "--all-mail",
        help="Check all emails, not just unread ones. Useful for the first run.",
    ),
) -> None:
    """Fetch unread emails and save detected invoices."""
    _setup_logging(verbose)
    cfg = _load_config_or_exit(config)

    try:
        password = resolve_password(cfg)
    except ValueError as exc:
        console.print(f"[red]Password error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    db_path = resolve_db_path(cfg.storage)

    if not dry_run:
        Path(cfg.storage.target_dir).mkdir(parents=True, exist_ok=True)
        init_db(db_path)

    stats = _run_pipeline(
        cfg, password, db_path, dry_run=dry_run, since_days=since_days, all_mail=all_mail
    )
    _print_run_summary(stats)

    if stats.errors > 0:
        raise typer.Exit(code=2)


def _run_pipeline(
    cfg: AppConfig,
    password: str,
    db_path: Path,
    *,
    dry_run: bool,
    since_days: int | None,
    all_mail: bool,
) -> RunStats:
    total_unread = 0
    already_processed = 0
    not_invoices = 0
    detected = 0
    saved = 0
    errors = 0

    effective_days = since_days if since_days is not None else cfg.mail.lookback_days
    since_date: date | None = None
    if effective_days is not None:
        since_date = date.today() - timedelta(days=effective_days)
    scope = "all messages" if all_mail else "unread messages"
    if since_date is not None:
        console.print(f"[dim]Searching {scope} since {since_date.isoformat()}[/dim]")
    elif all_mail:
        console.print("[dim]Searching all messages (including read)[/dim]")

    try:
        with IMAPClient(cfg.mail, password) as client:
            client.select_folder(cfg.mail.folder)
            uids = client.fetch_uids(since=since_date, unseen_only=not all_mail)
            total_unread = len(uids)

            if not uids:
                console.print("[dim]No unread messages found.[/dim]")
                return RunStats(
                    total_unread=0,
                    already_processed=0,
                    not_invoices=0,
                    detected_invoices=0,
                    saved_successfully=0,
                    errors=0,
                    dry_run=dry_run,
                )

            console.print(f"Found [bold]{total_unread}[/bold] unread message(s).")

            for uid in uids:
                try:
                    fetched = client.fetch_email_by_uid(uid, cfg.mail.folder)

                    if not dry_run and is_processed(db_path, fetched.message_id):
                        logger.debug("Already processed: %s", fetched.message_id)
                        already_processed += 1
                        continue

                    result = detect_invoice(fetched, cfg.detection)

                    if not result.is_invoice:
                        logger.debug("Not an invoice: uid=%s subject=%r", uid, fetched.subject)
                        not_invoices += 1
                        continue

                    detected += 1
                    kw_str = ", ".join(result.matched_keywords) if result.matched_keywords else "-"
                    console.print(
                        f"  [green]Invoice detected[/green]: {fetched.subject!r} "
                        f"(keywords: {kw_str})"
                    )

                    if dry_run:
                        console.print("  [yellow][dry-run][/yellow] Would save to disk.")
                        continue

                    save_result = save_email(fetched, result, cfg.storage)
                    mark_processed(db_path, fetched, save_result.saved_dir)

                    if cfg.mail.mark_as_read:
                        client.mark_as_read(uid)

                    saved += 1
                    console.print(f"  Saved to: {save_result.saved_dir}")

                except Exception:
                    logger.exception("Error processing UID %s", uid)
                    errors += 1

    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        logger.exception("Fatal IMAP error")
        console.print(f"[red]Fatal error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    return RunStats(
        total_unread=total_unread,
        already_processed=already_processed,
        not_invoices=not_invoices,
        detected_invoices=detected,
        saved_successfully=saved,
        errors=errors,
        dry_run=dry_run,
    )


def _print_run_summary(stats: RunStats) -> None:
    prefix = "[yellow][dry-run][/yellow] " if stats.dry_run else ""
    console.print(
        f"\n{prefix}Summary: "
        f"{stats.total_unread} unread, "
        f"{stats.detected_invoices} invoice(s) detected, "
        f"{stats.saved_successfully} saved, "
        f"{stats.already_processed} skipped (duplicate), "
        f"{stats.errors} error(s)"
    )


@app.command("init-config")
def cmd_init_config(
    output: Path = typer.Option(Path("config.toml"), "--output", "-o", help="Output file path"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file"),
) -> None:
    """Write an example config.toml to disk."""
    if output.exists() and not force:
        console.print(f"[yellow]File already exists:[/yellow] {output}  (use --force to overwrite)")
        raise typer.Exit(code=1)
    output.write_text(EXAMPLE_CONFIG_TOML, encoding="utf-8")
    console.print(f"[green]Created:[/green] {output}")
    console.print("Edit the file and fill in your credentials before the first run.")


@app.command("list-processed")
def cmd_list_processed(
    config: Path = typer.Option(Path("config.toml"), "--config", "-c"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of records to show"),
) -> None:
    """Show recently processed emails from the SQLite database."""
    cfg = _load_config_or_exit(config)
    db_path = resolve_db_path(cfg.storage)

    if not db_path.exists():
        out.print("[yellow]Database not found.[/yellow] Run [bold]mail-invoice run[/bold] first.")
        raise typer.Exit(code=0)

    rows = list_processed(db_path, limit=limit)

    if not rows:
        out.print("No processed emails found.")
        return

    table = Table(title=f"Last {limit} Processed Emails", show_lines=True)
    table.add_column("Processed At", style="dim")
    table.add_column("Message-ID")
    table.add_column("Saved To")

    for row in rows:
        table.add_row(
            str(row["processed_at"]),
            str(row["message_id"]),
            str(row["saved_to"]) if row["saved_to"] else "[dim]N/A[/dim]",
        )
    out.print(table)
