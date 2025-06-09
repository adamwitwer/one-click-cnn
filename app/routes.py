import requests
import time
from flask import render_template_string

ROKU_IP = "192.168.50.129"
YOUTUBE_TV_APP_ID = "195316"

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

def register_routes(app):
    @app.route("/")
    def home():
        return render_template_string(HTML_PAGE)

    @app.route("/start-yttv", methods=["POST"])
    def launch_yttv():
        requests.post(f"http://{ROKU_IP}:8060/launch/{YOUTUBE_TV_APP_ID}")
        time.sleep(10)
        requests.post(f"http://{ROKU_IP}:8060/keypress/Up")
        time.sleep(6)
        requests.post(f"http://{ROKU_IP}:8060/keypress/Select")

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
