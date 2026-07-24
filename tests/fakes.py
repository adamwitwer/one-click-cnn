"""Shared test doubles.

The tests never touch real hardware: Roku and SmartThings HTTP calls go
through a fake `requests`, and the local TV functions are replaced outright.
"""


class Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, code, text="", data=None):
        self.status_code = code
        self.text = text
        self._data = data
        self.ok = 200 <= code < 300

    def json(self):
        return self._data


class FakeRoku:
    """Serves /launch/… and /query/active-app.

    `foreground_after` models CNN taking a variable time to come up — the
    behaviour the fixed 12s sleep used to get wrong.
    """

    def __init__(self, cnn_app_id, foreground_after=3):
        self.cnn_app_id = cnn_app_id
        self.foreground_after = foreground_after
        self.polls = 0
        self.launches = 0
        self.active = "0"

    def post(self, url):
        self.launches += 1
        return Resp(200)

    def get(self, url):
        self.polls += 1
        if self.polls >= self.foreground_after:
            self.active = self.cnn_app_id
        return Resp(200, f'<active-app><app id="{self.active}">App</app></active-app>')


def roku_only_requests(roku, forbid=("smartthings",)):
    """A `requests` module stand-in that serves Roku and rejects anything else.

    Used by the local-backend tests to prove no cloud call is made.
    """
    import types

    def post(url, **kwargs):
        for term in forbid:
            assert term not in url, f"unexpected call to {url}"
        return roku.post(url)

    def get(url, **kwargs):
        for term in forbid:
            assert term not in url, f"unexpected call to {url}"
        return roku.get(url)

    return types.SimpleNamespace(post=post, get=get, RequestException=Exception)


def wait_for_launch(routes, real_sleep, timeout=10.0):
    """Block until the background launch worker finishes.

    /start-cnn returns before muting completes, so asserting straight after
    the POST races the worker.
    """
    deadline = timeout
    while deadline > 0:
        with routes._launch_lock:
            if not routes._launch_state["in_progress"]:
                break
        real_sleep(0.02)
        deadline -= 0.02
    with routes._launch_lock:
        return dict(routes._launch_state)
