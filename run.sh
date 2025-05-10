#!/bin/bash
source venv/bin/activate
export $(grep -v '^#' .env | xargs)
flask run