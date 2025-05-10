import requests
import time
from flask import render_template_string

ROKU_IP = "192.168.1.247"
YOUTUBE_TV_APP_ID = "195316"

HTML_PAGE = """
<!doctype html>
<html>
  <head><title>Roku Remote</title></head>
  <body style="font-family: sans-serif; text-align: center; margin-top: 4em;">
    <h1>📺 Roku Control</h1>
    <form action="/start-yttv" method="post">
      <button type="submit" style="font-size: 2em; padding: 1em 2em;">▶️ Start YouTube TV</button>
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
        time.sleep(6)
        requests.post(f"http://{ROKU_IP}:8060/keypress/Up")
        time.sleep(0.5)
        requests.post(f"http://{ROKU_IP}:8060/keypress/Select")
        time.sleep(0.5)
        requests.post(f"http://{ROKU_IP}:8060/keypress/Mute")
        return "✅ YouTube TV launched and overlay cleared."
