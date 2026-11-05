#!/usr/bin/env python3
"""Headless CNN auto-start script.

Launches the CNN app on Roku and mutes the TV. Designed to run unattended via cron.

Muting goes through the local UPnP path by default (free, no OAuth, no token
expiry). Set TV_BACKEND=smartthings in .env to use the cloud API instead.

Deploy `tv_local.py` alongside this script — see the README's cron section.

Usage:
    python3 scripts/roku-cnn.py               # launch CNN and mute
    python3 scripts/roku-cnn.py --keepalive   # relaunch CNN if it crashed (see keepalive())
    python3 scripts/roku-cnn.py --check       # verify the deployment

Cron example (daily at 7 PM):
    00 19 * * * /path/to/venv/bin/python /path/to/roku-cnn.py >> /home/adam/roku-cnn.log 2>&1
    * 18-23 * * * /path/to/venv/bin/python /path/to/roku-cnn.py --keepalive >> /home/adam/roku-cnn.log 2>&1
"""
import os
import sys
import time
import json
import importlib.util
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv

# Load .env from the same directory as this script
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))
# Also try repo root .env (when running from the repo)
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

ROKU_IP = os.getenv("ROKU_IP", "192.168.50.129")
CNN_APP_ID = "65978"  # from /query/apps

TV_BACKEND = os.getenv("TV_BACKEND", "local").strip().lower()


def _import_tv_local():
    """Load tv_local from beside this script, or from the repo's app/ package."""
    for path in (os.path.join(BASE_DIR, "tv_local.py"),
                 os.path.join(BASE_DIR, "..", "app", "tv_local.py")):
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("tv_local", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


tv_local = _import_tv_local() if TV_BACKEND == "local" else None

SMARTTHINGS_TV_DEVICE_ID = os.getenv("SMARTTHINGS_TV_DEVICE_ID")
SMARTTHINGS_CLIENT_ID = os.getenv("SMARTTHINGS_CLIENT_ID")
SMARTTHINGS_CLIENT_SECRET = os.getenv("SMARTTHINGS_CLIENT_SECRET")

TOKEN_FILE = os.path.expanduser("~/.smartthings_tokens.json")
OAUTH_TOKEN_URL = "https://api.smartthings.com/oauth/token"
API_BASE = "https://api.smartthings.com/v1"

# SmartThings refresh tokens expire after ~30 days idle; warn before that bites.
REFRESH_TOKEN_WARN_AGE = 20 * 24 * 3600
CNN_FOREGROUND_TIMEOUT = 30
MUTE_VERIFY_ATTEMPTS = 4


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)


# ---------- SmartThings token helpers ----------

def _load_tokens():
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(
            f"Token file not found: {TOKEN_FILE}. "
            f"Authorize with scripts/smartthings_auth.py"
        )
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _save_tokens(tokens):
    tokens = dict(tokens, refreshed_at=time.time())
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
        raise RuntimeError(
            f"Token refresh failed: {resp.status_code} {resp.text}. "
            f"Re-authorize with scripts/smartthings_auth.py"
        )
    j = resp.json()
    return {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token", refresh_token),
        "expires_at": time.time() + int(j.get("expires_in", 3600)),
    }


def _get_access_token():
    tokens = _load_tokens()
    age = time.time() - tokens.get("refreshed_at", 0)
    if age > REFRESH_TOKEN_WARN_AGE:
        log(f"WARNING: tokens last refreshed {age / 86400:.0f} days ago. "
            f"Refresh tokens expire after ~30 days idle — re-auth may be needed soon.")
    if tokens.get("expires_at", 0) <= time.time() + 60:
        tokens = _refresh_tokens(tokens["refresh_token"])
        _save_tokens(tokens)
    return tokens["access_token"]


def send_command(capability, command, max_retries=3, retry_delay=3):
    """Send a device command. A 200 means SmartThings accepted it, not that the
    TV acted on it — verify separately when it matters."""
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
            log("401 from SmartThings, refreshing token…")
            try:
                tokens = _refresh_tokens(_load_tokens()["refresh_token"])
                _save_tokens(tokens)
                token = tokens["access_token"]
            except Exception as e:
                log(f"Token refresh failed: {e}")
                return False
            continue

        if resp.status_code in (409, 503) and attempt < max_retries:
            log(f"Device likely not ready (status {resp.status_code}). "
                f"Waiting {retry_delay}s then retrying…")
            time.sleep(retry_delay)
            continue

        break

    return False


