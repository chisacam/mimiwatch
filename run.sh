#!/bin/zsh
# Brings up the mimiwatch server.
#
#   ./run.sh              port 8900
#   PORT=8951 ./run.sh    a different port (for bringing up a separate test server)
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "No virtual environment. Run ./install.sh first." >&2
  exit 1
fi

exec .venv/bin/python server.py --port "${PORT:-8900}"
