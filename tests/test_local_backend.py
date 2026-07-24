"""Local backend: toggle semantics, verification, and the launch flow.

Run via tests/run.py — this needs TV_BACKEND=local set before app.routes is
imported, since the backend is resolved at import time.
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TV_BACKEND"] = "local"
os.environ["TV_IP"] = "192.0.2.1"  # TEST-NET-1: never routable

from app import routes, tv_local  # noqa: E402
from tests.fakes import FakeRoku, roku_only_requests, wait_for_launch  # noqa: E402

checks = []


def check(label, condition):
    assert condition, f"FAILED: {label}"
    checks.append(label)
    print(f"  ok  {label}")


# ---------- a fake TV ----------

tv = {"muted": False, "power": "on", "keys": [], "reachable": True, "readback": True}


def fake_send_key(key, timeout=12):
    tv["keys"].append(key)
    if not tv["reachable"]:
        return False
    if key == "KEY_MUTE":
        tv["muted"] = not tv["muted"]
    return True


tv_local.send_key = fake_send_key
tv_local.get_mute = lambda: tv["muted"] if tv["readback"] else None
tv_local.get_power = lambda: tv["power"]


def reset(**kwargs):
    tv.update(muted=False, keys=[], reachable=True, readback=True, power="on")
    tv.update(kwargs)


def main():
    check("TV_BACKEND=local selects the local backend", routes.using_local())

    # --- toggle semantics ---
    reset(muted=False)
    check("mutes an unmuted TV, verified",
          tv_local.ensure_muted(True) is True and tv["muted"] is True
          and tv["keys"] == ["KEY_MUTE"])

    # KEY_MUTE toggles, so acting on an already-muted TV would unmute it.
    reset(muted=True)
    check("sends nothing when the TV is already muted",
          tv_local.ensure_muted(True) is True and tv["keys"] == [])

    reset(muted=True)
    check("unmutes a muted TV",
          tv_local.ensure_muted(False) is True and tv["muted"] is False
          and tv["keys"] == ["KEY_MUTE"])

    # --- failure modes must never report success ---
    reset(muted=False, reachable=False)
    check("unreachable TV reports failure",
          tv_local.ensure_muted(True) is False)

    reset(muted=False, readback=False)
    result = tv_local.ensure_muted(True)
    check("without readback: one blind toggle, reported unverified",
          result is False and tv["keys"] == ["KEY_MUTE"])

    # --- explicit opt-out of verification ---
    tv_local.mute_readback = lambda: "off"
    reset(muted=False)
    check("TV_MUTE_READBACK=off trusts the toggle",
          tv_local.ensure_muted(True) is True and tv["keys"] == ["KEY_MUTE"])
    tv_local.mute_readback = lambda: "auto"

    # --- status contract matches the SmartThings one ---
    reset(muted=True)
    check("status reports muted", routes.get_tv_status() == "muted")
    reset(muted=False)
    check("status reports unmuted", routes.get_tv_status() == "unmuted")
    reset(power="standby")
    check("standby TV reports off", routes.get_tv_status() == "off")

    # --- dispatch layer ---
    reset(muted=False)
    check("routes.toggle_mute uses the local path",
          routes.toggle_mute() is True and tv["muted"] is True)

    # --- full launch flow, asserting no SmartThings call happens ---
    roku = FakeRoku(routes.CNN_APP_ID, foreground_after=3)
    routes.requests = roku_only_requests(roku)
    real_sleep = time.sleep
    routes.time.sleep = lambda s: None  # fast-forward device waits

    reset(muted=False)
    from app import create_app
    client = create_app().test_client()

    resp = client.post("/start-cnn")
    check("/start-cnn returns immediately", resp.status_code == 200)

    state = wait_for_launch(routes, real_sleep)
    check("launch worker confirms the mute", state["muted"] is True and tv["muted"] is True)
    check("waited for CNN to reach the foreground", roku.polls >= 3)
    check("muted with a single key press", tv["keys"] == ["KEY_MUTE"])

    body = client.get("/tv-status?refresh=1").get_json()
    check("/tv-status reports muted and the launch outcome",
          body["status"] == "muted" and body["launch"]["muted"] is True)

    routes.time.sleep = real_sleep
    return True


if __name__ == "__main__":
    main()
    print(f"\n{len(checks)} checks passed")
