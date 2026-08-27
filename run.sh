#!/bin/zsh
# mimiwatch 서버를 띄웁니다.
#
#   ./run.sh              8900번 포트
#   PORT=8951 ./run.sh    다른 포트 (시험용 서버를 따로 띄울 때)
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "가상환경이 없습니다. 먼저 ./install.sh 를 실행하십시오." >&2
  exit 1
fi

exec .venv/bin/python server.py --port "${PORT:-8900}"
