#!/usr/bin/env python3
"""Headless CNN auto-start script.

Launches the CNN app on Roku and mutes the TV via SmartThings.
Designed to run unattended via cron.

Usage:
    python3 scripts/roku-cnn.py

Cron example (daily at 7 PM):
    00 19 * * * /path/to/venv/bin/python /path/to/roku-cnn.py >> /home/adam/roku-cnn.log 2>&1
"""
import os
import time
import json
import requests
from dotenv import load_dotenv

# Load .env from the same directory as this script
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))
# Also try repo root .env (when running from the repo)
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

ROKU_IP = os.getenv("ROKU_IP", "192.168.50.129")
CNN_APP_ID = "65978"  # from /query/apps

SMARTTHINGS_TV_DEVICE_ID = os.getenv("SMARTTHINGS_TV_DEVICE_ID")
SMARTTHINGS_CLIENT_ID = os.getenv("SMARTTHINGS_CLIENT_ID")
SMARTTHINGS_CLIENT_SECRET = os.getenv("SMARTTHINGS_CLIENT_SECRET")

TOKEN_FILE = os.path.expanduser("~/.smartthings_tokens.json")
OAUTH_TOKEN_URL = "https://api.smartthings.com/oauth/token"
API_BASE = "https://api.smartthings.com/v1"


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)


# ---------- SmartThings token helpers ----------

def _load_tokens():
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(f"Token file not found: {TOKEN_FILE}")
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _save_tokens(tokens):
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f)
    os.replace(tmp, TOKEN_FILE)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass


def _refresh_tokens(refresh_token):
    log("Refreshing SmartThings token…")
    resp = requests.post(
        OAUTH_TOKEN_URL,
        auth=(SMARTTHINGS_CLIENT_ID, SMARTTHINGS_CLIENT_SECRET),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {resp.status_code} {resp.text}")
    j = resp.json()
    return {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token", refresh_token),
        "expires_at": time.time() + int(j.get("expires_in", 3600)),
    }


def _get_access_token():
    tokens = _load_tokens()
    if tokens.get("expires_at", 0) <= time.time() + 60:
        tokens = _refresh_tokens(tokens["refresh_token"])
        _save_tokens(tokens)
    return tokens["access_token"]


def mute_tv_smartthings(max_retries=3, retry_delay=3):
    token = _get_access_token()
    url = f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/commands"
    payload = {
        "commands": [{
            "component": "main",
            "capability": "audioMute",
            "command": "mute",
        }]
    }

    for attempt in range(1, max_retries + 1):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        log(f"SmartThings mute attempt {attempt}: {resp.status_code} {resp.text!r}")

        if resp.ok:
            return True

        if resp.status_code == 401:
            log("401 from SmartThings, refreshing token…")
            tokens = _refresh_tokens(_load_tokens()["refresh_token"])
            _save_tokens(tokens)
            token = tokens["access_token"]
            continue

        if resp.status_code in (409, 503) and attempt < max_retries:
            log(f"Device likely not ready (status {resp.status_code}). "
                f"Waiting {retry_delay}s then retrying…")
            time.sleep(retry_delay)
            continue

        break

    return False


# ---------- Roku helpers ----------

def launch_cnn_app():
    log("Launching CNN Roku app…")
    try:
        resp = requests.post(f"http://{ROKU_IP}:8060/launch/{CNN_APP_ID}", timeout=5)
        log(f"Launch response: {resp.status_code}")
        return resp.status_code in (200, 204)
    except requests.RequestException as e:
        log(f"Failed to launch CNN app: {e}")
        return False


def main():
    log("CNN auto-start script began.")
    if not launch_cnn_app():
        return

    # Give CNN app time to load
    time.sleep(8)

    log("Muting TV via SmartThings…")
    if mute_tv_smartthings():
        log("TV muted successfully.")
    else:
        log("Failed to mute TV via SmartThings.")


if __name__ == "__main__":
    main()
