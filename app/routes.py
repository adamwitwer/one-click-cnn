import os
import time
import json
import requests
import xml.etree.ElementTree as ET
from flask import render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv

# Load .env from repo root (fallback to app/.env if needed)
BASE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
load_dotenv(os.path.join(ROOT_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---------- Roku config ----------
ROKU_IP = "192.168.50.129"
CNN_APP_ID = "65978"  # from /query/apps

# ---------- SmartThings config ----------
SMARTTHINGS_CLIENT_ID = os.getenv("SMARTTHINGS_CLIENT_ID")
SMARTTHINGS_CLIENT_SECRET = os.getenv("SMARTTHINGS_CLIENT_SECRET")
SMARTTHINGS_TV_DEVICE_ID = os.getenv("SMARTTHINGS_TV_DEVICE_ID")

OAUTH_TOKEN_URL = "https://api.smartthings.com/oauth/token"
API_BASE = "https://api.smartthings.com/v1"
TOKEN_FILE = os.path.expanduser("~/.smartthings_tokens.json")

def _smartthings_config_ok() -> bool:
    return all([SMARTTHINGS_CLIENT_ID, SMARTTHINGS_CLIENT_SECRET, SMARTTHINGS_TV_DEVICE_ID])

def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)

# ---------- SmartThings token helpers ----------

def _load_tokens() -> dict:
    if not _smartthings_config_ok():
        raise RuntimeError("SmartThings config missing")
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

def send_smartthings_command(capability: str, command: str, arguments: list = None, max_retries: int = 3, retry_delay: int = 3) -> bool:
    """Send a command to the Samsung TV via SmartThings API."""
    token = _get_access_token()
    url = f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/commands"
    payload = {
        "commands": [{
            "component": "main",
            "capability": capability,
            "command": command,
            "arguments": arguments or []
        }]
    }

    for attempt in range(1, max_retries + 1):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        log(f"SmartThings {command} attempt {attempt}: {resp.status_code} {resp.text!r}")

        if resp.ok or resp.status_code in (200, 202):
            return True

        if resp.status_code == 401:
            log("401 from SmartThings; refreshing token and retrying…")
            tokens = _refresh_tokens(_load_tokens()["refresh_token"])
            _save_tokens(tokens)
            token = tokens["access_token"]
            continue

        if resp.status_code in (409, 503) and attempt < max_retries:
            log(f"Device not ready (status {resp.status_code}). Waiting {retry_delay}s then retrying…")
            time.sleep(retry_delay)
            continue

        break
    return False

def mute_tv_smartthings() -> bool:
    """Mute the Samsung TV via SmartThings API."""
    return send_smartthings_command("audioMute", "mute")

def toggle_mute_smartthings() -> bool:
    """Toggle mute status on the Samsung TV via SmartThings API."""
    token = _get_access_token()
    url = f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/status"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            status = resp.json()
            # Path to mute status: components.main.audioMute.mute.value
            mute_state = status.get("components", {}).get("main", {}).get("audioMute", {}).get("mute", {}).get("value")
            log(f"Current mute state: {mute_state}")
            
            new_command = "unmute" if mute_state == "muted" else "mute"
            log(f"Toggling mute to: {new_command}")
            return send_smartthings_command("audioMute", new_command)
        else:
            log(f"Failed to get TV status: {resp.status_code} {resp.text}")
            # Fallback: just send mute if we can't get status
            return send_smartthings_command("audioMute", "mute")
    except Exception as e:
        log(f"Error toggling mute: {e}")
        return False

def get_tv_status() -> str:
    """Get current TV status from SmartThings. Returns 'off', 'muted', 'unmuted', or 'unavailable'."""
    try:
        if not _smartthings_config_ok():
            return "unavailable"
        token = _get_access_token()
        url = f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/status"
        headers = {"Authorization": f"Bearer {token}"}
        
        resp = requests.get(url, headers=headers, timeout=10)
        
        # Debug logging to file
        with open("debug_log.txt", "w") as f:
            f.write(f"Status Code: {resp.status_code}\n")
            f.write(f"Response: {resp.text}\n")
            
        if resp.status_code == 200:
            status = resp.json()
            main = status.get("components", {}).get("main", {})
            
            # Check power status first
            switch_state = main.get("switch", {}).get("switch", {}).get("value")
            if switch_state != "on":
                return "off"
            
            # Check mute status
            audio_mute = main.get("audioMute", {})
            mute_attr = audio_mute.get("mute", {})
            mute_state = mute_attr.get("value")
            
            log(f"TV Status - Power: {switch_state}, Mute: {mute_state}")
            return "muted" if mute_state == "muted" else "unmuted"
            
        # Handle known offline/error states
        if resp.status_code in (409, 503):
            log(f"TV appears to be offline (status {resp.status_code})")
            return "off"
            
    except Exception as e:
        log(f"Error getting TV status: {e}")
        try:
            with open("debug_log.txt", "a") as f:
                f.write(f"Error: {e}\n")
        except:
            pass
            
    return "off" # Default fallback (offline/error)

def refresh_smartthings_status():
    """Send a refresh command to the TV to update its status."""
    try:
        if not _smartthings_config_ok():
            return
        log("Sending refresh command to SmartThings...")
        send_smartthings_command("refresh", "refresh")
    except Exception as e:
        log(f"Error sending refresh: {e}")

# ---------- Roku helpers ----------

def launch_roku_app(app_id: str, label: str) -> bool:
    """Launch a Roku app by ID."""
    try:
        url = f"http://{ROKU_IP}:8060/launch/{app_id}"
        log(f"Launching Roku app {label} (id={app_id}) at {url}…")
        
        # Debug log
        with open("debug_log.txt", "a") as f:
            f.write(f"Attempting to launch Roku app at {url}\n")
            
        resp = requests.post(url, timeout=5)
        
        with open("debug_log.txt", "a") as f:
            f.write(f"Response: {resp.status_code}\n")
            
        log(f"{label} launch response: {resp.status_code}")
        return resp.status_code in (200, 204)
    except requests.RequestException as e:
        log(f"Failed to launch {label}: {e}")
        try:
            with open("debug_log.txt", "a") as f:
                f.write(f"Error launching Roku: {e}\n")
                # Check for proxy env vars
                f.write(f"Env HTTP_PROXY: {os.environ.get('HTTP_PROXY')}\n")
                f.write(f"Env HTTPS_PROXY: {os.environ.get('HTTPS_PROXY')}\n")
        except:
            pass
        return False

def get_roku_active_app() -> dict:
    """Return the active Roku app as {'id': str, 'name': str} or {} on failure."""
    try:
        url = f"http://{ROKU_IP}:8060/query/active-app"
        resp = requests.get(url, timeout=3)
        if resp.status_code != 200:
            log(f"Roku active-app query failed: {resp.status_code}")
            return {}
        root = ET.fromstring(resp.text)
        app = root.find("app")
        if app is None:
            return {}
        return {"id": app.attrib.get("id", ""), "name": (app.text or "").strip()}
    except Exception as e:
        log(f"Failed to query Roku active app: {e}")
        return {}

# ---------- Flask routes ----------

def register_routes(app):
    @app.route("/")
    def home():
        # Force a refresh to get the latest mute status
        refresh_smartthings_status()
        # Wait briefly for the refresh to propagate
        time.sleep(1.5)
        
        tv_status = get_tv_status()
        active_app = get_roku_active_app()
        cnn_active = active_app.get("id") == CNN_APP_ID
        return render_template("index.html", tv_status=tv_status, cnn_active=cnn_active)

    @app.route("/tv-status")
    def tv_status():
        refresh = request.args.get("refresh", "1") == "1"
        if refresh:
            refresh_smartthings_status()
            time.sleep(1.0)
        status = get_tv_status()
        active_app = get_roku_active_app()
        cnn_active = active_app.get("id") == CNN_APP_ID
        return jsonify({"status": status, "cnn_active": cnn_active})

    @app.route("/toggle-mute", methods=["POST"])
    def toggle_mute():
        log("Web request received to toggle mute")
        if toggle_mute_smartthings():
            return redirect(url_for('home'))
        else:
            return render_template("message.html", 
                                   title="Error",
                                   message="Failed to toggle mute via SmartThings.",
                                   refresh_time=3,
                                   is_error=True)

    @app.route("/start-cnn", methods=["POST"])
    def launch_cnn():
        log("Web request received to start CNN Roku app")

        if not launch_roku_app(CNN_APP_ID, "CNN"):
            return render_template("message.html", 
                                   title="Error",
                                   message="Error launching CNN app. Check the logs for details.",
                                   refresh_time=3,
                                   is_error=True)

        # Let CNN app load & auto-dismiss its own overlay
        time.sleep(12)

        log("Muting TV via SmartThings (CNN)…")
        if mute_tv_smartthings():
            log("TV muted successfully (CNN).")
        else:
            log("Failed to mute TV via SmartThings (CNN).")

        return render_template("message.html", 
                               title="Done!",
                               message="CNN app launched successfully.",
                               refresh_time=2,
                               is_error=False)
