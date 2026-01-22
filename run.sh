#!/bin/bash
source venv/bin/activate
export $(grep -v '^#' .env | xargs)

if [[ "$1" == "--auth" ]]; then
  python3 scripts/smartthings_auth.py
  exit $?
fi

PORT="${PORT:-5000}"
flask run --host=0.0.0.0 --port="$PORT"
