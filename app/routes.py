import requests
import time
import os
from flask import render_template_string

ROKU_IP = "192.168.50.129"
YOUTUBE_TV_APP_ID = "195316"
SMARTTHINGS_TOKEN = os.getenv("SMARTTHINGS_TOKEN")
SMARTTHINGS_TV_DEVICE_ID = os.getenv("SMARTTHINGS_TV_DEVICE_ID")

def mute_tv_smartthings():
    """Mute the Samsung TV via SmartThings API"""
    url = f"https://api.smartthings.com/v1/devices/{SMARTTHINGS_TV_DEVICE_ID}/commands"
    headers = {
        "Authorization": f"Bearer {SMARTTHINGS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "commands": [{
            "component": "main",
            "capability": "audioMute",
            "command": "mute"
        }]
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.status_code == 200

HTML_PAGE = """
<!doctype html>
<html>
  <head><title>Roku Remote</title></head>
  <link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon-96x96.png') }}" sizes="96x96" />
  <link rel="icon" type="image/svg+xml" href="{{ url_for('static', filename='favicon.svg') }}" />
  <link rel="shortcut icon" href="{{ url_for('static', filename='favicon.ico') }}" />
  <link rel="apple-touch-icon" sizes="180x180" href="{{ url_for('static', filename='apple-touch-icon.png') }}" />
  <meta name="apple-mobile-web-app-title" content="1CNN" />
  <link rel="manifest" href="{{ url_for('static', filename='site.webmanifest') }}" />
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
    <h1>📺 Roku Control</h1>
    <form action="/start-yttv" method="post">
      <button type="submit" style="
        font-size: 3em;
        padding: 2em 3em;
        background-color: #673ab7;
        color: white;
        border: none;
        border-radius: 12px;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.2);
        cursor: pointer;
        transition: transform 0.1s ease-in-out;
      " 
      onmouseover="this.style.transform='scale(1.05)'"
      onmouseout="this.style.transform='scale(1)'">
      ▶️ Start YouTube TV
      </button>
    </form>
  </body>
</html>
"""

def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}", flush=True)

def is_yttv_running():
    """Check if YouTube TV is currently the active app"""
    try:
        log("Checking current active app...")
        resp = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=5)
        if resp.status_code == 200:
            # Parse the XML response to get the app ID
            content = resp.text
            log(f"Active app response: {content}")
            
            # Look for the app ID in the response
            if f'id="{YOUTUBE_TV_APP_ID}"' in content:
                log("YouTube TV is already running!")
                return True
            else:
                log("YouTube TV is not currently active.")
                return False
        else:
            log(f"Failed to query active app: {resp.status_code}")
            return False
    except requests.RequestException as e:
        log(f"Failed to check active app: {e}")
        return False

def register_routes(app):
    @app.route("/")
    def home():
        return render_template_string(HTML_PAGE)

    @app.route("/start-yttv", methods=["POST"])
    def launch_yttv():
        log("Web request received to start YouTube TV")

        # Check if YTTV is already running
        if is_yttv_running():
            log("YouTube TV is already active. Skipping launch to avoid overlay confusion.")
            return render_template_string("""
            <html>
              <head>
                <meta http-equiv="refresh" content="2; url=/" />
                <title>Already Running</title>
              </head>
              <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
                <h1>📺 YouTube TV is already running</h1>
                <p>No action needed. Returning to control screen...</p>
              </body>
            </html>
            """)

        try:
            log("Launching YouTube TV...")
            resp = requests.post(f"http://{ROKU_IP}:8060/launch/{YOUTUBE_TV_APP_ID}", timeout=5)
            log(f"Launch response: {resp.status_code}")
        except requests.RequestException as e:
            log(f"Failed to launch YouTube TV: {e}")
            return render_template_string("""
            <html>
              <head>
                <meta http-equiv="refresh" content="3; url=/" />
                <title>Error</title>
              </head>
              <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
                <h1>❌ Error launching YouTube TV</h1>
                <p>Check the logs for details. Returning to control screen...</p>
              </body>
            </html>
            """)

        time.sleep(10)  # Give YTTV time to load

        try:
            log("Sending 'Up' command to dismiss overlay...")
            requests.post(f"http://{ROKU_IP}:8060/keypress/Up")
            time.sleep(6)  # Samsung TV OS hangs! Add 6s
            log("Sending 'Select' command to confirm overlay dismissal...")
            requests.post(f"http://{ROKU_IP}:8060/keypress/Select")
            log("Overlay dismissed. CNN should be full screen.")
        except requests.RequestException as e:
            log(f"Failed to send overlay dismissal commands: {e}")

        # Mute TV via SmartThings
        time.sleep(1)
        log("Muting TV via SmartThings...")
        if mute_tv_smartthings():
            log("TV muted successfully")
        else:
            log("Failed to mute TV via SmartThings")

        return render_template_string("""
        <html>
          <head>
            <meta http-equiv="refresh" content="2; url=/" />
            <title>Done!</title>
          </head>
          <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
            <h1>✅ YouTube TV launched</h1>
            <p>Returning to control screen...</p>
          </body>
        </html>
        """)
