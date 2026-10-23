#!/bin/bash
# Avvia mixtaper dai sorgenti (macOS / Linux): basta un doppio clic.
# Runs mixtaper from source (macOS / Linux): just double-click.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installo uv (gestore Python) / installing uv (Python manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || exit 1
fi
exec uv run --no-editable --python 3.12 mixtaper "$@"
