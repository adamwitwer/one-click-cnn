"""The guard: LAN/Tailnet only, and no cross-site button presses.

Run via tests/run.py. No hardware or network: refused requests never reach a
route, and the allowed ones hit stand-in routes on a bare app.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TV_BACKEND"] = "local"
os.environ["TV_IP"] = "192.0.2.1"  # TEST-NET-1: never routable

from flask import Flask  # noqa: E402

from app import create_app, guard  # noqa: E402

checks = []


def check(label, condition):
    assert condition, f"FAILED: {label}"
    checks.append(label)
    print(f"  ok  {label}")


print("which addresses count as local")
for addr in ("127.0.0.1", "::1", "192.168.50.20", "10.0.0.5", "100.101.102.103",
             "fd7a:115c:a1e0::1", "::ffff:192.168.1.9"):
    check(f"{addr} is local", guard.is_local(addr))
# Real public addresses: Python counts the documentation ranges as private.
for addr in ("8.8.8.8", "93.184.215.14", "2001:4860:4860::8888", "100.128.0.1", "", None, "x"):
    check(f"{addr!r} is not", not guard.is_local(addr))

bare = Flask(__name__)
guard.install(bare)
bare.add_url_rule("/", "home", lambda: "home")
bare.add_url_rule("/start-cnn", "start", lambda: "started", methods=["POST"])
client = bare.test_client()


def post(addr="192.168.50.20", **headers):
    return client.post("/start-cnn", headers=headers, environ_base={"REMOTE_ADDR": addr})


print("source address")
check("the LAN gets the page",
      client.get("/", environ_base={"REMOTE_ADDR": "192.168.50.20"}).status_code == 200)
check("the Tailnet gets the page",
      client.get("/", environ_base={"REMOTE_ADDR": "100.90.1.2"}).status_code == 200)
check("the internet gets a 404",
      client.get("/", environ_base={"REMOTE_ADDR": "93.184.215.14"}).status_code == 404)
check("a POST from the internet is refused before it reaches the route",
      post("93.184.215.14").status_code == 404)

print("cross-site posts")
check("the page's own fetch (same-origin) launches", post(**{"Sec-Fetch-Site": "same-origin"}).status_code == 200)
check("another site's page cannot", post(**{"Sec-Fetch-Site": "cross-site"}).status_code == 403)
check("nor a sibling host on the same domain", post(**{"Sec-Fetch-Site": "same-site"}).status_code == 403)
check("a bookmark or typed URL is fine", post(**{"Sec-Fetch-Site": "none"}).status_code == 200)
check("without Sec-Fetch-Site, a matching Origin is fine",
      post(Origin="http://localhost").status_code == 200)
check("without Sec-Fetch-Site, a foreign Origin is refused",
      post(Origin="https://evil.example").status_code == 403)
check("no browser headers at all (curl, a script) is fine", post().status_code == 200)
check("a cross-site GET of the page is fine: only presses are guarded",
      client.get("/", headers={"Sec-Fetch-Site": "cross-site"},
                 environ_base={"REMOTE_ADDR": "192.168.50.20"}).status_code == 200)

print("the real app")
real = create_app().test_client()
check("create_app installs the guard (internet POST refused)",
      real.post("/toggle-mute", environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code == 404)
check("...and refuses a cross-site launch",
      real.post("/start-cnn", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403)
check("...and caps the body",
      real.post("/toggle-mute", data="x" * (128 * 1024)).status_code == 413)

print(f"\n{len(checks)} checks passed.")
