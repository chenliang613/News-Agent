#!/usr/bin/env bash
# Starts the local News-Agent webhook used by the Hermes WeChat fixed commands.
set -euo pipefail

HERMES_ENV_FILE="${HERMES_ENV_FILE:-$HOME/.hermes/.env}"
if [[ ! -r "$HERMES_ENV_FILE" ]]; then
  echo "Hermes environment file not readable: $HERMES_ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$HERMES_ENV_FILE"
set +a

exec "$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/python" -m news_agent.webhook --host 127.0.0.1 --port 8088
