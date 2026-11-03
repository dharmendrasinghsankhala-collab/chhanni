"""
Chhanni launcher - double-click (or desktop shortcut).
Starts the local engine (if not already running) and opens the app in its own window (Edge app mode).
  Chhanni.pyw            -> open the app
  Chhanni.pyw --server   -> engine only (used by the Resolve / Premiere panels)
"""
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "app"))
URL = "http://127.0.0.1:47321/"


def find_edge():
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.exists():
                return str(p)
    for name in ("chrome.exe",):
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                p = Path(base) / "Google" / "Chrome" / "Application" / name
                if p.exists():
                    return str(p)
    return None


def open_window():
    exe = find_edge()
    if not exe:
        webbrowser.open(URL)
        return None
    prof = Path(os.environ.get("APPDATA", Path.home())) / "Chhanni" / "window"
    prof.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([exe, f"--app={URL}", f"--user-data-dir={prof}", "--window-size=1440,920",
                             "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"])


def main():
    import server
    server_only = "--server" in sys.argv
    if server.server_running():
        if not server_only:
            open_window()
        return
    try:
        server.serve(block=False)
    except OSError:
        if not server_only:
            open_window()
        return
    if server_only:
        # stay alive while panels / app talk to us or a job runs; exit after 20 idle minutes
        while server.idle_seconds() < 20 * 60:
            time.sleep(10)
        return
    win = open_window()
    if win is not None:
        win.wait()
        time.sleep(2)
    # window closed: keep running in the background until the job finishes and things are idle
    while server.idle_seconds() < 5 * 60:
        time.sleep(5)


if __name__ == "__main__":
    log = Path(os.environ.get("APPDATA", Path.home())) / "Chhanni" / "chhanni.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if sys.executable.lower().endswith("pythonw.exe"):
        sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
    main()
