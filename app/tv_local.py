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
as read → compare → toggle-if-needed → verify.

The two paths fail independently, and the readback one fails more often: the
UPnP server is unreachable for the first seconds after the TV wakes, and drops
requests while the TV is busy. So reads are retried before they are believed,
and ``ensure_muted`` answers with three states rather than two — muted,
not muted, or sent-but-unconfirmed (``None``). Unconfirmed is not failure and
must not be reported as one.
"""
import os
import socket
import threading
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

# Readback is retried rather than trusted on the first answer: the UPnP endpoint
# does not respond at all while the TV is waking (an HDMI-CEC power-on from a
# Roku launch takes several seconds), and drops the odd request when it is busy.
# These are attempt counts rather than wall-clock deadlines so that a test which
# neutralises time.sleep finishes immediately instead of spinning.
READ_ATTEMPTS = 4          # ~8s of retries, plus each call's own socket timeout
READ_DELAY = 2.0
SETTLE_ATTEMPTS = 5        # ~5s for the TV to report a key press it accepted
SETTLE_DELAY = 1.0
RETRY_DELAY = 2.0          # between toggle attempts, when the websocket refused
HOLD_CHECKS = 4            # re-checks after a confirmed mute (see `hold`)
HOLD_DELAY = 1.5
AWAKE_ATTEMPTS = 10        # ~20s for a TV woken over HDMI-CEC to reach the LAN
AWAKE_DELAY = 2.0

# Serialises mute routines. Two of them interleaved (the launch worker and a tap
# on the mute button) would read the same state and both toggle, cancelling out.
_CONTROL_LOCK = threading.RLock()


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


def get_mute(timeout: float = 4):
    """True/False, or None if the TV can't be reached.

    A single unanswered call means very little — use `read_mute()` when the
    answer matters, which retries. The timeout is deliberately shorter than the
    SOAP default so a retry costs less than one long hang.
    """
    try:
        value = _find_text(_soap("GetMute", timeout=timeout), "CurrentMute")
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

    tv = None
    try:
        tv = SamsungTVWS(host=ip, port=WS_PORT, token_file=token_file(),
                         name=CLIENT_NAME, timeout=timeout)
        # send_key sleeps for key_press_delay *after* sending, so the TV has
        # taken the key by the time this returns and closing is safe.
        tv.send_key(key)
        return True
    except Exception as e:
        hint = "" if paired() else " (not paired yet — run scripts/pair-tv.py)"
        log(f"send_key({key}) failed: {type(e).__name__}: {e}{hint}")
        return False
    finally:
        # Close explicitly. The TV accepts only a few concurrent remote clients,
        # and a connection left for the garbage collector to reap still counts
        # against that — which is how a later key press gets refused.
        if tv is not None:
            try:
                tv.close()
            except Exception:
                pass


def read_mute(attempts: int = READ_ATTEMPTS, delay: float = READ_DELAY):
    """The TV's mute state, retried. True/False, or None if it never answered.

    One unanswered GetMute is not evidence of anything: the UPnP server is
    simply absent for the first few seconds after the TV wakes, which is when a
    launch-triggered mute runs.
    """
    for attempt in range(1, attempts + 1):
        value = get_mute()
        if value is not None:
            if attempt > 1:
                log(f"Mute readback answered on attempt {attempt}.")
            return value
        if attempt < attempts:
            time.sleep(delay)
    return None


def _await_mute(desired: bool, attempts: int = SETTLE_ATTEMPTS, delay: float = SETTLE_DELAY):
    """Watch for the TV to report `desired` after a key press.

    Returns True once it does, False if it answered consistently with the other
    value, or None if it never answered at all. Polling rather than reading once
    matters: the TV reports the new state a variable moment after acting on the
    key, and treating that lag as failure is what used to provoke a second
    KEY_MUTE — undoing the mute that had just worked.
    """
    answered = False
    for _ in range(attempts):
        time.sleep(delay)
        value = get_mute()
        if value is not None:
            answered = True
            if value is desired:
                return True
    return False if answered else None


def _holds(desired: bool, checks: int = HOLD_CHECKS, delay: float = HOLD_DELAY) -> bool:
    """True if the TV is still in `desired` after a few seconds of watching.

    A mute placed the instant CNN reaches the foreground can be undone a moment
    later by the app's own audio starting up or by an HDMI-CEC volume event, so
    a confirmed mute is watched briefly rather than assumed to stick.
    """
    for _ in range(checks):
        time.sleep(delay)
        value = get_mute()
        if value is not None and value is not desired:
            log(f"TV drifted back to mute={value!r} after the mute landed.")
            return False
    return True


def ensure_muted(muted: bool = True, attempts: int = 3, hold: bool = False):
    """Bring the TV to the requested mute state and verify it.

    Returns True (verified in the requested state), False (the TV answered and
    is *not* in it), or None (the key was sent but the TV never reported back).
    None is not failure — the control and readback paths are different services
    on the TV and the readback one is the flakier of the two — so callers should
    not present it as one.

    KEY_MUTE only toggles, so read first and act only if we're not already
    there; that keeps a retry from undoing a mute that already succeeded.
    Set `hold` to re-check for a few seconds afterwards and re-assert if the
    state slips back.
    """
    if mute_readback() == "off":
        log("Readback disabled (TV_MUTE_READBACK=off); sending KEY_MUTE and trusting it.")
        return send_key("KEY_MUTE")

    with _CONTROL_LOCK:
        result = _ensure_muted_once(muted, attempts)
        if result is True and hold and not _holds(muted):
            log("Re-asserting the mute.")
            result = _ensure_muted_once(muted, attempts)
        return result


def _ensure_muted_once(muted: bool, attempts: int):
    for attempt in range(1, attempts + 1):
        state = read_mute()

        if state is muted:
            return True

        if state is None:
            # Readback is down but control may well be fine. Send exactly one
            # toggle — never loop blind, that can flip a good mute back off —
            # then keep watching in case readback comes back.
            log("Mute readback unavailable; sending one KEY_MUTE and watching for it.")
            if not send_key("KEY_MUTE"):
                return False
            landed = _await_mute(muted)
            if landed is True:
                log("Readback recovered; the TV confirms the mute.")
                return True
            if landed is None:
                log("TV never reported its mute state; the key was sent, unconfirmed.")
                return None
            # Readback came back and disagrees — fall through to a verified
            # attempt, which will now read the real state and correct it.
            continue

        if not send_key("KEY_MUTE"):
            # Usually the websocket refusing a connection while the TV is busy.
            # Back off; the next pass re-reads state, so a key that landed
            # despite the error can't cause a double toggle.
            time.sleep(RETRY_DELAY)
            continue

        if _await_mute(muted) is True:
            return True
        log(f"Mute attempt {attempt}: TV has not reported mute={muted!r} yet.")

    # One last look: a slow TV that settles after the final attempt has still
    # ended up where we wanted it, and reporting failure then would be wrong.
    final = read_mute()
    if final is muted:
        return True
    return None if final is None else False


# ---------- power ----------

def get_power(attempts: int = 2) -> str:
    """'on', 'standby', or 'unreachable'."""
    ip = tv_ip()
    if not ip:
        return "unreachable"
    for attempt in range(1, attempts + 1):
        info = probe(ip, timeout=3)
        if info:
            state = info.get("device", {}).get("PowerState", "").lower()
            # Older firmware omits PowerState but only answers when on.
            return state or "on"
        if attempt < attempts:
            time.sleep(0.5)
    return "unreachable"


def wait_awake(attempts: int = AWAKE_ATTEMPTS, delay: float = AWAKE_DELAY) -> bool:
    """Wait until the TV is on the network. True if it got there.

    A TV in standby is *off* the LAN — its ports refuse connections instantly
    rather than timing out — and a Roku launch wakes it over HDMI-CEC. So the
    launch-time mute is fired at exactly the moment the TV may still be coming
    up. Sending into that gap is a mute that never happens, reported as a TV
    that can't be reached.
    """
    for attempt in range(1, attempts + 1):
        if get_power() == "on":
            if attempt > 1:
                log(f"TV reachable after {attempt} attempts.")
            return True
        if attempt < attempts:
            time.sleep(delay)
    log("TV never came up; trying anyway.")
    return False


def get_status() -> str:
    """Status contract: 'off', 'muted', 'unmuted', or 'unknown'.

    'unknown' means the TV is on but didn't answer the readback — distinct from
    'off', which the UI renders as "TV appears to be off". Reporting a powered-on
    TV as off on a dropped GetMute made the mute button vanish at random.
    """
    if get_power() != "on":
        return "off"
    muted = read_mute(attempts=2, delay=0.5)
    if muted is None:
        return "unknown"
    return "muted" if muted else "unmuted"
