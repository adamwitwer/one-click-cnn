#!/usr/bin/env python3
"""Log when the TV and Roku change state, to find out what turns them off.

Run from cron every minute. Each run takes one snapshot of the TV and the
Roku, compares it with the previous run's, and logs a line only when something
changed — plus one heartbeat line an hour, so a quiet log can be told apart
from a watcher that stopped running.

    * * * * * /home/adam/.scripts/venv/bin/python /home/adam/.scripts/tv-watch.py >> /home/adam/tv-watch.log 2>&1

    python3 scripts/tv-watch.py --check    # print one snapshot, change nothing

It only reads. Nothing here sends a key, launches an app, or touches the TV's
UPnP service — that is what the 18:30 mute verifies against, and a poll
landing in the middle of it can cost the mute a readback. TV power comes from
the info endpoint on port 8001 instead, which the mute routine polls itself.

What the fields tell apart:

* ``tv``          on / off. A TV in standby is off the LAN, so "off" is
                  simply "did not answer".
* ``roku``        Roku power-mode: PowerOn, DisplayOff, Ready, Suspend, …
                  The Roku drops out of PowerOn when *it* decides to sleep,
                  and over HDMI-CEC it can take the TV with it.
* ``app``         the foreground app, or "screensaver". CNN giving way to the
                  home screen or screensaver before the TV goes off points at
                  playback stopping ("Are you still watching?") rather than a
                  power timer.
* ``player``      Roku media-player state (play, pause, buffer, stop…).
* Roku uptime     not logged on its own; a drop means the Roku rebooted.

Standalone on purpose: copied into the cron directory next to roku-cnn.py and
reads the same .env (ROKU_IP, TV_IP).
"""
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

CNN_APP_ID = "65978"
FIELDS = ("tv", "roku", "app", "player")
TIMEOUT = 3


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)


def state_file():
    return os.path.expanduser(os.getenv("TV_WATCH_STATE", "~/.tv-watch-state.json"))


def _get(url, attempts=2):
    """GET with one retry, so a single dropped request isn't logged as a
    device switching off and straight back on. None if it never answered."""
    for attempt in range(attempts):
        try:
            resp = requests.get(url, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp
        except Exception:
            pass
        if attempt < attempts - 1:
            time.sleep(1)
    return None


def _xml(resp):
    try:
        return ET.fromstring(resp.content)
    except Exception:
        return None


def tv_power(ip):
    if not ip:
        return "unknown (TV_IP not set)"
    resp = _get(f"http://{ip}:8001/api/v2/")
    if resp is None:
        return "off"
    try:
        state = resp.json().get("device", {}).get("PowerState", "")
    except Exception:
        state = ""
    # Older firmware omits PowerState but only answers at all when on.
    return (state or "on").lower()


def roku_state(ip):
    """(power-mode, uptime seconds, app, player). Unreachable → all 'unreachable'."""
    base = f"http://{ip}:8060"
    info = _get(f"{base}/query/device-info")
    root = _xml(info) if info is not None else None
    if root is None:
        return "unreachable", None, "unreachable", "unreachable"

    power = root.findtext("power-mode") or "?"
    try:
        uptime = int(root.findtext("uptime") or "")
    except ValueError:
        uptime = None

    app = "?"
    active = _get(f"{base}/query/active-app")
    root = _xml(active) if active is not None else None
    if root is not None:
        if root.find("screensaver") is not None:
            app = "screensaver"
        else:
            el = root.find("app")
            if el is not None:
                name = (el.text or "").strip() or el.attrib.get("id", "?")
                app = "CNN" if el.attrib.get("id") == CNN_APP_ID else name

    player = "?"
    media = _get(f"{base}/query/media-player")
    root = _xml(media) if media is not None else None
    if root is not None:
        player = root.attrib.get("state", "?")
        if root.attrib.get("error") == "true":
            player += " (error)"

    return power, uptime, app, player


def snapshot():
    power, uptime, app, player = roku_state(os.getenv("ROKU_IP", "192.168.50.129"))
    return {
        "tv": tv_power(os.getenv("TV_IP", "").strip()),
        "roku": power,
        "app": app,
        "player": player,
        "uptime": uptime,
        "at": time.time(),
    }


def describe(snap):
    return "  ".join(f"{k}={snap.get(k)}" for k in FIELDS)


def changes(prev, cur):
    """Human-readable list of what moved between two snapshots."""
    out = [f"{k} {prev.get(k)} → {cur.get(k)}" for k in FIELDS if prev.get(k) != cur.get(k)]
    before, now = prev.get("uptime"), cur.get("uptime")
    if before is not None and now is not None and now < before:
        out.append(f"ROKU REBOOTED (uptime now {now // 60} min)")
    return out


def load_state():
    try:
        with open(state_file()) as f:
            return json.load(f)
    except Exception:
        return None


def save_state(snap):
    path = state_file()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f)
    os.replace(tmp, path)


def run():
    cur = snapshot()
    prev = load_state()

    if prev is None:
        log(f"watch started: {describe(cur)}")
    else:
        gap = cur["at"] - prev.get("at", cur["at"])
        if gap > 600:
            # The Pi was down or cron stopped: anything below happened at an
            # unknown point in the gap, not at this timestamp.
            log(f"(no snapshot for {gap / 60:.0f} min — changes below happened somewhere in that gap)")
        moved = changes(prev, cur)
        if moved:
            log(" | ".join(moved) + f"    [now: {describe(cur)}]")
        elif time.localtime(cur["at"]).tm_min == 0:
            log(f"heartbeat: {describe(cur)}")
        if cur["uptime"] is None:
            # Roku unreachable: keep the last known uptime, or a reboot that
            # passes through "unreachable" would never be compared and missed.
            cur["uptime"] = prev.get("uptime")

    save_state(cur)


def main():
    if "--check" in sys.argv:
        print(describe(snapshot()))
        return
    run()


if __name__ == "__main__":
    main()
