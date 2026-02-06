# One-Touch CNN

A Flask-based web application that simplifies your TV experience. Launch the CNN app on your Roku and toggle your TV's mute status—all with a single tap from your smartphone.

## Features

-   **One-Touch Launch**: Start the CNN app on your Roku device with a single button press.
-   **Mute Toggle**: Mute or unmute your Samsung TV via SmartThings.
-   **Auto-Mute on Launch**: Automatically mutes the TV after launching CNN.
-   **Live Status**: Polls your TV for power and mute state, updating the UI automatically.
-   **PWA Support**: Install as a home-screen web app on iOS/Android for a native feel.
-   **Responsive Design**: Dark-mode interface optimized for mobile.

## Prerequisites

-   **Python 3.8+**
-   **Roku Device**: TV or Streaming Stick on your local network.
-   **Samsung TV** (Optional): For mute/unmute functionality via SmartThings.

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

    # Roku config
    ROKU_IP=192.168.1.100

    # SmartThings Configuration (Optional - for TV mute control)
    SMARTTHINGS_CLIENT_ID=your_client_id
    SMARTTHINGS_CLIENT_SECRET=your_client_secret
    SMARTTHINGS_TV_DEVICE_ID=your_device_id
    ```

    > **Note:** Find your Roku IP in Roku Settings > Network. SmartThings tokens are managed automatically after initial authorization.

## SmartThings OAuth Setup

If you need to (re)authorize SmartThings on a new machine, use the helper script:

```bash
python3 scripts/smartthings_auth.py
```

This prints an authorization URL. Open it in your browser, log in, and approve the request. The script saves tokens to `~/.smartthings_tokens.json`.

Notes:
- Ensure the redirect URI you registered in SmartThings matches the script (default: `http://localhost:8000/callback`).
- To override, use `SMARTTHINGS_REDIRECT_URI` in `.env` or pass `--redirect-uri`.
- If you can't run a local callback, use manual mode:
  ```bash
  python3 scripts/smartthings_auth.py --manual
  ```

## Usage

1.  **Start the application:**
    ```bash
    ./run.sh
    ```
    To use a different port:
    ```bash
    PORT=5050 ./run.sh
    ```
    To (re)authorize SmartThings:
    ```bash
    ./run.sh --auth
    ```

2.  **Access the interface:**
    Open your web browser and navigate to `http://localhost:5000` (or your server's IP address). The default port is **5000** unless overridden with `PORT` or `FLASK_RUN_PORT`.

3.  **Add to Home Screen:**
    For the best experience on iOS/Android, use "Add to Home Screen" to install it as a web app.

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
    http://<tailscale-ip>:5000
    ```

*Tip: For a nicer URL, consider Tailscale's built-in `serve` feature or MagicDNS.*

## Project Structure

```
one-click-cnn/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── routes.py            # All routes and device control logic
│   ├── static/              # CSS, icons, PWA manifest
│   └── templates/           # Jinja2 templates (base, index, message)
├── scripts/
│   └── smartthings_auth.py  # OAuth authorization helper
├── .env.example             # Environment variable template
├── requirements.txt         # Python dependencies
└── run.sh                   # Startup script
```
