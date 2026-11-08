#!/usr/bin/env bash
# Start the di2 web UI on http://127.0.0.1:8095 (Ctrl-C to stop).
# Extra arguments go to `di2 serve`, e.g. ./serve.sh --port 8096
# The first run creates .venv and installs pyserial.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "first run: creating .venv"
  python3 -m venv .venv
  .venv/bin/pip install -q pyserial
fi
[ -e /dev/di2 ] || echo "note: /dev/di2 not found; plug in the SM-BCR2 to read the bike (the page still shows the last read)"
exec .venv/bin/python -m di2 serve "$@"
