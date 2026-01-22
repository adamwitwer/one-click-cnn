#!/usr/bin/env python3
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

OAUTH_AUTHORIZE_URL = "https://api.smartthings.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://api.smartthings.com/oauth/token"
TOKEN_FILE = os.path.expanduser("~/.smartthings_tokens.json")


def _load_env() -> None:
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(root_dir, ".env"))


def _build_auth_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    return (
        f"{OAUTH_AUTHORIZE_URL}?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
        f"&state={state}"
    )


def _exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    resp = requests.post(
        OAUTH_TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text}")
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
    }


def _save_tokens(tokens: dict) -> None:
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f)
    os.replace(tmp, TOKEN_FILE)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        error = params.get("error", [None])[0]

        self.server.auth_result = {"code": code, "state": state, "error": error}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Auth received. You can close this tab.")


def _run_local_server(host: str, port: int, expected_state: str, timeout: int) -> str:
    httpd = HTTPServer((host, port), _CallbackHandler)
    httpd.auth_result = None

    def serve():
        httpd.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    start = time.time()
    while time.time() - start < timeout:
        result = httpd.auth_result
        if result:
            httpd.shutdown()
            if result.get("error"):
                raise RuntimeError(f"Authorization failed: {result['error']}")
            if result.get("state") != expected_state:
                raise RuntimeError("State mismatch in callback")
            if not result.get("code"):
                raise RuntimeError("Missing authorization code")
            return result["code"]
        time.sleep(0.2)

    httpd.shutdown()
    raise TimeoutError("Timed out waiting for authorization callback")


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartThings OAuth helper")
    parser.add_argument("--redirect-uri", help="Redirect URI registered in SmartThings")
    parser.add_argument("--scope", help="OAuth scope", default=os.getenv("SMARTTHINGS_SCOPE", "r:devices:* x:devices:*") )
    parser.add_argument("--manual", action="store_true", help="Paste authorization code instead of running local server")
    parser.add_argument("--timeout", type=int, default=180, help="Callback wait timeout in seconds")
    args = parser.parse_args()

    _load_env()
    client_id = os.getenv("SMARTTHINGS_CLIENT_ID")
    client_secret = os.getenv("SMARTTHINGS_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError("Missing SMARTTHINGS_CLIENT_ID or SMARTTHINGS_CLIENT_SECRET in .env")

    redirect_uri = args.redirect_uri or os.getenv("SMARTTHINGS_REDIRECT_URI") or "http://localhost:8000/callback"
    state = str(int(time.time()))
    auth_url = _build_auth_url(client_id, redirect_uri, args.scope, state)

    print("Open this URL to authorize SmartThings:")
    print(auth_url)

    if args.manual:
        code = input("Paste the code parameter from the redirect URL: ").strip()
    else:
        parsed = urlparse(redirect_uri)
        if parsed.scheme not in ("http", "https"):
            raise RuntimeError("Redirect URI must be http or https for local callback")
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        if parsed.path != "/callback":
            raise RuntimeError("Redirect URI path must be /callback for auto-capture")
        code = _run_local_server(host, port, state, args.timeout)

    tokens = _exchange_code(client_id, client_secret, code, redirect_uri)
    _save_tokens(tokens)
    print(f"Saved tokens to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
