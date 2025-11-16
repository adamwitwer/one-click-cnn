import os
import time
import json
import requests
from flask import render_template_string
from dotenv import load_dotenv

# Load .env sitting next to this file
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---------- Roku config ----------
ROKU_IP = "192.168.50.129"
YOUTUBE_TV_APP_ID = "195316"
CNN_APP_ID = "65978"  # from /query/apps

# ---------- SmartThings config ----------
SMARTTHINGS_CLIENT_ID = os.getenv("SMARTTHINGS_CLIENT_ID")
SMARTTHINGS_CLIENT_SECRET = os.getenv("SMARTTHINGS_CLIENT_SECRET")
SMARTTHINGS_TV_DEVICE_ID = os.getenv("SMARTTHINGS_TV_DEVICE_ID")

OAUTH_TOKEN_URL = "https://api.smartthings.com/oauth/token"
API_BASE = "https://api.smartthings.com/v1"
TOKEN_FILE = os.path.expanduser("~/.smartthings_tokens.json")

HTML_PAGE = """
<!doctype html>
<html>
  <head><title>Roku Remote</title></head>
  <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon-96x96.png') }}" sizes="96x96" />
  <link rel="icon" type="image/svg+xml" href="{{ url_for('static', filename='favicon.svg') }}" />
  <link rel="shortcut icon" href="{{ url_for('static', filename='favicon.ico') }}" />
  <link rel="apple-touch-icon" sizes="180x180" href="{{ url_for('static', filename='apple-touch-icon.png') }}" />
  <meta name="apple-mobile-web-app-title" content="1CNN" />
  <link rel="manifest" href="{{ url_for('static', filename='site.webmanifest') }}" />
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
    <h1>📺 Roku Control</h1>

    <form action="/start-yttv" method="post">
      <button type="submit" style="
        font-size: 2.4em;
        padding: 1.6em 2.8em;
        background-color: #673ab7;
        color: white;
        border: none;
        border-radius: 12px;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.2);
        cursor: pointer;
        transition: transform 0.1s ease-in-out;
        margin-bottom: 1.5em;
      "
      onmouseover="this.style.transform='scale(1.05)'"
      onmouseout="this.style.transform='scale(1)'">
        ▶️ Start YouTube TV (legacy)
      </button>
    </form>

    <form action="/start-cnn" method="post">
      <button type="submit" style="
        font-size: 2.2em;
        padding: 1.4em 2.6em;
        background-color: #e53935;
        color: white;
        border: none;
        border-radius: 12px;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.2);
        cursor: pointer;
        transition: transform 0.1s ease-in-out;
      "
      onmouseover="this.style.transform='scale(1.05)'"
      onmouseout="this.style.transform='scale(1)'">
        🟥 Start CNN App
      </button>
    </form>
  </body>
</html>
"""

def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)

# ---------- SmartThings token helpers ----------

def _load_tokens() -> dict:
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(f"Token file not found: {TOKEN_FILE}")
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)

def _save_tokens(tokens: dict) -> None:
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f)
    os.replace(tmp, TOKEN_FILE)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        # Best effort; not fatal if chmod fails on some platforms
        pass

def _refresh_tokens(refresh_token: str) -> dict:
    """Refresh SmartThings OAuth token using Basic auth."""
    log("Refreshing SmartThings token…")
    resp = requests.post(
        OAUTH_TOKEN_URL,
        auth=(SMARTTHINGS_CLIENT_ID, SMARTTHINGS_CLIENT_SECRET),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"SmartThings refresh failed: {resp.status_code} {resp.text}")
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
    }

def _get_access_token() -> str:
    tokens = _load_tokens()
    # Refresh a bit early to avoid clock skew
    if tokens.get("expires_at", 0) <= time.time() + 60:
        tokens = _refresh_tokens(tokens["refresh_token"])
        _save_tokens(tokens)
    return tokens["access_token"]

def mute_tv_smartthings(max_retries: int = 3, retry_delay: int = 3) -> bool:
    """
    Mute the Samsung TV via SmartThings API.
    Handles:
      - token refresh (401)
      - transient device state errors (409/503) with retries
    """
    token = _get_access_token()
    url = f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/commands"
    payload = {
        "commands": [{
            "component": "main",
            "capability": "audioMute",
            "command": "mute"
        }]
    }

    for attempt in range(1, max_retries + 1):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        log(f"SmartThings mute attempt {attempt}: {resp.status_code} {resp.text!r}")

        # Success
        if resp.ok or resp.status_code in (200, 202):
            return True

        # Token expired / invalid
        if resp.status_code == 401:
            log("401 from SmartThings; refreshing token and retrying…")
            tokens = _refresh_tokens(_load_tokens()["refresh_token"])
            _save_tokens(tokens)
            token = tokens["access_token"]
            continue

        # Device not ready / busy
        if resp.status_code in (409, 503) and attempt < max_retries:
            log(f"Device not ready (status {resp.status_code}). "
                f"Waiting {retry_delay}s then retrying…")
            time.sleep(retry_delay)
            continue

        # Other errors: give up
        break

    return False

