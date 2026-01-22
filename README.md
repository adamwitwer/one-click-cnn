# One-Touch CNN 🧞📺

A modern, Flask-based web application that simplifies your TV experience. Launch the CNN app on your Roku and toggle your TV's mute status—all with a single tap from your smartphone.

## ✨ Features

-   **One-Touch Launch**: Instantly start the CNN app on your Roku device.
-   **Mute Toggle**: Quickly mute or unmute your Samsung TV via SmartThings.
-   **Smart Automation**: Automatically dismisses "Are you still watching?" overlays and other pop-ups.
-   **Responsive Design**: Beautiful, dark-mode interface that looks great on any mobile device.
-   **Real-time Feedback**: Visual loading indicators keep you informed while the magic happens.

## 🛠️ Prerequisites

-   **Python 3.8+**
-   **Roku Device**: TV or Streaming Stick (Developer mode not required).
-   **Samsung TV** (Optional): For mute functionality via SmartThings.

## 🚀 Installation

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

## ⚙️ Configuration

1.  Create a `.env` file in the root directory:
    ```bash
    cp .env.example .env
    ```

2.  Update the `.env` file with your device details:
    ```ini
    # Roku Configuration
    ROKU_IP=192.168.1.100  # Find this in Roku Settings > Network

    # SmartThings Configuration (Optional - for TV control)
    SMARTTHINGS_CLIENT_ID=your_client_id
    SMARTTHINGS_CLIENT_SECRET=your_client_secret
    SMARTTHINGS_TV_DEVICE_ID=your_device_id
    ```

    *Note: SmartThings tokens are managed automatically after the first run.*

## 🔐 SmartThings OAuth Helper

If you need to (re)authorize SmartThings on a new machine, use the helper script:

```bash
python3 scripts/smartthings_auth.py
```

This prints an authorization URL. Open it in your browser, log in, and approve the request. The script will save tokens to `~/.smartthings_tokens.json`.

Notes:
- Ensure the redirect URI you registered in SmartThings matches the script (default: `http://localhost:8000/callback`).
- To override, use `SMARTTHINGS_REDIRECT_URI` in `.env` or pass `--redirect-uri`.
- If you can’t run a local callback, use manual mode:
  ```bash
  python3 scripts/smartthings_auth.py --manual
  ```

## 📱 Usage

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
    Open your web browser and navigate to `http://localhost:5000` (or your server's IP address).

3.  **Add to Home Screen:**
    For the best experience on iOS/Android, use "Add to Home Screen" to install it as a web app.

## 🌐 Access Outside Your LAN (Tailscale)

If you run this on a Raspberry Pi with Tailscale already installed, you can access the app securely from anywhere without opening ports.

1.  **Start the app (binds to all interfaces):**
    ```bash
    ./run.sh
    ```

2.  **Find your Pi's Tailscale IP:**
    ```bash
    tailscale ip -4
    ```

3.  **Open the app from another device on your Tailscale network:**
    ```
    http://<tailscale-ip>:5000
    ```

*Tip: If you want a nicer URL, consider Tailscale's built-in `serve` feature or MagicDNS.*
