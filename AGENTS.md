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
- **Websocket pairing is per-host and interactive.** The first connection raises a prompt on the
  TV that a human must accept with the remote. Tokens live in `~/.samsungtv_token.txt` and do not
  transfer between machines. Unattended pairing attempts just time out.
- **Roku's `/keypress/VolumeMute` appears to do nothing here.** The Roku returns 200 but the TV's
  reported mute state doesn't move. Tested; not a viable control path.
- **CNN takes a variable time to reach the foreground.** Poll Roku's `/query/active-app` until the
  app ID matches rather than sleeping a fixed interval.

## Conventions

- **Never report success you haven't verified.** `ensure_muted()` returns `True` only on a
  confirmed state. When readback is unavailable it sends the key and returns `False` — the UI says
  "couldn't confirm" rather than claiming it worked. Preserve this; silent false success was the
  original bug.
- **Read config lazily.** `tv_local` is imported before `load_dotenv()` runs, so anything captured
  at module import misses `.env`. Use accessor functions (`token_file()`, `mute_readback()`), not
  module-level constants, for anything env-derived. `TV_IP` is likewise read per call.
- **Long device work goes off the request thread.** Verified muting outlasts what a browser will
  hold a POST open for. `/start-cnn` starts a background worker and returns immediately; the page
  polls `/tv-status`, which reports `launch.in_progress` / `launch.muted` / `launch.detail`.
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
