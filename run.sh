#!/bin/zsh
# mimiwatch 데모 서버를 띄웁니다.
cd "$(dirname "$0")"
HAYAMIMI="${HAYAMIMI_DIR:-/Users/chiyak/hobby/hayamimi}"
exec "$HAYAMIMI/.venv/bin/python" server.py --port "${PORT:-8900}"
