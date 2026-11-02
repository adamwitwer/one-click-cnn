"""scripts/tv-watch.py: logs state changes, only reads, never touches UPnP.

Run via tests/run.py. No hardware or network: `requests` is replaced.
"""
import importlib.util
import io
import os
import sys
import tempfile
import types
from contextlib import redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from tests.fakes import Resp  # noqa: E402

state_dir = tempfile.mkdtemp()
os.environ["TV_WATCH_STATE"] = os.path.join(state_dir, "state.json")
os.environ["TV_IP"] = "192.0.2.1"
os.environ["ROKU_IP"] = "192.0.2.2"

spec = importlib.util.spec_from_file_location("tv_watch", os.path.join(ROOT, "scripts", "tv-watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
watch.time.sleep = lambda s: None

checks = []


def check(label, condition):
    assert condition, f"FAILED: {label}"
    checks.append(label)
    print(f"  ok  {label}")


class World:
    tv_on = True
    roku_power = "PowerOn"
    uptime = 5000
    app = '<app id="65978">CNN</app>'
    player = "play"
    urls = []


def fake_get(url, **kwargs):
    World.urls.append(url)
    if ":8001/" in url:
        if not World.tv_on:
            raise ConnectionError("refused")
        return Resp(200, data={"device": {"PowerState": "on"}})
    if url.endswith("/query/device-info"):
        r = Resp(200)
        r.content = (f"<device-info><power-mode>{World.roku_power}</power-mode>"
                     f"<uptime>{World.uptime}</uptime></device-info>").encode()
        return r
    if url.endswith("/query/active-app"):
        r = Resp(200)
        r.content = f"<active-app>{World.app}</active-app>".encode()
        return r
    if url.endswith("/query/media-player"):
        r = Resp(200)
        r.content = f'<player state="{World.player}" error="false"/>'.encode()
        return r
    raise AssertionError(f"unexpected GET {url}")


def no_post(*a, **k):
    raise AssertionError("tv-watch must never POST")


watch.requests = types.SimpleNamespace(get=fake_get, post=no_post)

# Pin the clock off the hour so heartbeats don't fire unless asked for.
clock = {"t": 1_800_000_000 + 17 * 60}
watch.time.time = lambda: clock["t"]


def tick(minutes=1):
    clock["t"] += 60 * minutes
    buf = io.StringIO()
    with redirect_stdout(buf):
        watch.run()
    return buf.getvalue()


print("first run and quiet runs")
out = tick()
check("first run logs the starting state", "watch started" in out and "app=CNN" in out)
check("an unchanged minute logs nothing", tick() == "")

print("transitions")
World.app = "<screensaver/>"
out = tick()
check("CNN giving way to the screensaver is logged", "app CNN → screensaver" in out)
World.tv_on = False
World.roku_power = "Suspend"
out = tick()
check("TV going off is logged", "tv on → off" in out)
check("Roku sleeping is logged in the same line", "roku PowerOn → Suspend" in out)
World.uptime = 30
out = tick()
check("an uptime drop is reported as a Roku reboot", "ROKU REBOOTED" in out)

print("heartbeat and gaps")
clock["t"] = (clock["t"] // 3600 + 1) * 3600 - 60
out = tick()
check("top of the hour logs a heartbeat", "heartbeat" in out)
out = tick(minutes=30)
check("a long gap between runs is flagged", "no snapshot for 30 min" in out)

print("reads only")
check("never queries UPnP (port 9197)", not any(":9197" in u for u in World.urls))

print(f"\n{len(checks)} checks passed")
