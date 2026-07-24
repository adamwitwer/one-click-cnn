import os
import time
import json
import threading
import requests
import xml.etree.ElementTree as ET
from flask import render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv

from . import tv_local

# Load .env from repo root
BASE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

# ---------- Roku config ----------
ROKU_IP = os.getenv("ROKU_IP", "192.168.50.129")
CNN_APP_ID = "65978"  # from /query/apps

# ---------- TV backend ----------
# "local" talks to the TV directly on the LAN: free, instant, and not subject to
# SmartThings' paid API tiers (from October 2026). It needs a one-time pairing —
# run ./run.sh --pair. "smartthings" keeps the original cloud path available.
TV_BACKEND = os.getenv("TV_BACKEND", "local").strip().lower()

# ---------- SmartThings config ----------
SMARTTHINGS_CLIENT_ID = os.getenv("SMARTTHINGS_CLIENT_ID")
SMARTTHINGS_CLIENT_SECRET = os.getenv("SMARTTHINGS_CLIENT_SECRET")
SMARTTHINGS_TV_DEVICE_ID = os.getenv("SMARTTHINGS_TV_DEVICE_ID")

OAUTH_TOKEN_URL = "https://api.smartthings.com/oauth/token"
API_BASE = "https://api.smartthings.com/v1"
TOKEN_FILE = os.path.expanduser("~/.smartthings_tokens.json")

# SmartThings refresh tokens are single-use and expire if unused for ~30 days.
# Warn well before that so a silent re-auth requirement doesn't surprise us.
REFRESH_TOKEN_WARN_AGE = 20 * 24 * 3600

# How long to wait for CNN to reach the foreground before muting.
CNN_FOREGROUND_TIMEOUT = 30
# SmartThings accepts commands asynchronously, so confirm the TV actually muted.
MUTE_VERIFY_ATTEMPTS = 4

# Guards token refresh: the web request and the background mute worker can
# otherwise refresh concurrently and invalidate each other's refresh token.
_TOKEN_LOCK = threading.RLock()

# Result of the most recent launch, surfaced to the UI so a failed mute is visible.
_launch_state = {"in_progress": False, "muted": None, "detail": ""}
_launch_lock = threading.Lock()

class AuthError(RuntimeError):
    """Tokens are missing or the refresh token is dead — re-auth required."""

def _smartthings_config_ok() -> bool:
    return all([SMARTTHINGS_CLIENT_ID, SMARTTHINGS_CLIENT_SECRET, SMARTTHINGS_TV_DEVICE_ID])

def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)

# ---------- SmartThings token helpers ----------

def _load_tokens() -> dict:
    if not _smartthings_config_ok():
        raise RuntimeError("SmartThings config missing")
    if not os.path.exists(TOKEN_FILE):
        raise AuthError(f"Token file not found: {TOKEN_FILE}")
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)

def _save_tokens(tokens: dict) -> None:
    tokens = dict(tokens, refreshed_at=time.time())
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
        raise AuthError(
            f"SmartThings refresh failed: {resp.status_code} {resp.text}. "
            f"Re-authorize with ./run.sh --auth"
        )
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
    }

def _force_refresh() -> str:
    """Refresh and persist tokens under the lock. Returns the new access token."""
    with _TOKEN_LOCK:
        tokens = _refresh_tokens(_load_tokens()["refresh_token"])
        _save_tokens(tokens)
        return tokens["access_token"]

def _get_access_token() -> str:
    with _TOKEN_LOCK:
        tokens = _load_tokens()
        age = time.time() - tokens.get("refreshed_at", 0)
        if age > REFRESH_TOKEN_WARN_AGE:
            log(f"WARNING: SmartThings tokens last refreshed {age / 86400:.0f} days ago. "
                f"Refresh tokens expire after ~30 days idle — re-auth may be needed soon.")
        # Refresh a bit early to avoid clock skew
        if tokens.get("expires_at", 0) <= time.time() + 60:
            tokens = _refresh_tokens(tokens["refresh_token"])
            _save_tokens(tokens)
        return tokens["access_token"]

