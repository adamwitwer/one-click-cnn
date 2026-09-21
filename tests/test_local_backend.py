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
#
# The interesting behaviour is in how the TV *reports* itself, not in whether
# it takes the key: control (websocket) and readback (UPnP) are separate
# services that fail separately, and the mute bugs all lived in that gap.
#
#   reachable       the websocket accepts keys
#   readback        UPnP answers GetMute at all
#   readback_fails  it answers, but only after this many dropped requests
#                   (the TV waking up after an HDMI-CEC power-on)
#   lag_after_key   after a key press it keeps reporting the old state for
#                   this many reads (the TV acted; its report hasn't caught up)
#   drift           the TV unmutes itself once, this many reads after the mute
#                   was confirmed (CNN's audio coming up)
#   waking          it is off the LAN for this many power checks, then on (a TV
#                   coming back over HDMI-CEC when the Roku launches)

tv = {}


def reset(**kwargs):
    tv.update(muted=False, power="on", keys=[], reads=0, reachable=True,
              readback=True, readback_fails=0, lag_after_key=0, lag=0,
              stale=False, drift=0, waking=0)
    tv.update(kwargs)


def fake_send_key(key, timeout=12):
    tv["keys"].append(key)
    if not tv["reachable"]:
        return False
    if key == "KEY_MUTE":
        tv["stale"] = tv["muted"]
        tv["lag"] = tv["lag_after_key"]
        tv["muted"] = not tv["muted"]
    return True


def fake_get_mute():
    tv["reads"] += 1
    if tv["readback_fails"] > 0:
        tv["readback_fails"] -= 1
        return None
    if not tv["readback"]:
        return None
    if tv["lag"] > 0:
        tv["lag"] -= 1
        return tv["stale"]
    value = tv["muted"]
    if tv["drift"] > 0 and value is True:
        tv["drift"] -= 1
        if tv["drift"] == 0:
            tv["muted"] = False
    return value


def fake_get_power():
    if tv["waking"] > 0:
        tv["waking"] -= 1
        return "unreachable"
    return tv["power"]


tv_local.send_key = fake_send_key
tv_local.get_mute = fake_get_mute
tv_local.get_power = fake_get_power
reset()


def main():
    # Device waits are real seconds; neutralise them for the whole suite and
    # keep a live sleep for anything that has to wait on the worker thread.
    # (This patches the shared time module, so tv_local's sleeps go too.)
    real_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        return run(real_sleep)
    finally:
        time.sleep = real_sleep


def create_app_client():
    from app import create_app
    return create_app().test_client()


