#!/bin/bash
source venv/bin/activate
export $(grep -v '^#' .env | xargs)

if [[ "$1" == "--auth" ]]; then
  python3 scripts/smartthings_auth.py
  exit $?
fi

if [[ -n "$PORT" ]]; then
  flask run --host=0.0.0.0 --port="$PORT"
else
  flask run --host=0.0.0.0
fi
