"""roku-cnn.py --keepalive: put CNN back after a crash, and only after a crash.

Run via tests/run.py. No hardware or network: `requests` and `tv_local` are
replaced. The crash is modelled on what tv-watch caught on 2026-11-02/03: the
Roku stops answering for a minute and comes back on its home screen.
"""
import importlib.util
import io
import os
import sys
import tempfile
import time
import types
from contextlib import redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

os.environ["TV_BACKEND"] = "local"
os.environ["TV_IP"] = "192.0.2.1"            # TEST-NET-1: never routable
os.environ["ROKU_IP"] = "192.0.2.2"
os.environ["CNN_KEEPALIVE_STATE"] = os.path.join(tempfile.mkdtemp(), "keepalive.json")

from tests.fakes import Resp  # noqa: E402

spec = importlib.util.spec_from_file_location("roku_cnn", os.path.join(ROOT, "scripts", "roku-cnn.py"))
cron = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cron)
cron.time.sleep = lambda s: None

CNN = cron.CNN_APP_ID
APPS = {
    "cnn": f'<app id="{CNN}" type="appl">CNN</app>',
    "home": '<app id="562859" type="home">Roku Dynamic Menu</app>',
    "bare home": "<app>Roku</app>",
    "youtube": '<app id="837" type="appl">YouTube</app>',
    # What the Roku shows for a few minutes after CNN closes on a TV gone dark.
    "native ui": '<app id="1" type="appl">Native UI</app>',
}


class Roku:
    app = "cnn"
    power = "PowerOn"
    answering = True
    launches = 0


class TV:
    power = "on"


def fake_get(url, **kwargs):
    assert ":9197" not in url, "keepalive must not touch UPnP"
    if not Roku.answering:
        raise ConnectionError("no route to host")
    if url.endswith("/query/active-app"):
        return Resp(200, f"<active-app>{APPS[Roku.app]}</active-app>")
    if url.endswith("/query/device-info"):
        return Resp(200, f"<device-info><power-mode>{Roku.power}</power-mode></device-info>")
    raise AssertionError(f"unexpected GET {url}")


def fake_post(url, **kwargs):
    assert url.endswith(f"/launch/{CNN}"), f"unexpected POST {url}"
    Roku.launches += 1
    Roku.app = "cnn"
    return Resp(200)


cron.requests = types.SimpleNamespace(get=fake_get, post=fake_post, RequestException=Exception)
cron.tv_local = types.SimpleNamespace(
    get_power=lambda: TV.power,
    send_key=lambda *a, **k: (_ for _ in ()).throw(AssertionError("keepalive must not send keys")),
)

checks = []


def check(label, condition):
    assert condition, f"FAILED: {label}"
    checks.append(label)
    print(f"  ok  {label}")


def at(hh, mm, day=4):
    return time.strptime(f"2026-11-{day:02d} {hh:02d}:{mm:02d}", "%Y-%m-%d %H:%M")


last_output = {"text": ""}


def run(hh, mm, day=4):
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = cron.keepalive(at(hh, mm, day))
    last_output["text"] = buf.getvalue()
    return result


def reset(app="cnn"):
    try:
        os.remove(os.environ["CNN_KEEPALIVE_STATE"])
    except FileNotFoundError:
        pass
    Roku.app, Roku.power, Roku.answering, Roku.launches = app, "PowerOn", True, 0
    TV.power = "on"


print("the crash it exists for")
reset()
run(20, 50)
Roku.answering = False
check("a Roku that stops answering is waited out", run(20, 51) == "roku not answering")
Roku.answering, Roku.app = True, "home"
check("first minute on the home screen waits", run(20, 52) == "waiting" and Roku.launches == 0)
check("second minute relaunches CNN", run(20, 53) == "relaunched" and Roku.launches == 1)
check("and CNN is running again afterwards", run(20, 54) == "cnn running")

reset()
run(20, 50)
Roku.app = "bare home"
run(20, 51)
check("the bare <app>Roku</app> home screen counts as home", run(20, 52) == "relaunched")

print("leaving people alone")
reset()
run(20, 50)
Roku.app = "youtube"
check("opening another app disarms it", run(20, 51) == "other app")
check("...and the log names the app", "YouTube replaced CNN" in last_output["text"])
Roku.app = "home"
run(20, 52)
run(20, 53)
check("...so going home from YouTube doesn't bring CNN back", Roku.launches == 0)

reset()
run(20, 50)
Roku.app, TV.power = "home", "unreachable"
check("TV off: no relaunch", run(20, 51) == "tv off")
TV.power = "on"
run(20, 52)
run(20, 53)
check("...and it stays disarmed once the TV is back", Roku.launches == 0)

reset()
run(23, 0)
Roku.app, TV.power = "native ui", "unreachable"
check("CNN closing with the TV off stands down", run(23, 3) == "other app")
check("...without claiming someone opened an app (11-07 23:03)",
      "closed with the TV off" in last_output["text"]
      and "replaced" not in last_output["text"])

reset()
run(20, 50)
Roku.app, Roku.power = "home", "Suspend"
run(20, 51)
run(20, 52)
check("a sleeping Roku is never launched to (it would wake the TV)", Roku.launches == 0)

reset(app="home")
run(18, 40)
run(18, 41)
check("never armed (CNN not seen this evening): no launch", Roku.launches == 0)

print("the window")
reset()
check("before 18:30 does nothing", run(18, 29) == "outside window")
check("23:30 is outside", run(23, 30) == "outside window")
reset()
run(23, 25)
Roku.app = "home"
run(23, 26)
check("23:27 is inside", run(23, 27) == "relaunched")

print("limits")
reset()
run(19, 0)
minute = 1
for _ in range(cron.KEEPALIVE_MAX):
    Roku.app = "home"
    run(19, minute)
    run(19, minute + 1)
    minute += 2
check(f"relaunches {cron.KEEPALIVE_MAX} times", Roku.launches == cron.KEEPALIVE_MAX)
Roku.app = "home"
run(19, minute)
check("then gives up for the night", run(19, minute + 1) == "gave up"
      and Roku.launches == cron.KEEPALIVE_MAX)
Roku.app = "cnn"
run(19, 0, day=5)
Roku.app = "home"
run(19, 1, day=5)
check("and the count resets the next evening", run(19, 2, day=5) == "relaunched")

print(f"\n{len(checks)} checks passed")