def get_mute_state():
    """Return the TV's reported mute value, or None if unavailable."""
    try:
        token = _get_access_token()
        resp = requests.get(
            f"{API_BASE}/devices/{SMARTTHINGS_TV_DEVICE_ID}/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            log(f"Status query returned {resp.status_code}")
            return None
        main = resp.json().get("components", {}).get("main", {})
        return main.get("audioMute", {}).get("mute", {}).get("value")
    except Exception as e:
        log(f"Error reading mute state: {e}")
        return None


def mute_tv_smartthings():
    """Mute the TV and confirm it actually took effect."""
    for attempt in range(1, MUTE_VERIFY_ATTEMPTS + 1):
        send_command("audioMute", "mute")
        time.sleep(2 * attempt)
        send_command("refresh", "refresh", max_retries=1)
        time.sleep(1.5)
        state = get_mute_state()
        log(f"Mute verification attempt {attempt}: TV reports {state!r}")
        if state == "muted":
            return True
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


def get_active_app_id():
    try:
        resp = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=3)
        if resp.status_code != 200:
            return ""
        app = ET.fromstring(resp.text).find("app")
        return app.attrib.get("id", "") if app is not None else ""
    except Exception as e:
        log(f"Failed to query Roku active app: {e}")
        return ""


def wait_for_cnn(timeout=CNN_FOREGROUND_TIMEOUT, interval=1.5):
    """Wait for CNN to reach the foreground. Muting before it's up is why the
    mute would silently do nothing."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_active_app_id() == CNN_APP_ID:
            return True
        time.sleep(interval)
    return False


def get_roku_power():
    """Roku power-mode (PowerOn, Suspend, …), or '' if it didn't answer."""
    try:
        resp = requests.get(f"http://{ROKU_IP}:8060/query/device-info", timeout=3)
        if resp.status_code != 200:
            return ""
        return ET.fromstring(resp.text).findtext("power-mode") or ""
    except Exception:
        return ""


def get_foreground():
    """'cnn', 'home' (home screen or screensaver over it), 'other' (some other
    app), or None if the Roku didn't answer."""
    try:
        resp = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=3)
        if resp.status_code != 200:
            return None
        app = ET.fromstring(resp.text).find("app")
    except Exception:
        return None
    if app is None:
        return "home"
    app_id = app.attrib.get("id", "")
    if app_id == CNN_APP_ID:
        return "cnn"
    # The home screen reports either type="home" or a bare <app>Roku</app>.
    if not app_id or app.attrib.get("type") == "home":
        return "home"
    return "other"


# ---------- keepalive ----------
#
# CNN has been dying a couple of hours into the evening: the Roku goes quiet
# for about a minute and comes back on its home screen. Run every minute from
# cron, keepalive() puts CNN back — but only in the shape a crash leaves
# behind, so it doesn't fight a person:
#
#   * CNN must have been seen in the foreground this evening, and the Roku is
#     now on its home screen. Someone opening another app disarms it until CNN
#     is seen again.
#   * The TV must be on and the Roku awake. Launching to a sleeping Roku wakes
#     the TV over HDMI-CEC, which would undo someone turning it off for the
#     night; and with the TV off, CNN closes by itself within minutes.
#   * Home must persist across KEEPALIVE_GRACE runs, so a person pressing Home
#     to pick something else has a moment before CNN comes back.
#   * At most KEEPALIVE_MAX relaunches an evening, so a CNN that dies on start
#     doesn't relaunch all night.
#
# It never touches the mute: that is the TV's state, and survives the app.

KEEPALIVE_START = (18, 30)
KEEPALIVE_END = (23, 30)
KEEPALIVE_GRACE = 2
KEEPALIVE_MAX = 3


def keepalive_state_file():
    return os.path.expanduser(os.getenv("CNN_KEEPALIVE_STATE", "~/.cnn-keepalive.json"))


def _load_keepalive(today):
    try:
        with open(keepalive_state_file()) as f:
            state = json.load(f)
        if state.get("date") == today:
            return state
    except Exception:
        pass
    return {"date": today, "armed": False, "home_runs": 0, "relaunches": 0}


