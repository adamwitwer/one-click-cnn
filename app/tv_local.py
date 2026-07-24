"""Local Samsung TV control — no cloud, no OAuth, no API quota.

Three local interfaces between them do everything the SmartThings integration
did, all on the LAN and all free:

* The Tizen websocket remote on port 8002 — sends ``KEY_MUTE``. This is the
  *control* path. It needs a one-time pairing prompt accepted on the TV;
  ``scripts/pair-tv.py`` handles that.
* UPnP ``RenderingControl`` on port 9197 — ``GetMute``/``GetVolume``. This is
  the *readback* path, used to verify that a mute actually landed.
  ``SetMute`` is deliberately not used: this TV (UN50TU690TFXZA) answers it
  with UPnP error 501 "Action Failed" outside an active DLNA session.
* The Tizen info endpoint on port 8001 — reports ``PowerState``.

``KEY_MUTE`` is a toggle rather than an absolute set, so every mute is issued
as read → compare → toggle-if-needed → verify. Where readback is unavailable
the toggle is still sent, and the caller is told the result is unverified.
"""
import os
import socket
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

UPNP_PORT = 9197
INFO_PORT = 8001
WS_PORT = 8002
CONTROL_PATH = "/upnp/control/RenderingControl1"
SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"

CLIENT_NAME = "OneTouchCNN"


# Read lazily, not at import: this module is imported before load_dotenv() runs,
# so anything captured here would miss values set in .env.

def token_file() -> str:
    """Path to the pairing token written by scripts/pair-tv.py."""
    return os.path.expanduser(os.getenv("TV_TOKEN_FILE", "~/.samsungtv_token.txt"))


def mute_readback() -> str:
    """"auto": verify mutes via UPnP readback when the TV answers (preferred).
    "off":  the TV misreports mute state — send KEY_MUTE and trust it. That
    makes mute a blind toggle, so launching while already muted will unmute."""
    return os.getenv("TV_MUTE_READBACK", "auto").strip().lower()

# Cached result of autodiscovery, so we scan at most once per process.
_discovered_ip = None
_discovery_attempted = False


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - [tv] {msg}", flush=True)


# ---------- discovery ----------

def _local_subnet() -> str:
    """Best-effort /24 prefix for this host, e.g. '192.168.50.'."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are sent; this just picks the outbound interface.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0].rsplit(".", 1)[0] + "."
    finally:
        s.close()


def probe(ip: str, timeout: float = 1.5) -> dict:
    """Return the TV's device info dict if `ip` is a Samsung TV, else {}."""
    try:
        resp = requests.get(f"http://{ip}:{INFO_PORT}/api/v2/", timeout=timeout)
        if resp.status_code != 200:
            return {}
        info = resp.json()
        if "Samsung" in info.get("device", {}).get("type", ""):
            return info
    except Exception:
        pass
    return {}