def send_smartthings_command(capability: str, command: str, arguments: list = None, max_retries: int = 3, retry_delay: int = 3) -> bool:
    """Send a command to the Samsung TV via SmartThings API.

    A 200 here only means SmartThings *accepted* the command — delivery to the
    TV is asynchronous and can silently fail. Use _verify_muted() when it matters.
    """
    try:
        token = _get_access_token()
    except Exception as e:
        log(f"Cannot send {command}: {e}")
        return False

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
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
        except requests.RequestException as e:
            log(f"SmartThings {command} attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            return False

        log(f"SmartThings {command} attempt {attempt}: {resp.status_code} {resp.text!r}")

        if resp.ok:
            return True

        if resp.status_code == 401:
            log("401 from SmartThings; refreshing token and retrying…")
            try:
                token = _force_refresh()
            except Exception as e:
                log(f"Token refresh failed: {e}")
                return False
            continue

        if resp.status_code in (409, 503) and attempt < max_retries:
            log(f"Device not ready (status {resp.status_code}). Waiting {retry_delay}s then retrying…")
            time.sleep(retry_delay)
            continue

        break
    return False

def _get_device_status(timeout: int = 10) -> dict:
    """Return the TV's 'main' component status dict, or {} if unavailable."""
    if not _smartthings_config_ok():
        return {}
    try:
        token = _get_access_token()
        resp = requests.get(
            f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if resp.status_code == 401:
            log("401 on status query; refreshing token and retrying…")
            token = _force_refresh()
            resp = requests.get(
                f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/status",
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
        if resp.status_code == 200:
            return resp.json().get("components", {}).get("main", {})
        log(f"TV status query returned {resp.status_code}")
    except AuthError:
        raise
    except Exception as e:
        log(f"Error getting TV status: {e}")
    return {}

def _mute_value(main: dict) -> str:
    return main.get("audioMute", {}).get("mute", {}).get("value")

def mute_tv_smartthings() -> bool:
    """Mute the TV and confirm it actually took effect."""
    for attempt in range(1, MUTE_VERIFY_ATTEMPTS + 1):
        send_smartthings_command("audioMute", "mute")
        # Give the TV time to act, then ask it (not the cloud cache) for state.
        time.sleep(2 * attempt)
        send_smartthings_command("refresh", "refresh", max_retries=1)
        time.sleep(1.5)
        try:
            state = _mute_value(_get_device_status())
        except AuthError as e:
            log(f"Cannot verify mute: {e}")
            return False
        log(f"Mute verification attempt {attempt}: TV reports {state!r}")
        if state == "muted":
            return True
    log("Mute could not be verified after all attempts.")
    return False

def toggle_mute_smartthings() -> bool:
    """Toggle mute status on the Samsung TV via SmartThings API."""
    try:
        main = _get_device_status(timeout=15)
    except AuthError as e:
        log(f"Cannot toggle mute: {e}")
        return False

    if not main:
        # Fallback: assume unmuted and mute, which is the common case.
        log("Could not read mute state; sending mute as a fallback.")
        return send_smartthings_command("audioMute", "mute")

    mute_state = _mute_value(main)
    new_command = "unmute" if mute_state == "muted" else "mute"
    log(f"Current mute state: {mute_state}; toggling to: {new_command}")
    return send_smartthings_command("audioMute", new_command)

def get_tv_status_smartthings() -> str:
    """Current TV status: 'off', 'muted', 'unmuted', 'auth', or 'unavailable'."""
    if not _smartthings_config_ok():
        return "unavailable"

    try:
        main = _get_device_status()
    except AuthError as e:
        log(f"SmartThings authorization problem: {e}")
        return "auth"

    if not main:
        return "off"  # Offline / error / TV asleep

    switch_state = main.get("switch", {}).get("switch", {}).get("value")
    if switch_state != "on":
        return "off"

    mute_state = _mute_value(main)
    log(f"TV Status - Power: {switch_state}, Mute: {mute_state}")
    return "muted" if mute_state == "muted" else "unmuted"

def refresh_smartthings_status():
    """Send a refresh command to the TV to update its status."""
    try:
        if not _smartthings_config_ok():
            return
        log("Sending refresh command to SmartThings...")
        send_smartthings_command("refresh", "refresh")
    except Exception as e:
        log(f"Error sending refresh: {e}")

# ---------- Backend dispatch ----------
#
# The local backend is preferred: UPnP SetMute is absolute and reads back
# instantly, so there is no accept-but-never-act gap to paper over, and no
# metered API call. SmartThings remains available via TV_BACKEND=smartthings.

def using_local() -> bool:
    return TV_BACKEND == "local"

def get_tv_status() -> str:
    if using_local():
        return tv_local.get_status()
    return get_tv_status_smartthings()

def ensure_muted() -> bool:
    """Mute the TV and confirm it actually took effect."""
    if using_local():
        return tv_local.ensure_muted(True)
    return mute_tv_smartthings()

def toggle_mute() -> bool:
    if using_local():
        current = tv_local.get_mute()
        if current is None:
            log("Could not read mute state from the TV.")
            return False
        return tv_local.ensure_muted(not current)
    return toggle_mute_smartthings()

def refresh_tv_status():
    """Ask the TV to re-report state. Only the cloud backend needs this."""
    if not using_local():
        refresh_smartthings_status()

# ---------- Roku helpers ----------

def launch_roku_app(app_id: str, label: str) -> bool:
    """Launch a Roku app by ID."""
    try:
        url = f"http://{ROKU_IP}:8060/launch/{app_id}"
        log(f"Launching Roku app {label} (id={app_id}) at {url}…")
        resp = requests.post(url, timeout=5)
        log(f"{label} launch response: {resp.status_code}")
        return resp.status_code in (200, 204)
    except requests.RequestException as e:
        log(f"Failed to launch {label}: {e}")
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

def wait_for_roku_app(app_id: str, timeout: int = CNN_FOREGROUND_TIMEOUT, interval: float = 1.5) -> bool:
    """Block until the given app is in the foreground, or timeout elapses.

    Replaces a fixed sleep: CNN's load time varies, and muting before it is
    actually up is why the mute silently did nothing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_roku_active_app().get("id") == app_id:
            return True
        time.sleep(interval)
    return False

# ---------- Launch orchestration ----------

def _launch_worker() -> None:
    """Wait for CNN to come up, then mute — off the request thread."""
    try:
        if wait_for_roku_app(CNN_APP_ID):
            log("CNN is in the foreground.")
        else:
            log(f"CNN not in foreground after {CNN_FOREGROUND_TIMEOUT}s; muting anyway.")

        if not using_local() and not _smartthings_config_ok():
            detail, muted = "SmartThings is not configured.", False
        elif ensure_muted():
            detail, muted = "", True
            log("TV muted successfully (CNN).")
        else:
            muted = False
            if using_local():
                detail = "Could not reach the TV to mute it."
            # Distinguish "TV ignored us" from "our tokens are dead" — the
            # latter needs a re-auth and would otherwise look like a flaky TV.
            elif get_tv_status() == "auth":
                detail = "SmartThings needs re-authorization (./run.sh --auth)."
            else:
                detail = "Could not confirm the TV muted."
            log(f"Failed to mute TV (CNN): {detail}")
    except Exception as e:
        detail, muted = f"Mute failed: {e}", False
        log(f"Unexpected error in launch worker: {e}")

    with _launch_lock:
        _launch_state.update(in_progress=False, muted=muted, detail=detail)

def start_launch_worker() -> None:
    with _launch_lock:
        if _launch_state["in_progress"]:
            log("Launch worker already running; not starting another.")
            return
        _launch_state.update(in_progress=True, muted=None, detail="")
    threading.Thread(target=_launch_worker, daemon=True).start()

# ---------- Flask routes ----------

def register_routes(app):
    @app.route("/")
    def home():
        tv_status = get_tv_status()
        active_app = get_roku_active_app()
        cnn_active = active_app.get("id") == CNN_APP_ID
        # Local polling is free, so it can be brisk. The cloud path is metered,
        # so it polls slower and only occasionally forces a device refresh.
        return render_template("index.html",
                               tv_status=tv_status,
                               cnn_active=cnn_active,
                               poll_ms=10000 if using_local() else 20000,
                               throttle_refresh=not using_local())

    @app.route("/tv-status")
    def tv_status():
        refresh = request.args.get("refresh", "1") == "1"
        if refresh:
            refresh_tv_status()
        status = get_tv_status()
        active_app = get_roku_active_app()
        cnn_active = active_app.get("id") == CNN_APP_ID
        with _launch_lock:
            launch = dict(_launch_state)
        return jsonify({"status": status, "cnn_active": cnn_active, "launch": launch})

    @app.route("/toggle-mute", methods=["POST"])
    def toggle_mute_route():
        log("Web request received to toggle mute")
        if toggle_mute():
            return redirect(url_for('home'))
        else:
            return render_template("message.html",
                                   title="Error",
                                   message="Failed to toggle mute. Check the logs for details.",
                                   refresh_time=3,
                                   is_error=True)

    @app.route("/start-cnn", methods=["POST"])
    def launch_cnn():
        log("Web request received to start CNN Roku app")
        # The home page posts here via fetch and keeps a single spinner up while
        # it polls /tv-status for the outcome, so it just needs a JSON ack. A
        # plain form post (no JS) still gets the interstitial page.
        wants_json = request.headers.get("X-Requested-With") == "fetch"

        if not launch_roku_app(CNN_APP_ID, "CNN"):
            if wants_json:
                return jsonify({"ok": False, "error": "Could not launch the CNN app."}), 502
            return render_template("message.html",
                                   title="Error",
                                   message="Error launching CNN app. Check the logs for details.",
                                   refresh_time=3,
                                   is_error=True)

        # Muting waits for CNN to reach the foreground and then verifies, which
        # takes longer than a browser is willing to hold a request open. Run it
        # in the background; the home page polls /tv-status for the outcome.
        start_launch_worker()

        if wants_json:
            return jsonify({"ok": True})
        return render_template("message.html",
                               title="Done!",
                               message="CNN app launched. Muting the TV…",
                               refresh_time=2,
                               is_error=False)