def _save_keepalive(state):
    path = keepalive_state_file()
    with open(path + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def _tv_is_on():
    tv = tv_local or _import_tv_local()
    if tv is None or not os.getenv("TV_IP", "").strip():
        # Without a known address tv_local would scan the subnet every minute.
        return None
    return tv.get_power() == "on"


def keepalive(now=None):
    """One keepalive pass. Returns what it did, for the tests and the log."""
    now = now or time.localtime()
    if not (KEEPALIVE_START <= (now.tm_hour, now.tm_min) < KEEPALIVE_END):
        return "outside window"

    state = _load_keepalive(time.strftime("%Y-%m-%d", now))
    result = _keepalive_step(state)
    _save_keepalive(state)
    return result


def _keepalive_step(state):
    fg = get_foreground()
    if fg is None:
        return "roku not answering"          # the crash itself; wait it out
    if fg == "cnn":
        state.update(armed=True, home_runs=0)
        return "cnn running"
    if fg == "other":
        if state["armed"]:
            log("Keepalive: another app was opened over CNN; standing down.")
        state.update(armed=False, home_runs=0)
        return "other app"
    if not state["armed"]:
        return "not armed"

    tv_on = _tv_is_on()
    if tv_on is not True or get_roku_power() != "PowerOn":
        why = "TV state unknown (TV_IP not set?)" if tv_on is None else "TV or Roku is off"
        log(f"Keepalive: CNN is gone but {why}; not relaunching.")
        state.update(armed=False, home_runs=0)
        return "tv off"

    state["home_runs"] += 1
    if state["home_runs"] < KEEPALIVE_GRACE:
        return "waiting"

    if state["relaunches"] >= KEEPALIVE_MAX:
        log(f"Keepalive: CNN dropped again after {KEEPALIVE_MAX} relaunches tonight; giving up.")
        state.update(armed=False, home_runs=0)
        return "gave up"

    state["relaunches"] += 1
    state["home_runs"] = 0
    log(f"Keepalive: CNN dropped to the home screen; relaunching "
        f"({state['relaunches']}/{KEEPALIVE_MAX} tonight).")
    if not launch_cnn_app():
        return "launch failed"
    if wait_for_cnn():
        log("Keepalive: CNN is back in the foreground.")
    return "relaunched"


def mute_tv():
    """Mute via the local UPnP path, falling back to SmartThings if configured.

    True = confirmed muted, False = the TV reported itself unmuted, None = the
    key was sent but the TV never reported back. None is not failure: readback
    is the flakier of the TV's two interfaces, so an unconfirmed mute usually
    is a mute. Exiting non-zero on it would fill the cron log with false alarms.
    """
    if TV_BACKEND == "local":
        if tv_local is None:
            log("TV_BACKEND=local but tv_local.py was not found next to this script.")
            return False
        # The launch may have just woken the TV over HDMI-CEC; it is off the
        # LAN entirely until it finishes coming up.
        tv_local.wait_awake()
        log("Muting TV locally over UPnP…")
        # Hold it briefly — CNN's own audio starting can undo a mute placed the
        # moment the app reaches the foreground.
        return tv_local.ensure_muted(True, hold=True)

    log("Muting TV via SmartThings…")
    return mute_tv_smartthings()


def check():
    """Verify this deployment can do its job, without launching or muting.

    Useful right after deploying: cron only runs once a day, and a silent
    misconfiguration would otherwise surface as a missed mute that evening.
    """
    ok = True
    print(f"Script dir:   {os.path.abspath(BASE_DIR)}")
    print(f"Backend:      {TV_BACKEND}")

    print(f"Roku:         {ROKU_IP}", end="  ")
    active = get_active_app_id()
    if active:
        print(f"reachable (active app id: {active})")
    else:
        print("UNREACHABLE")
        ok = False

    if TV_BACKEND == "local":
        if tv_local is None:
            print("tv_local.py:  NOT FOUND — copy app/tv_local.py next to this script")
            return False
        print(f"tv_local.py:  {tv_local.__file__}")

        ip = tv_local.tv_ip()
        if not ip:
            print("TV:           NOT FOUND — set TV_IP in .env, or check the TV is on")
            return False
        print(f"TV:           {ip}  (power: {tv_local.get_power()})")

        paired = tv_local.paired()
        print(f"Paired:       {'yes' if paired else 'NO — run ./run.sh --pair'} "
              f"({tv_local.token_file()})")
        ok = ok and paired

        mute = tv_local.read_mute()
        print(f"Mute:         {'unreadable' if mute is None else mute}")
    else:
        missing = [n for n, v in (("SMARTTHINGS_CLIENT_ID", SMARTTHINGS_CLIENT_ID),
                                  ("SMARTTHINGS_CLIENT_SECRET", SMARTTHINGS_CLIENT_SECRET),
                                  ("SMARTTHINGS_TV_DEVICE_ID", SMARTTHINGS_TV_DEVICE_ID))
                   if not v]
        if missing:
            print(f"SmartThings:  missing {', '.join(missing)}")
            return False
        print(f"Token file:   {TOKEN_FILE} "
              f"({'present' if os.path.exists(TOKEN_FILE) else 'MISSING'})")
        try:
            _get_access_token()
            print("SmartThings:  token OK")
        except Exception as e:
            print(f"SmartThings:  {e}")
            ok = False

    print("\n" + ("✓ Ready." if ok else "✗ Not ready — see above."))
    return ok


def main():
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    if "--keepalive" in sys.argv:
        keepalive()
        return

    log("CNN auto-start script began.")
    if not launch_cnn_app():
        sys.exit(1)

    if wait_for_cnn():
        log("CNN is in the foreground.")
    else:
        log(f"CNN not in foreground after {CNN_FOREGROUND_TIMEOUT}s; muting anyway.")

    muted = mute_tv()
    if muted is True:
        log("TV muted successfully.")
    elif muted is None:
        log("Mute sent, but the TV didn't confirm it (readback unavailable).")
    else:
        log("Failed to mute TV.")
        sys.exit(1)


if __name__ == "__main__":
    main()
