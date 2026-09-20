# Working on this repo

A small Flask app that launches CNN on a Roku and mutes a Samsung TV. Two deployments, one
codebase: the web app, and a headless cron script on a Raspberry Pi.

## Setup

Always use the venv's interpreter — a bare `python3` will fail with `ModuleNotFoundError`:

```bash
./venv/bin/python …          # not python3
./run.sh                     # start the app (activates the venv itself)
./run.sh --pair              # one-time TV pairing
./run.sh --pair --check      # report TV address, power, pairing, readback
./venv/bin/python scripts/roku-cnn.py --check   # verify a cron deployment
```

Run the tests before and after any change to the mute or launch paths:

```bash
./venv/bin/python tests/run.py
```

No linter is configured.

## Architecture

```
routes.py     Flask routes + backend dispatch + Roku control
tv_local.py   Local TV control (websocket mute, UPnP readback, discovery)
roku-cnn.py   Standalone cron equivalent of the launch flow
```

`TV_BACKEND` selects how the TV is muted: `local` (default, free) or `smartthings` (cloud, paid
tiers from October 2026). Routes never call a backend directly — they go through the dispatch
layer in `routes.py` (`get_tv_status`, `ensure_muted`, `toggle_mute`, `refresh_tv_status`). Add
new TV operations there rather than branching on `TV_BACKEND` at the call site.

`roku-cnn.py` is deployed by copying it plus `tv_local.py` into a directory on the Pi, so it must
keep working as a standalone file. It loads `tv_local` from beside itself or from `app/`. Don't
give it imports that assume a repo checkout.

## Device behavior worth knowing

These were established by testing against real hardware (Roku Streaming Stick 4K, Samsung
UN50TU690TFXZA). They are the reasons the code looks the way it does.

- **A SmartThings HTTP 200 means "accepted", not "done."** Commands are delivered to the device
  asynchronously and can be dropped silently. This is what made auto-mute unreliable: the app
  slept 12s, fired mute, got a 200, and reported success. Always verify by reading state back.
- **UPnP `SetMute` does not work on this TV.** Port 9197 exposes `RenderingControl` and answers
  `GetMute`/`GetVolume` correctly, but `SetMute` returns UPnP error 501 "Action Failed" outside an
  active DLNA session. Don't reach for it again — it looks perfect and isn't.
- **`KEY_MUTE` over the websocket is a toggle, not a set.** So `ensure_muted()` reads state first
  and sends the key only if the TV isn't already where it should be. Never send it in a blind
  retry loop; that can undo a mute that already succeeded.
- **UPnP readback lags the key press.** The TV acts on `KEY_MUTE` immediately but can keep
  reporting the old state for several seconds. Reading once after a fixed sleep and calling a
  stale answer "it didn't work" is how a second `KEY_MUTE` went out and unmuted a TV that had
  muted — reported to the user as success, TV audibly unmuted. Verify by polling until the state
  changes (`_await_mute`), never with a single read.
- **A dropped `GetMute` is not evidence of anything.** The UPnP endpoint drops the odd request
  while the TV is busy, and is absent entirely for the first seconds after the TV wakes. One
  unanswered call used to mean "could not reach the TV to mute it" on a TV that was muting fine.
  `read_mute()` retries; `get_mute()` is the raw single shot.
- **A TV in standby is off the LAN, not slow.** Ports 8001/9197/8002 refuse connections instantly
  (errno 64, "Host is down") rather than timing out, so probing a sleeping TV is cheap. It comes
  back several seconds after the Roku launch wakes it over HDMI-CEC, which is exactly when the
  launch-time mute fires — hence `wait_awake()` before muting.
- **The TV counts websocket clients.** `send_key` closes its connection explicitly; one left for
  the garbage collector still occupies a slot, and the next connection is refused.
- **A mute can slip back on its own.** CNN's audio coming up (or an HDMI-CEC volume event) can
  undo a mute placed the instant the app reaches the foreground. The launch paths pass
  `hold=True`, which re-checks for a few seconds and re-asserts once.
- **Websocket pairing is per-host and interactive.** The first connection raises a prompt on the
  TV that a human must accept with the remote. Tokens live in `~/.samsungtv_token.txt` and do not
  transfer between machines. Unattended pairing attempts just time out.
- **Roku's `/keypress/VolumeMute` appears to do nothing here.** The Roku returns 200 but the TV's
  reported mute state doesn't move. Tested; not a viable control path.
- **CNN takes a variable time to reach the foreground.** Poll Roku's `/query/active-app` until the
  app ID matches rather than sleeping a fixed interval.

## Conventions

