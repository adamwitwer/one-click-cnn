# One-Touch CNN

A Flask-based web application that simplifies your TV experience. Launch the CNN app on your Roku and toggle your TV's mute status—all with a single tap from your smartphone.

Everything runs on your own network: the Roku is controlled over its local HTTP API, and the TV over its own LAN interfaces. No cloud account or paid API is required.

## Features

-   **One-Touch Launch**: Start the CNN app on your Roku device with a single button press.
-   **Mute Toggle**: Mute or unmute your Samsung TV — locally over the LAN (free) or via SmartThings.
-   **Auto-Mute on Launch**: Waits for CNN to actually load, then mutes and verifies it took effect.
-   **Live Status**: Polls your TV for power and mute state, updating the UI automatically.
-   **PWA Support**: Install as a home-screen web app on iOS/Android for a native feel.
-   **Responsive Design**: Dark-mode interface optimized for mobile.

## Prerequisites

-   **Python 3.8+**
-   **Roku Device**: TV or Streaming Stick on your local network.
-   **Samsung TV** (Optional): For mute/unmute. Controlled locally over the LAN by default — no
    account or cloud API needed, but it must be on the same network and paired once via
    `./run.sh --pair`.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/adamwitwer/one-click-cnn.git
    cd one-click-cnn
    ```

2.  **Set up a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

1.  Create a `.env` file in the root directory:
    ```bash
    cp .env.example .env
    ```

2.  Update the `.env` file with your device details:
    ```ini
    # Flask runtime config
    FLASK_APP=app:create_app
    FLASK_RUN_HOST=0.0.0.0
    FLASK_RUN_PORT=5050

    # Roku config
    ROKU_IP=192.168.1.100

    # Mute backend: "local" (free, direct over the LAN) or "smartthings" (cloud)
    TV_BACKEND=local
    TV_IP=            # blank = autodiscover on this subnet

    # SmartThings Configuration (only for TV_BACKEND=smartthings)
    SMARTTHINGS_CLIENT_ID=your_client_id
    SMARTTHINGS_CLIENT_SECRET=your_client_secret
    SMARTTHINGS_TV_DEVICE_ID=your_device_id
    ```

    > **Note:** Find your Roku IP in Roku Settings > Network. See
    > [Avoiding the SmartThings API fee](#avoiding-the-smartthings-api-fee-local-backend) for the
    > one-time TV pairing that `TV_BACKEND=local` needs.

## SmartThings OAuth Setup (optional fallback)

**Only needed for `TV_BACKEND=smartthings`.** The default `local` backend needs no account, no
OAuth, and no cloud — see [Switching to `local`](#switching-to-local).

To (re)authorize SmartThings on a machine:

```bash
./run.sh --auth
```

This prints an authorization URL. Open it in your browser, log in, and approve the request. The script saves tokens to `~/.smartthings_tokens.json`.

Notes:
- Ensure the redirect URI you registered in SmartThings matches the script (default: `http://localhost:8000/callback`).
- To override, use `SMARTTHINGS_REDIRECT_URI` in `.env` or pass `--redirect-uri`.
- If you can't run a local callback, use manual mode:
  ```bash
  ./venv/bin/python scripts/smartthings_auth.py --manual
  ```
  (Use the venv's interpreter, not a bare `python3` — the dependencies live in the venv.)

> **Token lifetime:** SmartThings refresh tokens are single-use and expire after roughly 30 days
> of inactivity. The app rotates them automatically whenever it talks to the API, but if the app
> sits unused for a month you'll need to re-authorize. The UI says so explicitly
> ("SmartThings needs re-authorization") rather than reporting the TV as off.
>
> Run only **one** host against a given set of tokens. Because each refresh invalidates the
> previous refresh token, a laptop and a Pi sharing `~/.smartthings_tokens.json` credentials will
> knock each other offline.

## Troubleshooting

**CNN launches but the TV doesn't mute.** Both backends wait for CNN to actually reach the
foreground (polling Roku's `/query/active-app`) rather than sleeping a fixed guess, then mute and
read the state back to confirm. If it can't be confirmed the home screen says so instead of
claiming success — the old code reported "launched successfully" either way, which is what made
this fail silently.

- On `local`: check `./run.sh --pair --check` (or `roku-cnn.py --check` for the cron deployment).
  If **Paired: no**, re-run `./run.sh --pair`. If the TV was replaced or changed IP, clear `TV_IP`
  in `.env` to let it rediscover.
- On `smartthings`: an HTTP 200 there means "accepted", not "the TV did it", so the app retries up
  to four times with backoff. Look for `Mute verification attempt` in the log.

**Everything reports "TV appears to be off".** Usually the TV genuinely is in standby. On
`smartthings`, if dead tokens are the cause instead you'll see the re-authorization message — run
`./run.sh --auth`.

**The TV mutes and immediately unmutes.** Only possible with `TV_MUTE_READBACK=off`, where mute is
a blind toggle. Set it back to `auto` if your TV reports state reliably.

## Usage

1.  **Start the application:**
    ```bash
    ./run.sh
    ```
    To override the port:
    ```bash
    PORT=8080 ./run.sh
    ```
    To (re)authorize SmartThings:
    ```bash
    ./run.sh --auth
    ```
    To pair with the TV for local (free) mute control:
    ```bash
    ./run.sh --pair
    ```

2.  **Access the interface:**
    Open your web browser and navigate to `http://localhost:5050` (or your server's IP address). The default port is **5050** as set in `.env`.

3.  **Add to Home Screen:**
    For the best experience on iOS/Android, use "Add to Home Screen" to install it as a web app.

## Scheduled Auto-Start (Cron)

The `scripts/roku-cnn.py` script launches CNN and mutes the TV headlessly — no web server needed. It reads configuration from a `.env` file in the same directory (or the repo root).

1.  **Copy the script, its TV helper, and `.env` to your server:**
    ```bash
    cp scripts/roku-cnn.py /home/adam/.scripts/roku-cnn.py
    cp app/tv_local.py     /home/adam/.scripts/tv_local.py
    cp .env                /home/adam/.scripts/.env
    ```
    `tv_local.py` is only needed for `TV_BACKEND=local`, but copying it always keeps the two
    deployments identical. The script finds it either beside itself or in the repo's `app/`.

    This `.env` is separate from the repo's and is not tracked by git, so it won't pick up changes
    from a `git pull`. Set `TV_IP` in it — otherwise every cron run starts with a ~6s subnet scan.

2.  **Install dependencies in the server's venv:**
    ```bash
    /home/adam/.scripts/venv/bin/pip install requests python-dotenv samsungtvws
    ```
    Pair the server with the TV once — pairing tokens are per-host, so the Pi needs its own even
    if your laptop is already paired:
    ```bash
    cd /path/to/repo && ./run.sh --pair
    ```
    Run it as the same user cron runs as: the token lands in that user's `~`, and both the web app
    and the cron job read it from there, so one pairing covers both.

3.  **Verify the deployment** (checks Roku, TV, and pairing without launching or muting):
    ```bash
    /home/adam/.scripts/venv/bin/python /home/adam/.scripts/roku-cnn.py --check
    ```

4.  **Add a cron entry** (e.g. daily at 7 PM):
    ```bash
    crontab -e
    ```
    ```
    00 19 * * * /home/adam/.scripts/venv/bin/python /home/adam/.scripts/roku-cnn.py >> /home/adam/roku-cnn.log 2>&1
    ```

## Remote Access via Tailscale

If you run this on a Raspberry Pi (or any server) with Tailscale installed, you can access the app securely from anywhere without opening ports.

1.  **Start the app (binds to all interfaces by default):**
    ```bash
    ./run.sh
    ```

2.  **Find your device's Tailscale IP:**
    ```bash
    tailscale ip -4
    ```

3.  **Open the app from another device on your Tailscale network:**
    ```
    http://<tailscale-ip>:5050
    ```

*Tip: For a nicer URL, consider Tailscale's built-in `serve` feature or MagicDNS.*

## Avoiding the SmartThings API fee (local backend)

Samsung is moving the SmartThings API to paid tiers in October 2026 (free through September 2026);
personal use is expected to cost **$4.99/month**. SmartThings is only needed for the mute — Roku
launching is local and unmetered — so the app can talk to the TV directly instead.

Set `TV_BACKEND` in `.env`:

| Value | Mute path | Cost |
| --- | --- | --- |
| `local` | Tizen websocket + UPnP, direct over the LAN | free |
| `smartthings` | Samsung cloud API | paid from Oct 2026 |

### Switching to `local`

1. Turn the TV on, then run the pairing helper **while standing in front of it**:
   ```bash
   ./run.sh --pair
   ```
   The TV shows an "Allow this device to connect?" prompt — accept it with the remote. The token
   is saved to `~/.samsungtv_token.txt` and is never needed again.

2. Set `TV_BACKEND=local` in `.env` and restart the app.

`./run.sh --pair --check` reports address, power, pairing and readback status without changing
anything.

### How the local backend works

- **Control:** `KEY_MUTE` over the Tizen websocket on port 8002.
- **Readback:** UPnP `RenderingControl` `GetMute` on port 9197, used to verify the mute landed.
  `SetMute` is *not* used — a UN50TU690TFXZA answers it with UPnP error 501 ("Action Failed")
  outside an active DLNA session, so control goes through the websocket instead.
- **Power:** the Tizen info endpoint on port 8001 reports `PowerState`.

Because `KEY_MUTE` toggles rather than sets, the app reads the current state first and only sends
the key if the TV isn't already where it should be — so a retry can't undo a mute that worked.

If your TV reports mute state unreliably, set `TV_MUTE_READBACK=off`; mute then becomes an
unverified toggle (and launching while already muted would unmute).

**TV address:** leave `TV_IP` blank to autodiscover on startup (a ~6s subnet scan, cached for the
life of the process). Setting `TV_IP` explicitly — ideally alongside a DHCP reservation — skips it.

### Polling

Neither backend polls while the page is hidden. On `local`, status is read every 10s and is always
fresh — the calls are free and answer in well under a second. On `smartthings`, polling drops to
20s and only forces a device refresh on one poll in three (roughly 80 API calls per hour of active
screen time); raise `poll_ms` in `app/routes.py` if the published limits turn out to be tight.

## Project Structure

```
one-click-cnn/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── routes.py            # All routes and device control logic
│   ├── tv_local.py          # Local TV control (websocket mute + UPnP readback)
│   ├── static/              # CSS, icons, PWA manifest
│   └── templates/           # Jinja2 templates (base, index, message)
├── scripts/
│   ├── roku-cnn.py          # Headless cron script (launch + mute)
│   ├── pair-tv.py           # One-time local TV pairing helper
│   └── smartthings_auth.py  # OAuth authorization helper
├── .env.example             # Environment variable template
├── AGENTS.md                # Notes for developing on this repo
├── requirements.txt         # Python dependencies
└── run.sh                   # Startup script
```

## Development

See [AGENTS.md](AGENTS.md) for architecture notes, the device quirks behind the current design
(including approaches that look right but don't work on this hardware), and how to test without
touching real hardware.
