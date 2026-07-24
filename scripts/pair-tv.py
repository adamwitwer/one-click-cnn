#!/usr/bin/env python3
"""One-time pairing for local Samsung TV control.

The TV shows an "Allow this device to connect?" prompt the first time a remote
client connects. Accept it with the TV remote; the resulting token is saved so
the app never needs to ask again.

Run this while you are standing in front of the TV, with the TV switched on.

Usage:
    python3 scripts/pair-tv.py            # pair, then verify with a mute test
    python3 scripts/pair-tv.py --check    # report status only, change nothing
"""
import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit(
        "This needs the project's virtualenv. Run one of:\n"
        "    ./run.sh --pair\n"
        "    ./venv/bin/python scripts/pair-tv.py"
    )

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(os.path.join(REPO_ROOT, ".env"))


def _import_tv_local():
    """From the repo's app/ package, or from beside this script on a server
    where only the scripts were deployed."""
    try:
        from app import tv_local
        return tv_local
    except ImportError:
        pass
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), "tv_local.py")
    if not os.path.exists(path):
        sys.exit("Could not find tv_local.py (expected in app/ or beside this script).")
    spec = importlib.util.spec_from_file_location("tv_local", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tv_local = _import_tv_local()


def check():
    ip = tv_local.tv_ip()
    if not ip:
        print("✗ No Samsung TV found. Set TV_IP in .env, or check the TV is on.")
        return False

    print(f"TV address:     {ip}")
    print(f"Power state:    {tv_local.get_power()}")
    print(f"Paired:         {'yes' if tv_local.paired() else 'no'} ({tv_local.token_file()})")

    mute = tv_local.get_mute()
    print(f"Mute readback:  {'unavailable' if mute is None else mute}")
    print(f"Volume:         {tv_local.get_volume()}")
    return True


def main():
    if not check():
        return 1

    if "--check" in sys.argv:
        return 0

    if tv_local.get_power() != "on":
        print("\n✗ Turn the TV on first, then run this again.")
        return 1

    print("\n" + "=" * 62)
    print("  WATCH THE TV SCREEN NOW.")
    print("  A prompt will ask whether to allow 'OneTouchCNN' to connect.")
    print("  Select Allow with the TV remote — you have about 30 seconds.")
    print("=" * 62 + "\n")
    time.sleep(2)

    before = tv_local.get_mute()
    if not tv_local.send_key("KEY_MUTE", timeout=45):
        print("\n✗ Pairing failed. Common causes:")
        print("   - The prompt wasn't accepted in time (just run this again)")
        print("   - The TV is on a different network than this machine")
        print("   - A stale denial: on the TV, Settings > General > External Device")
        print("     Manager > Device Connection Manager > Device List, remove")
        print("     'OneTouchCNN', then run this again")
        return 1

    print("✓ Key accepted — the TV authorized this client.")
    time.sleep(2)
    after = tv_local.get_mute()

    print(f"\nMute before: {before}   after: {after}")
    if before is None or after is None:
        print("\n⚠ Control works, but mute state can't be read back over UPnP.")
        print("  The app will still mute, but can't confirm it. Set")
        print("  TV_MUTE_READBACK=off in .env to silence the warnings.")
    elif before != after:
        print("\n✓ Readback works too — mute changes are verifiable.")
        print("  Restoring the previous state…")
        tv_local.send_key("KEY_MUTE")
        time.sleep(1.5)
        print(f"  Restored to: {tv_local.get_mute()}")
    else:
        print("\n⚠ The key was sent but the reported mute state didn't change.")
        print("  Either the TV ignored KEY_MUTE, or UPnP readback is stale.")
        print("  Did the TV audio actually mute? If yes, readback is the problem:")
        print("  set TV_MUTE_READBACK=off in .env.")

    print(f"\nToken saved to {tv_local.token_file()}")
    print("Now set TV_BACKEND=local in .env to stop using the SmartThings API.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
