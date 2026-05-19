#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load secrets from .env if present (not tracked by git)
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$ENV_FILE"
fi

# Ensure uv is on PATH (common homeserver location)
export PATH="${HOME}/.local/bin:${PATH}"

exec uv run --project "$SCRIPT_DIR" mail-invoice run \
    --config "${SCRIPT_DIR}/config.toml" \
    "$@"
