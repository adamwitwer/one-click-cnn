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
layer in `routes.py` (`get_tv_status`, `ensure_muted`, `toggle_mute`, `wait_for_tv`,
`refresh_tv_status`). Add
new TV operations there rather than branching on `TV_BACKEND` at the call site.

Both deployments run on the same Pi and are updated separately — the web app from a git checkout
behind a systemd unit, the cron script from copied files. A change to `tv_local.py` or the mute
path needs both. The README's [Deployments](README.md#deployments) section has the commands; the
failure mode is updating one and leaving the other running old code, which looks fine until it
doesn't.

`roku-cnn.py` is deployed by copying it plus `tv_local.py` into a directory on the Pi, so it must
keep working as a standalone file. It loads `tv_local` from beside itself or from `app/`. Don't
give it imports that assume a repo checkout.

## Device behavior worth knowing

These were established by testing against real hardware (Roku Streaming Stick 4K, Samsung
UN50TU690TFXZA). They are the reasons the code looks the way it does. Where a bullet is marked
**(suspected)** it is an inferred failure mode the code defends against but which has *not* been
caught in the act — treat it as a hypothesis, and if you get the chance to confirm or kill one,
say so here. Don't promote one to fact without evidence.

- **A SmartThings HTTP 200 means "accepted", not "done."** Commands are delivered to the device
  asynchronously and can be dropped silently. This is what made auto-mute unreliable: the app
  slept 12s, fired mute, got a 200, and reported success. Always verify by reading state back.
- **UPnP `SetMute` does not work on this TV.** Port 9197 exposes `RenderingControl` and answers
  `GetMute`/`GetVolume` correctly, but `SetMute` returns UPnP error 501 "Action Failed" outside an
  active DLNA session. Don't reach for it again — it looks perfect and isn't.
- **`KEY_MUTE` over the websocket is a toggle, not a set.** So `ensure_muted()` reads state first
  and sends the key only if the TV isn't already where it should be. Never send it in a blind
  retry loop; that can undo a mute that already succeeded.
- **UPnP readback lags the key press (suspected).** The best-fitting explanation for "the mute
  just doesn't work maybe a quarter of the time": if the TV acts on `KEY_MUTE` immediately but
  keeps reporting the old state for a few seconds, the old single read 1.5s later saw a stale
  "unmuted", called the key press a failure, and sent a second `KEY_MUTE` — unmuting the TV that
  had just muted, then reporting success. Replaying the old algorithm against a TV that lags
  three reads does exactly that (returns `True`, TV left unmuted, two keys sent), so the bug is
  real *if* the lag is; the lag itself hasn't been caught on hardware. `scripts/pair-tv.py`
  already warned about "stale readback" before any of this, which is where the suspicion comes
  from. Either way, polling until the state changes (`_await_mute`) is strictly better than one
  read: it costs nothing when the TV is prompt.
- **A dropped `GetMute` is not evidence of anything.** The UPnP endpoint is absent for the first
  seconds after the TV wakes, and one unanswered call used to mean "could not reach the TV to
  mute it". Caught in the cron log (Pi, 2026-09-19 18:30): CNN reached the foreground at
  18:30:02, `GetMute` was refused at 18:30:04 (errno 111) because the TV was still coming up from
  the HDMI-CEC wake, and the run logged `Failed to mute TV` having sent one blind key.
  `read_mute()` retries; `get_mute()` is the raw single shot.
- **A TV in standby is off the LAN, not slow.** Measured: 20 consecutive `GetMute` calls to a
  sleeping TV all failed in 0.00s (errno 64 "Host is down" from macOS, errno 111 "Connection
  refused" from the Pi) rather than burning their timeout. So probing a sleeping TV is cheap, and
  a fast failure says nothing about how long it will take to come back. It rejoins the network
  seconds after the Roku launch wakes it over HDMI-CEC, which is exactly when the launch-time
  mute fires — hence `wait_awake()` before muting.
- **The TV may count websocket clients (suspected).** `send_key` closes its connection
  explicitly rather than leaving it to the garbage collector. Samsung TVs are widely reported to
  cap concurrent remote clients, which would make a leaked connection cost the *next* key press —
  but that has not been reproduced here. Closing is correct hygiene regardless; don't cite this
  as the cause of a failed key without checking.
- **A mute can slip back on its own (suspected).** A mute placed the instant CNN reaches the
  foreground could be undone moments later by the app's own audio starting or an HDMI-CEC volume
  event. Not observed — the launch paths pass `hold=True`, which watches for a few seconds and
  re-asserts once, and the log line `TV drifted back to mute=False after the mute landed` is the
  thing to grep for to confirm it ever happens.
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
  `routes.toggle_mute`, `_launch_worker`, `_launch_state["muted"]`, the `is not False` test in the
  `/toggle-mute` route, and the `launch.muted === false` test in the page. A plain truthiness test
  anywhere on that path silently re-merges `None` with `False` and the false banner comes back.
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
- **A launch is in progress from the tap, not from the worker.** `/start-cnn` calls
  `claim_launch()` *before* the Roku request, which takes a couple of seconds when it has to wake
  the TV. Claiming after it left a window where a poll read "not launching", and the page dropped
  its spinner mid-launch to flash "CNN is running / TV appears to be off". The page guards the
  same window from its side: responses to polls sent before the tap (older `epoch`) or answered
  while the tap's POST is still out (`launchPending`) are discarded. A failed Roku launch must
  `abandon_launch()`, or every later tap is refused as a duplicate.
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
tests/test_local_backend.py         toggle semantics, readback failure modes, launch flow
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
success from an unverified mute, each makes a named check fail. The four checks added for the
readback failures were each confirmed to fail against the previous implementation before the fix
landed — replaying the old `ensure_muted()` against the same fakes leaves the TV unmuted while
returning `True`. If you rewrite the mute path, re-run that exercise rather than trusting a green
suite; these fakes are the only place the failure modes exist.

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
