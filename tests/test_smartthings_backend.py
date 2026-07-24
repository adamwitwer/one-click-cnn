"""SmartThings backend: async command handling, verification, token failures.

Run via tests/run.py — this needs TV_BACKEND=smartthings set before
app.routes is imported.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TV_BACKEND"] = "smartthings"
os.environ.update(
    SMARTTHINGS_CLIENT_ID="test-client-id",
    SMARTTHINGS_CLIENT_SECRET="test-secret",
    SMARTTHINGS_TV_DEVICE_ID="test-device",
)

from app import routes  # noqa: E402
from tests.fakes import FakeRoku, Resp, wait_for_launch  # noqa: E402

checks = []


def check(label, condition):
    assert condition, f"FAILED: {label}"
    checks.append(label)
    print(f"  ok  {label}")


# Credentials are read at import; override for the test process.
routes.SMARTTHINGS_CLIENT_ID = "test-client-id"
routes.SMARTTHINGS_CLIENT_SECRET = "test-secret"
routes.SMARTTHINGS_TV_DEVICE_ID = "test-device"

_token_fd = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump({"access_token": "AT", "refresh_token": "RT",
           "expires_at": time.time() + 3600, "refreshed_at": time.time()}, _token_fd)
_token_fd.close()
routes.TOKEN_FILE = _token_fd.name

roku = FakeRoku(routes.CNN_APP_ID, foreground_after=4)

# The TV obeys only once CNN is up, and only on the second command — modelling
# SmartThings accepting a command (HTTP 200) that the device never acts on.
cloud = {"muted": "unmuted", "mute_cmds": 0, "obeys": True, "token_valid": True}


def fake_post(url, **kwargs):
    if ":8060/" in url:
        return roku.post(url)
    if "oauth/token" in url:
        if not cloud["token_valid"]:
            return Resp(400, "invalid_grant")
        return Resp(200, data={"access_token": "AT2", "refresh_token": "RT2",
                               "expires_in": 3600})
    if url.endswith("/commands"):
        command = kwargs["json"]["commands"][0]["command"]
        if command == "mute":
            cloud["mute_cmds"] += 1
            if cloud["obeys"] and roku.active == routes.CNN_APP_ID and cloud["mute_cmds"] >= 2:
                cloud["muted"] = "muted"
        return Resp(200, '{"results":[{"status":"ACCEPTED"}]}')
    raise AssertionError(f"unexpected POST {url}")


def fake_get(url, **kwargs):
    if ":8060/" in url:
        return roku.get(url)
    if url.endswith("/status"):
        if not cloud["token_valid"]:
            return Resp(401, "unauthorized")
        return Resp(200, data={"components": {"main": {
            "switch": {"switch": {"value": "on"}},
            "audioMute": {"mute": {"value": cloud["muted"]}},
        }}})
    raise AssertionError(f"unexpected GET {url}")


import types  # noqa: E402

routes.requests = types.SimpleNamespace(post=fake_post, get=fake_get, RequestException=Exception)
real_sleep = time.sleep
routes.time.sleep = lambda s: None  # fast-forward retry backoff


def main():
    check("TV_BACKEND=smartthings selects the cloud backend", not routes.using_local())

    from app import create_app
    client = create_app().test_client()

    # --- an accepted-but-ignored command is retried until verified ---
    resp = client.post("/start-cnn")
    check("/start-cnn returns immediately", resp.status_code == 200)

    state = wait_for_launch(routes, real_sleep)
    check("launch worker confirms the mute", state["muted"] is True)
    check("retried past the ignored first command", cloud["mute_cmds"] >= 2)
    check("waited for CNN to reach the foreground", roku.polls >= 4)

    body = client.get("/tv-status?refresh=0").get_json()
    check("/tv-status reports muted", body["status"] == "muted" and body["cnn_active"] is True)

    # --- a TV that never obeys must not be reported as success ---
    cloud.update(muted="unmuted", mute_cmds=0, obeys=False)
    roku.polls, roku.active = 0, "0"
    client.post("/start-cnn")
    state = wait_for_launch(routes, real_sleep)
    check("an unverifiable mute is reported as failure",
          state["muted"] is False and bool(state["detail"]))

    # --- dead refresh token: distinct message, no 500 ---
    cloud["token_valid"] = False
    check("expired tokens surface as an auth problem, not 'TV off'",
          routes.get_tv_status() == "auth")
    resp = client.get("/tv-status?refresh=0")
    check("the endpoint still returns 200 with dead tokens", resp.status_code == 200)

    cloud.update(muted="unmuted", mute_cmds=0, obeys=True)
    roku.polls, roku.active = 0, "0"
    client.post("/start-cnn")
    state = wait_for_launch(routes, real_sleep)
    check("a launch with dead tokens asks for re-authorization",
          state["muted"] is False and "re-authorization" in state["detail"])

    routes.time.sleep = real_sleep
    os.unlink(_token_fd.name)
    return True


if __name__ == "__main__":
    main()
    print(f"\n{len(checks)} checks passed")
