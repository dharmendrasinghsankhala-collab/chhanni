"""
Chhanni worker - runs one heavy job in its own process so a model crash (CUDA OOM, native segfault,
driver hiccup) can never take the app down.  The server starts it and reads JSON lines from stdout:
  {"t":"log","m":"..."}            log line
  {"t":"progress","d":{...}}       progress dict (same shape the UI already understands)
  {"t":"done","phase":"finished"}  last line on success
  {"t":"error","m":"..."}          last line on failure
Stop: the server creates <out>/.stop  (the worker polls it and stops cleanly).
usage: python worker.py <mode> <out_dir> [folders_json]
"""
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

_OUT = sys.stdout
_LOCK = threading.Lock()


def emit(obj):
    with _LOCK:
        try:
            _OUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
            _OUT.flush()
        except Exception:
            pass


def main():
    mode, out = sys.argv[1], Path(sys.argv[2])
    folders = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
    # keep stray prints (libraries) away from the JSON channel
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    import engine

    def log(*a):
        msg = " ".join(str(x) for x in a)
        engine.LOG.append((time.time(), msg))
        emit({"t": "log", "m": msg})

    engine.log = log
    for mod in ("ai_audio", "ai_visual", "deep", "people"):
        try:
            m = __import__(mod)
            if hasattr(m, "log"):
                m.log = log
        except Exception:
            pass

    stop_file = out / ".stop"

    def watch():
        while True:
            if stop_file.exists():
                engine.request_stop()
                return
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True).start()
    import jobs
    try:
        phase = jobs.run(out, folders, mode, log=log, progress=lambda d: emit({"t": "progress", "d": d}))
        emit({"t": "done", "phase": phase})
    except Exception as e:
        log("[error] " + "".join(traceback.format_exception_only(type(e), e)).strip())
        log(traceback.format_exc()[-1500:])
        emit({"t": "error", "m": str(e)})
        sys.exit(2)


if __name__ == "__main__":
    main()
