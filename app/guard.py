"""Who may press the buttons.

There is no login, deliberately: this is a remote control for the living-room
TV, and anyone on the home network should be able to use it. Two things stop
"anyone on the home network" from meaning more than that:

- Source address. Flask binds 0.0.0.0 so the Pi's changing DHCP address never
  stops it from starting, so the check is per request instead. Loopback, the
  private ranges and Tailscale's 100.64.0.0/10 are let in. Anything else gets a
  404 -- which is what a stray port forward or UPnP mapping would deliver,
  since forwarding rewrites the destination and leaves the public source alone.

- Cross-site POSTs. /start-cnn and /toggle-mute accept plain form posts (the
  no-JS path), so without a check any web page someone in the house opens could
  submit one and drive the TV. Browsers say where a request came from in
  Sec-Fetch-Site, or failing that Origin; a POST marked as coming from another
  site is refused. A request that carries neither header is not from a browser
  page (curl, a script), and nothing can forge one on a visitor's behalf.
"""
import ipaddress
from urllib.parse import urlsplit

from flask import current_app, request

# Tailscale's addresses. Python does not count the CGNAT block as private; its
# IPv6 range is a ULA, which it does.
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def is_local(addr):
    """Loopback, the LAN or the Tailnet. Anything unparseable is not."""
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip in TAILSCALE


def is_cross_site():
    """True when a browser says this request came from another site's page."""
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        # "none" is the user typing the URL or using a bookmark.
        return fetch_site not in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if origin:
        return urlsplit(origin).netloc != request.host
    return False


def install(app):
    @app.before_request
    def _guard():
        if not is_local(request.remote_addr):
            return "Not found", 404
        if request.method == "POST" and is_cross_site():
            return "Forbidden", 403
        # Flask applies MAX_CONTENT_LENGTH only when a route reads the body, and
        # these routes never do, so the cap has to be checked here to mean anything.
        limit = current_app.config.get("MAX_CONTENT_LENGTH")
        if limit and (request.content_length or 0) > limit:
            return "Too large", 413
