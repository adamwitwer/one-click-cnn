#!/bin/bash
source venv/bin/activate
export $(grep -v '^#' .env | xargs)
PORT="${PORT:-5000}"
flask run --host=0.0.0.0 --port="$PORT"