def run(real_sleep):
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

    # --- a readback that lags the key press must not provoke a second toggle ---
    # This is the mute that "sometimes just doesn't work": the TV muted, said
    # "unmuted" for a few seconds, and a second KEY_MUTE undid it.
    reset(muted=False, lag_after_key=3)
    result = tv_local.ensure_muted(True)
    check("waits out a lagging readback instead of toggling again",
          result is True and tv["muted"] is True and tv["keys"] == ["KEY_MUTE"])

    # --- a readback that is merely slow to wake is not a failure ---
    reset(muted=False, readback_fails=tv_local.READ_ATTEMPTS - 1)
    check("retries a readback the waking TV hasn't answered yet",
          tv_local.ensure_muted(True) is True and tv["keys"] == ["KEY_MUTE"])

    # --- readback down at first, back in time to confirm the blind toggle ---
    reset(muted=False, readback_fails=tv_local.READ_ATTEMPTS)
    check("confirms the blind toggle once readback recovers",
          tv_local.ensure_muted(True) is True and tv["muted"] is True
          and tv["keys"] == ["KEY_MUTE"])

    # --- a mute that slips back is re-asserted when asked to hold ---
    reset(muted=False, drift=1)
    check("re-asserts a mute the TV undoes moments later",
          tv_local.ensure_muted(True, hold=True) is True and tv["muted"] is True
          and tv["keys"] == ["KEY_MUTE", "KEY_MUTE"])

    # --- a TV still waking up is waited for, not written off ---
    reset(muted=False, waking=3)
    check("waits for a TV the launch has just woken",
          tv_local.wait_awake() is True and routes.get_tv_status() == "unmuted")
    reset(waking=tv_local.AWAKE_ATTEMPTS)
    check("gives up waiting eventually", tv_local.wait_awake() is False)

    # --- failure modes must never report success ---
    reset(muted=False, reachable=False)
    check("unreachable TV reports failure",
          tv_local.ensure_muted(True) is False)

    # None, not False: the key went out and only the readback is missing, so
    # this must not reach the UI as "could not mute".
    reset(muted=False, readback=False)
    result = tv_local.ensure_muted(True)
    check("without readback: one blind toggle, reported unverified",
          result is None and tv["keys"] == ["KEY_MUTE"])

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
    # A powered-on TV that won't answer is 'unknown', not 'off' — reporting it
    # as off took the mute button away from a working TV.
    reset(readback=False)
    check("on but unreadable reports unknown", routes.get_tv_status() == "unknown")

    # --- dispatch layer ---
    reset(muted=False)
    check("routes.toggle_mute uses the local path",
          routes.toggle_mute() is True and tv["muted"] is True)

    # The button means "flip it", so an unreadable state is no reason to refuse.
    reset(muted=False, readback=False)
    check("toggle falls back to a blind key when state is unreadable",
          routes.toggle_mute() is True and tv["keys"] == ["KEY_MUTE"])

    # An unconfirmed toggle must not reach the user as an error page.
    reset(muted=True, readback=False)
    resp = create_app_client().post("/toggle-mute")
    check("an unconfirmed toggle redirects home rather than erroring",
          resp.status_code == 302 and tv["keys"] == ["KEY_MUTE"])

    # --- full launch flow, asserting no SmartThings call happens ---
    roku = FakeRoku(routes.CNN_APP_ID, foreground_after=3)
    routes.requests = roku_only_requests(roku)

    reset(muted=False)
    client = create_app_client()

    resp = client.post("/start-cnn")
    check("/start-cnn returns immediately", resp.status_code == 200)

    state = wait_for_launch(routes, real_sleep)
    check("launch worker confirms the mute", state["muted"] is True and tv["muted"] is True)
    check("waited for CNN to reach the foreground", roku.polls >= 3)
    check("muted with a single key press", tv["keys"] == ["KEY_MUTE"])

    body = client.get("/tv-status?refresh=1").get_json()
    check("/tv-status reports muted and the launch outcome",
          body["status"] == "muted" and body["launch"]["muted"] is True)

    # --- the launch counts as in progress from the moment of the tap ---
    # The Roku call takes seconds when it wakes the TV. A poll landing then used
    # to read "not launching" and the page dropped its spinner mid-launch.
    from tests.fakes import Resp
    seen = {}
    real_post = roku.post

    def slow_roku_post(url):
        with routes._launch_lock:
            seen["in_progress"] = routes._launch_state["in_progress"]
        seen["poll"] = client.get("/tv-status?refresh=0").get_json()["launch"]["in_progress"]
        return real_post(url)

    reset(muted=False)
    roku.active, roku.post = "0", slow_roku_post
    client.post("/start-cnn")
    wait_for_launch(routes, real_sleep)
    check("launch is marked in progress before the Roku call returns",
          seen["in_progress"] is True and seen["poll"] is True)

    # A failed Roku launch must release the claim, or every later tap is refused.
    reset(muted=False)
    roku.post = lambda url: Resp(500)
    resp = client.post("/start-cnn", headers={"X-Requested-With": "fetch"})
    with routes._launch_lock:
        state = dict(routes._launch_state)
    check("a failed Roku launch releases the claim and says why",
          resp.status_code == 502 and state["in_progress"] is False
          and state["muted"] is False and "launch" in state["detail"])
    roku.post = real_post

    # A second tap mid-launch must not relaunch CNN under the running worker.
    reset(muted=False)
    with routes._launch_lock:
        routes._launch_state.update(in_progress=True)
    launches = roku.launches
    resp = client.post("/start-cnn", headers={"X-Requested-With": "fetch"})
    check("a tap during a launch doesn't relaunch CNN",
          resp.get_json().get("already_running") is True and roku.launches == launches)
    with routes._launch_lock:
        routes._launch_state.update(in_progress=False)

    # --- a launch with no readback reports unverified, not failed ---
    reset(muted=False, readback=False)
    roku.active = "0"
    client.post("/start-cnn")
    state = wait_for_launch(routes, real_sleep)
    check("an unconfirmed launch mute is not reported as a failure",
          state["muted"] is None and tv["keys"] == ["KEY_MUTE"])

    # --- status polling stays off the TV while the mute routine has it ---
    reset(muted=False)
    with routes._launch_lock:
        routes._launch_state.update(in_progress=True, muted=None, detail="")
    body = client.get("/tv-status?refresh=1").get_json()
    check("polling doesn't compete with the mute routine for the TV",
          body["status"] == "unknown" and body["launch"]["in_progress"] is True
          and tv["reads"] == 0)
    with routes._launch_lock:
        routes._launch_state.update(in_progress=False)

    return True


if __name__ == "__main__":
    main()
    print(f"\n{len(checks)} checks passed")
