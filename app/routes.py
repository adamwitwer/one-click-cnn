import requests, time, os, json
from flask import render_template_string
from dotenv import load_dotenv

# Load .env sitting next to this file (works in gunicorn/uwsgi too)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

ROKU_IP = "192.168.50.129"
YOUTUBE_TV_APP_ID = "195316"

SMARTTHINGS_CLIENT_ID = os.getenv("SMARTTHINGS_CLIENT_ID")
SMARTTHINGS_CLIENT_SECRET = os.getenv("SMARTTHINGS_CLIENT_SECRET")
SMARTTHINGS_TV_DEVICE_ID = os.getenv("SMARTTHINGS_TV_DEVICE_ID")

OAUTH_TOKEN_URL = "https://api.smartthings.com/oauth/token"
API_BASE = "https://api.smartthings.com/v1"
TOKEN_FILE = os.path.expanduser("~/.smartthings_tokens.json")  # 👈 add this

HTML_PAGE = """ ... your same HTML ... """

def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)

# ---------- SmartThings token helpers ----------
def _load_tokens():
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(f"Token file not found: {TOKEN_FILE}")
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)

def _save_tokens(tokens):
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f)
    os.replace(tmp, TOKEN_FILE)
    try: os.chmod(TOKEN_FILE, 0o600)
    except Exception: pass

def _refresh_tokens(refresh_token):
    # 👇 IMPORTANT: Basic auth, not form fields
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
        "refresh_token": data.get("refresh_token", refresh_token),  # may rotate
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
    }

def _get_access_token():
    tokens = _load_tokens()
    if tokens.get("expires_at", 0) <= time.time() + 60:
        log("Access token near/past expiry. Refreshing…")
        tokens = _refresh_tokens(tokens["refresh_token"])
        _save_tokens(tokens)
    return tokens["access_token"]

def _request_with_auto_refresh(method, url, **kwargs):
    token = _get_access_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Content-Type", "application/json")
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)

    if resp.status_code == 401:
        log("401 from SmartThings. Refreshing token and retrying once…")
        tokens = _refresh_tokens(_load_tokens()["refresh_token"])
        _save_tokens(tokens)
        headers["Authorization"] = f"Bearer {tokens['access_token']}"
        resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    return resp

# ---------- Your existing logic with tiny changes ----------

def mute_tv_smartthings():
    """Mute the Samsung TV via SmartThings API"""
    url = f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/commands"
    payload = {
        "commands": [{
            "component": "main",
            "capability": "audioMute",
            "command": "mute"
        }]
    }
    resp = _request_with_auto_refresh("POST", url, json=payload)
    if resp.status_code != 200:
        log(f"SmartThings mute failed: {resp.status_code} {resp.text}")
        return False
    return True

def is_yttv_running():
    """Check if YouTube TV is currently the active app"""
    try:
        log("Checking current active app...")
        resp = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=5)
        if resp.status_code == 200:
            content = resp.text
            log(f"Active app response: {content}")
            if f'id="{YOUTUBE_TV_APP_ID}"' in content:
                log("YouTube TV is already running!")
                return True
            else:
                log("YouTube TV is not currently active.")
                return False
        else:
            log(f"Failed to query active app: {resp.status_code}")
            return False
    except requests.RequestException as e:
        log(f"Failed to check active app: {e}")
        return False

def register_routes(app):
    @app.route("/")
    def home():
        return render_template_string(HTML_PAGE)

    @app.route("/start-yttv", methods=["POST"])
    def launch_yttv():
        log("Web request received to start YouTube TV")

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

        try:
            log("Launching YouTube TV...")
            resp = requests.post(f"http://{ROKU_IP}:8060/launch/{YOUTUBE_TV_APP_ID}", timeout=5)
            log(f"Launch response: {resp.status_code}")
        except requests.RequestException as e:
            log(f"Failed to launch YouTube TV: {e}")
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

        time.sleep(10)  # Give YTTV time to load

        try:
            log("Sending 'Up' command to dismiss overlay...")
            requests.post(f"http://{ROKU_IP}:8060/keypress/Up", timeout=5)
            time.sleep(6)  # Samsung TV OS hangs! Add 6s
            log("Sending 'Select' command to confirm overlay dismissal...")
            requests.post(f"http://{ROKU_IP}:8060/keypress/Select", timeout=5)
            log("Overlay dismissed. CNN should be full screen.")
        except requests.RequestException as e:
            log(f"Failed to send overlay dismissal commands: {e}")

        # Mute TV via SmartThings
        time.sleep(1)
        log("Muting TV via SmartThings...")
        if mute_tv_smartthings():
            log("TV muted successfully")
        else:
            log("Failed to mute TV via SmartThings")

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