- **Never report success you haven't verified — and never report failure you haven't verified
  either.** `ensure_muted()` is tri-state: `True` (the TV reported the state we wanted), `False`
  (it reported the opposite), `None` (the key went out, the TV never answered). The two bugs sit
  on either side of this: reporting unverified as success hid a mute that never happened, and
  reporting it as failure put "Could not reach the TV to mute it" on screen while the TV sat there
  muted. `None` is logged, not surfaced. Keep the three apart at every layer — `routes.ensure_muted`,
  `_launch_worker`, `_launch_state["muted"]`, and the `launch.muted === false` test in the page.
- **Read config lazily.** `tv_local` is imported before `load_dotenv()` runs, so anything captured
  at module import misses `.env`. Use accessor functions (`token_file()`, `mute_readback()`), not
  module-level constants, for anything env-derived. `TV_IP` is likewise read per call.
- **Device waits are attempt counts, not wall-clock deadlines.** `READ_ATTEMPTS`, `SETTLE_ATTEMPTS`,
  `AWAKE_ATTEMPTS` and friends in `tv_local`. A deadline loop spins at full speed in the tests,
  which neutralise `time.sleep`; a counted loop just finishes.
- **Long device work goes off the request thread.** Verified muting outlasts what a browser will
  hold a POST open for. `/start-cnn` starts a background worker and returns immediately; the page
  posts via `fetch` (header `X-Requested-With: fetch` → JSON ack; a no-JS form post still gets
  `message.html`) and keeps one spinner up while polling `/tv-status`, which reports
  `launch.in_progress` / `launch.muted` / `launch.detail`.
- **Status polling stays off the TV while a mute routine is running.** `/tv-status` returns
  `"unknown"` for the duration instead of querying: the poll hits the same UPnP service the mute
  is verifying against, and a readback lost to that contention is a mute that misses. `"unknown"`
  also covers a TV that is on but not answering — the page holds its current display rather than
  flapping to "TV appears to be off" and taking the mute button away from a working TV.
- **The TV's reported state wins over the worker's return value in the UI.** So the home page
  shows the mute-failed banner only when `launch.muted === false` *and* `status !== 'muted'`.
  Don't surface `launch.detail` unconditionally, and don't loosen the `=== false` to a falsy test;
  either resurrects the false "Could not reach the TV to mute it" that appeared while the TV was,
  in fact, muted.
- **Distinguish failure modes in user-facing text.** Dead OAuth tokens must not render as "TV
  appears to be off" — that masked a re-auth requirement as a hardware problem for weeks.
- **Guard token refresh with `_TOKEN_LOCK`.** SmartThings refresh tokens are single-use; two
  concurrent refreshes invalidate each other. The same applies across hosts, which is why only one
  machine should hold a given set of SmartThings credentials.
- **Local calls are free; cloud calls are metered.** Polling cadence is backend-aware (`poll_ms`
  in `routes.py`). Don't add unconditional polling to the SmartThings path.

## Tests

```
tests/run.py                        runner — runs each suite in its own process
tests/fakes.py                      shared doubles (FakeRoku, fake requests, launch waiter)
tests/test_local_backend.py         toggle semantics, verification, launch flow
tests/test_smartthings_backend.py   async commands, retries, token failures
```

No hardware or network is touched: Roku and SmartThings calls go through a fake `requests`, and
`tv_local`'s `send_key`/`get_mute`/`get_power` are replaced. The local suite additionally asserts
that *no* SmartThings URL is ever requested. `TV_IP` is set to `192.0.2.1` (TEST-NET-1) so a
missed patch fails fast instead of reaching a real device.

Each suite runs in its own process because `TV_BACKEND` is resolved when `app.routes` is imported.
`run.py` also strips `TV_*` from the environment so a developer's `.env` can't steer results.

The suites are worth extending rather than replacing — they encode the specific regressions this
app has already suffered. Verified by mutation: removing the "already muted" guard, or returning
success from an unverified mute, each makes a named check fail.

The fake TV in `test_local_backend.py` models the *reporting* failures, because that is where
every mute bug has lived: `readback` (UPnP silent), `readback_fails` (silent for N calls, then
back), `lag_after_key` (reports the old state for N reads after acting), `drift` (unmutes itself
once), `waking` (off the LAN for N power checks), `reachable` (websocket refuses).

Two gotchas when adding tests:

- Patching `time.sleep` to speed up device waits patches the *shared* module, so your own wait
  loops become no-ops too. Capture `real_sleep = time.sleep` before patching (see
  `fakes.wait_for_launch`).
- `/start-cnn` returns before the mute finishes. Wait on `routes._launch_state["in_progress"]`
  under `routes._launch_lock` instead of asserting straight after the POST.

## Hardware changes

If the TV is replaced, expect to re-verify the assumptions above — `SetMute` may work on another
model, which would be simpler than the toggle-and-verify dance. `scripts/pair-tv.py` reports
whether control and readback both function, and is the fastest way to find out.