# ---------- Roku helpers ----------

def is_yttv_running() -> bool:
    """Check if YouTube TV is currently the active Roku app."""
    try:
        log("Checking current active app…")
        resp = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=5)
        if resp.status_code == 200:
            content = resp.text
            log(f"Active app response: {content}")
            if f'id="{YOUTUBE_TV_APP_ID}"' in content:
                log("YouTube TV is already running.")
                return True
            log("YouTube TV is not currently active.")
            return False
        else:
            log(f"Failed to query active app: {resp.status_code}")
            return False
    except requests.RequestException as e:
        log(f"Failed to check active app: {e}")
        return False

def launch_roku_app(app_id: str, label: str) -> bool:
    """Launch a Roku app by ID."""
    try:
        log(f"Launching Roku app {label} (id={app_id})…")
        resp = requests.post(f"http://{ROKU_IP}:8060/launch/{app_id}", timeout=5)
        log(f"{label} launch response: {resp.status_code}")
        return resp.status_code in (200, 204)
    except requests.RequestException as e:
        log(f"Failed to launch {label}: {e}")
        return False

# ---------- Flask routes ----------

def register_routes(app):
    @app.route("/")
    def home():
        return render_template_string(HTML_PAGE)

    @app.route("/start-yttv", methods=["POST"])
    def launch_yttv():
        log("Web request received to start YouTube TV")

        # Check if YTTV is already running
        if is_yttv_running():
            log("YouTube TV is already active. Skipping launch to avoid overlay confusion.")
            return render_template_string("""
            <html>
              <head>
                <meta http-equiv="refresh" content="2; url=/" />
                <title>Already Running</title>
              </head>
              <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
                <h1>📺 YouTube TV is already running</h1>
                <p>No action needed. Returning to control screen...</p>
              </body>
            </html>
            """)

        if not launch_roku_app(YOUTUBE_TV_APP_ID, "YouTube TV"):
            return render_template_string("""
            <html>
              <head>
                <meta http-equiv="refresh" content="3; url=/" />
                <title>Error</title>
              </head>
              <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
                <h1>❌ Error launching YouTube TV</h1>
                <p>Check the logs for details. Returning to control screen...</p>
              </body>
            </html>
            """)

        # Give YTTV time to load, then dismiss overlay with Roku keypresses
        time.sleep(10)
        try:
            log("Sending 'Up' command to dismiss overlay…")
            requests.post(f"http://{ROKU_IP}:8060/keypress/Up", timeout=5)
            time.sleep(6)  # Samsung TV OS hangs a bit
            log("Sending 'Select' command to confirm overlay dismissal…")
            requests.post(f"http://{ROKU_IP}:8060/keypress/Select", timeout=5)
            log("Overlay dismissed. CNN should be full screen.")
        except requests.RequestException as e:
            log(f"Failed to send overlay dismissal commands: {e}")

        # Mute TV via SmartThings
        time.sleep(1)
        log("Muting TV via SmartThings (YTTV)…")
        if mute_tv_smartthings():
            log("TV muted successfully (YTTV).")
        else:
            log("Failed to mute TV via SmartThings (YTTV).")

        return render_template_string("""
        <html>
          <head>
            <meta http-equiv="refresh" content="2; url=/" />
            <title>Done!</title>
          </head>
          <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
            <h1>✅ YouTube TV launched</h1>
            <p>Returning to control screen...</p>
          </body>
        </html>
        """)

    @app.route("/start-cnn", methods=["POST"])
    def launch_cnn():
        log("Web request received to start CNN Roku app")

        if not launch_roku_app(CNN_APP_ID, "CNN"):
            return render_template_string("""
            <html>
              <head>
                <meta http-equiv="refresh" content="3; url=/" />
                <title>Error</title>
              </head>
              <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
                <h1>❌ Error launching CNN app</h1>
                <p>Check the logs for details. Returning to control screen...</p>
              </body>
            </html>
            """)

        # Let CNN app load & auto-dismiss its own overlay
        time.sleep(12)

        log("Muting TV via SmartThings (CNN)…")
        if mute_tv_smartthings():
            log("TV muted successfully (CNN).")
        else:
            log("Failed to mute TV via SmartThings (CNN).")

        return render_template_string("""
        <html>
          <head>
            <meta http-equiv="refresh" content="2; url=/" />
            <title>Done!</title>
          </head>
          <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
            <h1>✅ CNN app launched</h1>
            <p>Returning to control screen...</p>
          </body>
        </html>
        """)