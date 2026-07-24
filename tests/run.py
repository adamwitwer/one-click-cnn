#!/usr/bin/env python3
"""Run the test suites.

    ./venv/bin/python tests/run.py

Each suite runs in its own process: `TV_BACKEND` is resolved when
`app.routes` is imported, so the two backends can't share an interpreter.

No hardware and no network are involved — Roku and SmartThings calls go
through fakes, and the local TV functions are replaced.
"""
import os
import subprocess
import sys

SUITES = [
    ("local backend", "test_local_backend.py"),
    ("smartthings backend", "test_smartthings_backend.py"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    failed = []
    for name, filename in SUITES:
        # flush: child output goes straight to the terminal, so unflushed
        # parent output would appear after it.
        print(f"\n=== {name} ===", flush=True)
        env = dict(os.environ, PYTHONPATH=ROOT)
        # A developer's own .env must not steer the tests.
        for key in ("TV_BACKEND", "TV_IP", "TV_MUTE_READBACK", "TV_TOKEN_FILE"):
            env.pop(key, None)
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, filename)],
            env=env, cwd=ROOT,
        )
        if result.returncode != 0:
            failed.append(name)

    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"All {len(SUITES)} suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
