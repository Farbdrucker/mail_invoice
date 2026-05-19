# mail-invoice

Connects to an IMAP mailbox, detects emails containing invoices or bills (German and English keywords), and saves attachments plus an `email.md` summary to an organised directory tree. Designed to run unattended as a cronjob.

## Setup

```bash
# Clone / copy the project, then install dependencies
uv sync
```

## Configuration

```bash
# Generate a config file from the built-in template
mail-invoice init-config --output config.toml

# Edit it — at minimum set host, username, and target_dir
$EDITOR config.toml
```

Store your password in `.env` (not committed to git) rather than in `config.toml`:

```bash
# .env
export MAIL_INVOICE_PASSWORD="your-password-here"
```

Key config options in `config.toml`:

| Field | Default | Description |
|---|---|---|
| `mail.host` | — | IMAP server (e.g. `imap.gmx.net`) |
| `mail.username` | — | Your email address |
| `mail.folder` | `INBOX` | IMAP folder to check |
| `mail.mark_as_read` | `true` | Mark saved emails as read on the server |
| `mail.lookback_days` | unset | Persistent date filter (days back from today) |
| `storage.target_dir` | — | Root directory for saved invoices |
| `detection.subject_keywords` | see example | Trigger words in the subject line |
| `detection.require_attachment` | `false` | Skip emails without a recognised attachment |

## Usage

```bash
# First run — scan everything from the last year, preview only
mail-invoice run --all-mail --since-days 365 --dry-run

# First run — save for real
mail-invoice run --all-mail --since-days 365

# Normal run (unread emails only)
mail-invoice run

# Show recently saved invoices
mail-invoice list-processed --limit 20
```

Saved files are organised as:

```
target_dir/
  2024/
    01/
      2024-01-15_amazon_invoice-123/
        email.md        ← metadata + body (YAML front matter for LLM parsing)
        invoice.pdf
```

## Automated runs (cronjob)

`run.sh` in the project root is the cronjob entry point. It sources `.env` for the password and forwards any extra arguments to `mail-invoice run`.

Make it executable once:

```bash
chmod +x run.sh
```

Add to crontab (`crontab -e`):

```cron
# Run every 30 minutes, log to file
*/30 * * * * /path/to/mail_invoice/run.sh >> /var/log/mail-invoice.log 2>&1
```

Pass extra flags directly (they are forwarded to `mail-invoice run`):

```cron
# Only check the last 30 days
*/30 * * * * /path/to/mail_invoice/run.sh --since-days 30 >> /var/log/mail-invoice.log 2>&1
```

To keep the log from growing indefinitely, add a logrotate config at `/etc/logrotate.d/mail-invoice`:

```
/var/log/mail-invoice.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
```
