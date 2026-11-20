import os
import sys
import time
import urllib.request
import webbrowser
import threading
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
os.chdir(REPO_DIR)
sys.path.insert(0, str(REPO_DIR))

URL = "http://127.0.0.1:8765/"


def is_running():
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "ClipCutLauncher/1.0"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status in (200, 302, 303, 307)
    except Exception:
        return False


def open_browser_when_ready():
    for _ in range(30):
        time.sleep(0.5)
        if is_running():
            webbrowser.open(URL)
            return


def main():
    print("=" * 60)
    print("                 ClipCut AI Video Studio")
    print("=" * 60)

    if is_running():
        print(f"\nClipCut is already running at {URL}")
        print("Opening browser...\n")
        webbrowser.open(URL)
        time.sleep(1)
        return

    print(f"\nStarting ClipCut server at {URL} ...")
    print("Your browser will open automatically when ready.")
    print("Keep this window open while using ClipCut.\n")

    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    from studio import server
    server.main()


if __name__ == "__main__":
    main()