def discover(timeout: float = 1.5) -> str:
    """Scan the local /24 for a Samsung TV. Returns its IP or ''."""
    try:
        prefix = _local_subnet()
    except Exception as e:
        log(f"Could not determine local subnet: {e}")
        return ""

    log(f"Scanning {prefix}0/24 for a Samsung TV…")
    hosts = [f"{prefix}{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as pool:
        for ip, info in zip(hosts, pool.map(lambda h: probe(h, timeout), hosts)):
            if info:
                name = info.get("device", {}).get("name", "?")
                log(f"Found TV {name} at {ip}")
                return ip
    log("No Samsung TV found on the local network.")
    return ""


def tv_ip() -> str:
    """Configured TV_IP, or an autodiscovered address (cached)."""
    global _discovered_ip, _discovery_attempted
    configured = os.getenv("TV_IP", "").strip()
    if configured:
        return configured
    if not _discovery_attempted:
        _discovery_attempted = True
        _discovered_ip = discover()
    return _discovered_ip or ""


# ---------- UPnP RenderingControl ----------

def _soap(action: str, extra: str = "", timeout: float = 6) -> ET.Element:
    """Issue a RenderingControl SOAP call. Returns the response body element."""
    ip = tv_ip()
    if not ip:
        raise RuntimeError("TV address unknown (set TV_IP in .env)")

    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{SERVICE}">'
        f'<InstanceID>0</InstanceID><Channel>Master</Channel>{extra}'
        f'</u:{action}></s:Body></s:Envelope>'
    )
    resp = requests.post(
        f"http://{ip}:{UPNP_PORT}{CONTROL_PATH}",
        data=body.encode(),
        timeout=timeout,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{SERVICE}#{action}"',
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{action} failed: HTTP {resp.status_code} {resp.text[:200]}")
    return ET.fromstring(resp.content)


def _find_text(root: ET.Element, tag: str) -> str:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return (el.text or "").strip()
    return ""


def get_mute():
    """True/False, or None if the TV can't be reached."""
    try:
        value = _find_text(_soap("GetMute"), "CurrentMute")
        return value in ("1", "true", "True")
    except Exception as e:
        log(f"GetMute failed: {e}")
        return None


def get_volume():
    try:
        return int(_find_text(_soap("GetVolume"), "CurrentVolume"))
    except Exception as e:
        log(f"GetVolume failed: {e}")
        return None


# ---------- websocket remote (control path) ----------

def paired() -> bool:
    return os.path.exists(token_file())


def send_key(key: str, timeout: int = 12) -> bool:
    """Send a remote key over the Tizen websocket. Requires prior pairing."""
    ip = tv_ip()
    if not ip:
        log("Cannot send key: TV address unknown (set TV_IP in .env)")
        return False
    try:
        from samsungtvws import SamsungTVWS
    except ImportError:
        log("samsungtvws is not installed — run: pip install -r requirements.txt")
        return False

    try:
        tv = SamsungTVWS(host=ip, port=WS_PORT, token_file=token_file(),
                         name=CLIENT_NAME, timeout=timeout)
        tv.send_key(key)
        return True
    except Exception as e:
        hint = "" if paired() else " (not paired yet — run scripts/pair-tv.py)"
        log(f"send_key({key}) failed: {type(e).__name__}: {e}{hint}")
        return False


def ensure_muted(muted: bool = True, attempts: int = 3) -> bool:
    """Bring the TV to the requested mute state and verify it.

    KEY_MUTE only toggles, so read first and act only if we're not already
    there — that keeps a retry from undoing a mute that already succeeded.
    """
    if mute_readback() == "off":
        log("Readback disabled (TV_MUTE_READBACK=off); sending KEY_MUTE and trusting it.")
        return send_key("KEY_MUTE")

    for attempt in range(1, attempts + 1):
        state = get_mute()

        if state is muted:
            return True

        if state is None:
            # No readback: send one toggle and report it as unverified rather
            # than looping, since looping blind could flip mute back off.
            log("Mute readback unavailable; sending a single unverified KEY_MUTE.")
            send_key("KEY_MUTE")
            return False  # Sent, but unconfirmed — never claim success.

        if not send_key("KEY_MUTE"):
            time.sleep(0.5)
            continue

        time.sleep(1.5)
        new_state = get_mute()
        if new_state is muted:
            return True
        log(f"Mute attempt {attempt}: TV reports {new_state!r}, wanted {muted!r}")
        time.sleep(0.5)
    return False


# ---------- power ----------

def get_power() -> str:
    """'on', 'standby', or 'unreachable'."""
    ip = tv_ip()
    if not ip:
        return "unreachable"
    info = probe(ip, timeout=3)
    if not info:
        return "unreachable"
    state = info.get("device", {}).get("PowerState", "").lower()
    return state or "on"  # Older firmware omits PowerState but only answers when on.


def get_status() -> str:
    """Mirror of the SmartThings status contract: 'off', 'muted', or 'unmuted'."""
    if get_power() != "on":
        return "off"
    muted = get_mute()
    if muted is None:
        return "off"
    return "muted" if muted else "unmuted"
