#!/usr/bin/env bash
# Auto_Script 최초 1회 설치 스크립트 (RHEL/CentOS/Rocky/Alma 기준, dnf)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo dnf install -y python3 python3-pip mariadb

python3 -m venv "$SCRIPT_DIR/.venv"
"$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "설치 완료. 실행: source $SCRIPT_DIR/.venv/bin/activate && python main.py"
