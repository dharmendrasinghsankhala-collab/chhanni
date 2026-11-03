#!/usr/bin/env python3
"""
Chhanni  -  documentary footage QC + best-selects -> DaVinci Resolve draft
----------------------------------------------------------------------------
Phase 1: technical QC (shake, blur, exposure, scene cuts, audio activity),
picks several best sections per clip, writes selects.json + HTML report.
resolve_import.py then builds bins and draft timelines inside Resolve.

Usage (see README):
  python chhanni.py all     D:\\Footage E:\\Footage2 --out D:\\Chhanni_Out
  python chhanni.py analyze D:\\Footage --out D:\\Chhanni_Out     (resumable)
  python chhanni.py select  --out D:\\Chhanni_Out                  (re-run with new thresholds, no re-analysis)
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import cv2

VERSION = "1.0"
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v", ".mts", ".m2ts", ".avi", ".mkv", ".lrv"}
SKIP_DIR_NAMES = {"proxy", "proxies", "cacheclip", "optimizedmedia", ".gallery",
                  "$recycle.bin", "system volume information", "chhanni_out"}

DEFAULTS = {
    # analysis (changing these needs re-analysis)
    "analysis_fps": 10,
    "analysis_width": 320,
    "hwaccel": "auto",              # auto | cuda | none
    "workers": 2,
    "proxy_dirs": [],               # extra folders to search for proxies (matched by file name)
    "use_proxies": True,
    "skip_shorter_than_sec": 1.0,
    # selection (re-run `select` after changing; no re-analysis needed)
    "shake_threshold": 0.9,         # jitter, % of frame width (higher = more tolerant)
    "whip_threshold": 6.0,          # pan speed, % of frame width per 1/10 s
    "blur_rel": 0.45,               # frame sharpness vs. best of the same shot
    "blur_abs": 12.0,               # absolute minimum sharpness (very soft = reject)
    "dark_luma": 16,                # mean luma below this = too dark
    "bright_clip_pct": 30.0,        # % of pixels clipped white above this = blown out
    "scene_threshold": 0.30,        # histogram distance for a cut
    "min_select_sec": 2.0,
    "broll_target_sec": 6.0,
    "broll_max_sec": 10.0,
    "max_selects_per_clip": 6,
    "gap_bridge_sec": 0.4,
    "min_spacing_sec": 1.0,
    "edge_trim_sec": 0.2,
    "aroll_min_speech_ratio": 0.45,
    "aroll_min_duration_sec": 20.0,
    "aroll_max_motion": 1.2,        # median jitter+pan for an interview-like static shot
    "thumbnails": True,
    # AI (v3)
    "dead_air_sec": 1.2,
    "whisper_model": "large-v3",
    "language": "auto",
    "transcribe_min_speech": 0.15,
    "use_clip": "auto",
}

def _find_ff(name):
    """PATH first, then the path saved by setup.bat (winget installs need a new shell for PATH)."""
    p = shutil.which(name)
    if p:
        return p
    saved = Path(__file__).with_name("ffmpeg_path.txt")
    if saved.exists():
        cand = Path(saved.read_text(encoding="utf-8", errors="ignore").strip()).with_name(name + (".exe" if os.name == "nt" else ""))
        if cand.exists():
            return str(cand)
    la = os.environ.get("LOCALAPPDATA")
    if la:
        for c in [Path(la, "Microsoft", "WinGet", "Links", name + ".exe")] + sorted(
                Path(la, "Microsoft", "WinGet", "Packages").glob(f"Gyan.FFmpeg*/**/{name}.exe")):
            if c.exists():
                return str(c)
    return name


NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # no console flashes from the GUI
FFMPEG = _find_ff("ffmpeg")
FFPROBE = _find_ff("ffprobe")
_print_lock = threading.Lock()
LOG = __import__("collections").deque(maxlen=400)
STOP = threading.Event()
ACTIVE_PROCS = set()
_procs_lock = threading.Lock()


def log(*a):
    msg = " ".join(str(x) for x in a)
    with _print_lock:
        LOG.append((time.time(), msg))
        try:
            print(msg, flush=True)
        except Exception:
            pass


def _track(proc, add=True):
    with _procs_lock:
        (ACTIVE_PROCS.add if add else ACTIVE_PROCS.discard)(proc)


def request_stop():
    STOP.set()
    with _procs_lock:
        for p in list(ACTIVE_PROCS):
            try:
                p.kill()
            except Exception:
                pass


# --------------------------------------------------------------------------- config
def load_config(out_dir: Path, cli_overrides=None):
    cfg = dict(DEFAULTS)
    for p in (Path(__file__).with_name("config.json"), out_dir / "config.json"):
        if p.exists():
            try:
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception as e:
                log(f"[warn] config {p} ignored: {e}")
    if cli_overrides:
        cfg.update({k: v for k, v in cli_overrides.items() if v is not None})
    return cfg


# --------------------------------------------------------------------------- db
def open_db(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(out_dir / "chhanni.db"), check_same_thread=False, timeout=60)
    db.execute("""CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY, size INTEGER, mtime REAL, status TEXT,
        error TEXT, meta TEXT, npz TEXT, analyzed_at TEXT, seconds REAL)""")
    db.commit()
    return db


_db_lock = threading.Lock()


def db_exec(db, sql, args=()):
    with _db_lock:
        cur = db.execute(sql, args)
        db.commit()
        return cur.fetchall()


# --------------------------------------------------------------------------- scan
def normalize_roots(roots):
    """A proxy folder given by mistake -> use the folder with the real camera files above it."""
    out = []
    for r in roots:
        p = Path(str(r).strip().strip('"')).resolve()
        if p.is_dir() and (p.name.lower() in ("proxy", "proxies") or is_camera_proxy_dir(p)):
            parent = p.parent
            if is_camera_proxy_dir(p):
                parent = p.parent / "CLIP" if (p.parent / "CLIP").is_dir() else p.parent
            log(f"[scan] '{p}' is a proxy folder - using the real clips in '{parent}'")
            p = parent
        if not p.exists():
            log(f"[scan] NOT FOUND: {p}")
            continue
        if p not in out:
            out.append(p)
    return out


def scan(roots):
    files = []
    for root in roots:
        root = Path(root)
        if root.is_file():
            if root.suffix.lower() in VIDEO_EXT:
                files.append(root.resolve())
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIR_NAMES and not d.startswith(".")
                           and not is_camera_proxy_dir(Path(dirpath, d))]
            if is_camera_proxy_dir(Path(dirpath)) or Path(dirpath).name.lower() in SKIP_DIR_NAMES:
                continue
            for f in filenames:
                if f.startswith("._"):
                    continue
                if Path(f).suffix.lower() in VIDEO_EXT:
                    files.append(Path(dirpath, f).resolve())
    # same card copied twice (same file name + size) -> keep the first copy only
    seen, uniq, dups = set(), [], 0
    for f in sorted(set(files)):
        try:
            key = (f.name.lower(), f.stat().st_size)
        except OSError:
            continue
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        uniq.append(f)
    if dups:
        log(f"[scan] {dups} duplicate copies skipped (same name + size)")
    return uniq


def is_camera_proxy_dir(p: Path):
    """Sony M4ROOT/SUB holds the camera's own low-res proxies (C0001S03.MP4)."""
    return p.name.lower() == "sub" and p.parent.name.lower() == "m4root"


def proxy_keys(stem):
    s = stem.lower()
    keys = [s]
    if len(s) > 3 and s[-3] == "s" and s[-2:].isdigit():   # Sony C6643S03 -> c6643
        keys.append(s[:-3])
    return keys


def build_proxy_index(roots, proxy_dirs):
    """name(lower) -> [paths]   from 'Proxy' folders inside roots + configured proxy dirs."""
    idx = {}
    cands = list(proxy_dirs)
    for root in roots:
        root = Path(root)
        if root.is_dir():
            for dirpath, dirnames, _ in os.walk(root):
                for d in dirnames:
                    if d.lower() in ("proxy", "proxies") or is_camera_proxy_dir(Path(dirpath, d)):
                        cands.append(Path(dirpath, d))
    for c in cands:
        c = Path(c)
        if not c.is_dir():
            continue
        for dirpath, _, filenames in os.walk(c):
            for f in filenames:
                if Path(f).suffix.lower() in VIDEO_EXT:
                    for k in proxy_keys(Path(f).stem):
                        idx.setdefault(k, []).append(Path(dirpath, f))
    return idx


# --------------------------------------------------------------------------- probe
def probe(path):
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed: " + r.stderr.decode(errors="ignore")[-300:])
    info = json.loads(r.stdout or b"{}")
    streams = info.get("streams", [])
    vids = [s for s in streams if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    if not vids:
        raise RuntimeError("no video stream")
    v = vids[0]

    def rate(s):
        try:
            n, d = s.split("/")
            return float(n) / float(d) if float(d) else 0.0
        except Exception:
            return 0.0
    fps = rate(v.get("avg_frame_rate", "0/0")) or rate(v.get("r_frame_rate", "0/0")) or 25.0
    fmt = info.get("format", {})
    dur = float(fmt.get("duration") or v.get("duration") or 0)
    tc = (v.get("tags", {}) or {}).get("timecode") or (fmt.get("tags", {}) or {}).get("timecode")
    if not tc:
        for s in streams:
            t = (s.get("tags", {}) or {}).get("timecode")
            if t:
                tc = t
                break
    return {
        "fps": round(fps, 3), "duration": dur,
        "width": int(v.get("width", 0)), "height": int(v.get("height", 0)),
        "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "start_tc": tc, "has_audio": bool(auds),
        "audio_channels": int(auds[0].get("channels", 0)) if auds else 0,
    }


# --------------------------------------------------------------------------- hw detection
_HW = None


def hw_caps():
    global _HW
    if _HW is None:
        caps = {"cuda": False, "scale_cuda": False}
        try:
            hw = subprocess.run([FFMPEG, "-hide_banner", "-hwaccels"], capture_output=True, creationflags=NO_WINDOW).stdout.decode()
            caps["cuda"] = "cuda" in hw
            flt = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, creationflags=NO_WINDOW).stdout.decode()
            caps["scale_cuda"] = " scale_cuda " in flt
        except Exception:
            pass
        _HW = caps
    return _HW


_MODE_FAILS = {}
_MODE_OK = {}


def decode_modes(cfg):
    pref = cfg.get("hwaccel", "auto")
    caps = hw_caps()
    modes = []
    if pref in ("auto", "cuda") and caps["cuda"]:
        if caps["scale_cuda"]:
            modes.append("cuda_full")
        modes.append("cuda")
    modes.append("cpu")
    # a GPU mode that keeps failing and never worked is dropped (saves time per file)
    return [m for m in modes if m == "cpu" or _MODE_OK.get(m) or _MODE_FAILS.get(m, 0) < 3]


def ffmpeg_video_cmd(path, mode, fps, W, H):
    base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-noautorotate"]
    if mode == "cuda_full":
        base += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        vf = f"scale_cuda={W}:{H},hwdownload,format=nv12,fps={fps},format=gray"
    elif mode == "cuda":
        base += ["-hwaccel", "cuda"]
        vf = f"fps={fps},scale={W}:{H}:flags=area,format=gray"
    else:
        base += ["-threads", "4"]
        vf = f"fps={fps},scale={W}:{H}:flags=area,format=gray"
    return base + ["-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf,
                   "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]


# --------------------------------------------------------------------------- analysis core
def analyze_frames(frame_iter, W, H):
    """Per-sample metrics from a gray uint8 frame iterator."""
    win = cv2.createHanningWindow((W, H), cv2.CV_32F)
    sharp, luma, dark, bright, dx, dy, resp, scene, mad = [], [], [], [], [], [], [], [], []
    prev_f = prev_h = prev_s = None
    for f in frame_iter:
        ff = f.astype(np.float32)
        lap = cv2.Laplacian(f, cv2.CV_32F, ksize=3)
        sharp.append(float(lap.var()))
        luma.append(float(f.mean()))
        dark.append(float((f <= 12).mean() * 100))
        bright.append(float((f >= 250).mean() * 100))
        small = cv2.resize(f, (64, 36), interpolation=cv2.INTER_AREA)
        h = cv2.calcHist([small], [0], None, [32], [0, 256])
        cv2.normalize(h, h, 1.0, 0, cv2.NORM_L1)
        if prev_f is None:
            dx.append(0.0); dy.append(0.0); resp.append(1.0); scene.append(0.0); mad.append(0.0)
        else:
            (sx, sy), r = cv2.phaseCorrelate(prev_f, ff, win)
            dx.append(float(sx)); dy.append(float(sy)); resp.append(float(r))
            scene.append(float(cv2.compareHist(prev_h, h, cv2.HISTCMP_BHATTACHARYYA)))
            mad.append(float(np.abs(small.astype(np.int16) - prev_s.astype(np.int16)).mean() / 255.0))
        prev_f, prev_h, prev_s = ff, h, small
    arr = lambda x: np.asarray(x, dtype=np.float32)
    return dict(sharp=arr(sharp), luma=arr(luma), dark=arr(dark), bright=arr(bright),
                dx=arr(dx), dy=arr(dy), resp=arr(resp), scene=arr(scene), mad=arr(mad))


def read_frames(proc, W, H):
    n = W * H
    while True:
        buf = proc.stdout.read(n)
        if not buf or len(buf) < n:
            break
        yield np.frombuffer(buf, dtype=np.uint8).reshape(H, W)


def analyze_video(src, cfg, expect_duration=None):
    meta = probe(src)
    if meta["width"] <= 0:
        raise RuntimeError("bad video dimensions")
    W = int(cfg["analysis_width"]) // 2 * 2
    H = max(2, int(round(W * meta["height"] / meta["width"] / 2)) * 2)
    fps = cfg["analysis_fps"]
    last_err = ""
    for mode in decode_modes(cfg):
        cmd = ffmpeg_video_cmd(src, mode, fps, W, H)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=W * H * 32,
                                creationflags=NO_WINDOW)
        _track(proc)
        err_chunks = []
        t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
        t.start()
        m = analyze_frames(read_frames(proc, W, H), W, H)
        proc.wait(); t.join(timeout=5)
        _track(proc, False)
        if STOP.is_set():
            raise RuntimeError("stopped")
        n = len(m["sharp"])
        dur = meta["duration"] or (n / fps)
        if n >= max(2, 0.5 * dur * fps):   # got most of the clip
            m["_mode"] = mode
            _MODE_OK[mode] = True
            return meta, m, (W, H)
        _MODE_FAILS[mode] = _MODE_FAILS.get(mode, 0) + 1
        last_err = b"".join(err_chunks).decode(errors="ignore")[-400:]
    raise RuntimeError(f"decode failed ({last_err.strip() or 'no frames'})")


def analyze_audio(src, step=0.1):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src), "-map", "0:a:0",
           "-vn", "-ac", "1", "-ar", "8000", "-af", "highpass=f=120,lowpass=f=3800",
           "-f", "s16le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0 or not r.stdout:
        return None
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(8000 * step)
    nwin = len(a) // hop
    if nwin == 0:
        return None
    a = a[: nwin * hop].reshape(nwin, hop)
    rms = np.sqrt((a ** 2).mean(axis=1) + 1e-12)
    return (20 * np.log10(rms)).astype(np.float32)


def cache_name(path):
    return hashlib.sha1(str(path).lower().encode()).hexdigest()[:16] + ".npz"


def find_proxy(src, meta_dur, proxy_idx):
    cands = proxy_idx.get(src.stem.lower(), [])
    card = str(src.parent.parent).lower()
    cands = sorted(cands, key=lambda p: (not str(p).lower().startswith(card), len(str(p))))
    for p in cands:
        if p == src:
            continue
        try:
            pm = probe(p)
            if meta_dur and abs(pm["duration"] - meta_dur) > max(1.0, 0.02 * meta_dur):
                continue
            return p
        except Exception:
            continue
    return None


def analyze_one(src: Path, cfg, out_dir: Path, proxy_idx):
    t0 = time.time()
    orig = probe(src)
    if orig["duration"] and orig["duration"] < cfg["skip_shorter_than_sec"]:
        return orig, None, "skipped (too short)"
    proxy = find_proxy(src, orig["duration"], proxy_idx) if cfg.get("use_proxies") else None
    ana_src = proxy or src
    _, m, (W, H) = analyze_video(ana_src, cfg)
    aud = analyze_audio(ana_src) if orig["has_audio"] else None
    if aud is None and proxy is not None and orig["has_audio"]:
        aud = analyze_audio(src)
    npz = out_dir / "cache" / cache_name(src)
    npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(npz, **{k: v for k, v in m.items() if not k.startswith("_")},
                        audio_db=aud if aud is not None else np.zeros(0, np.float32))
    meta = dict(orig, analysis_w=W, analysis_h=H, analysis_fps=cfg["analysis_fps"],
                decode_mode=m["_mode"], proxy=str(proxy) if proxy else None, path=str(src))
    return meta, npz, f"ok {time.time()-t0:.1f}s ({m['_mode']}{', proxy' if proxy else ''})"


def cmd_analyze(roots, out_dir, cfg, progress=None):
    """progress(dict) is called with phase/done/total/eta/current for the GUI."""
    STOP.clear()
    prog = progress or (lambda d: None)
    db = open_db(out_dir)
    prog({"phase": "scan", "msg": "Scanning folders..."})
    files = scan(roots)
    log(f"[scan] {len(files)} video files found")
    proxy_idx = build_proxy_index(roots, cfg.get("proxy_dirs", [])) if cfg.get("use_proxies") else {}
    if proxy_idx:
        log(f"[scan] {len(set(p for v in proxy_idx.values() for p in v))} proxy files indexed")
    caps = hw_caps()
    log(f"[gpu] cuda={'yes' if caps['cuda'] else 'no'} scale_cuda={'yes' if caps['scale_cuda'] else 'no'}")
    todo = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        row = db_exec(db, "SELECT size, mtime, status FROM files WHERE path=?", (str(f),))
        if row and row[0][0] == st.st_size and abs(row[0][1] - st.st_mtime) < 1 and row[0][2] in ("done", "skipped"):
            continue
        todo.append((f, st))
    log(f"[analyze] {len(todo)} to analyze, {len(files)-len(todo)} already done (resume)")
    total = len(todo)
    total_bytes = sum(st.st_size for _, st in todo) or 1
    state = {"done": 0, "bytes": 0, "errors": 0, "current": set()}
    lock = threading.Lock()
    t_start = time.time()

    def report():
        el = time.time() - t_start
        eta = el / state["bytes"] * (total_bytes - state["bytes"]) if state["bytes"] else None
        prog({"phase": "analyze", "done": state["done"], "total": total, "errors": state["errors"],
              "skipped_done": len(files) - len(todo), "files_total": len(files),
              "bytes_done": state["bytes"], "bytes_total": total_bytes, "elapsed": el, "eta": eta,
              "current": sorted(state["current"]), "gpu": caps["cuda"]})

    def work(item):
        f, st = item
        if STOP.is_set():
            return f, None
        with lock:
            state["current"].add(f.name)
        report()
        try:
            meta, npz, msg = analyze_one(f, cfg, out_dir, proxy_idx)
            status = "done" if npz else "skipped"
            db_exec(db, "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(f), st.st_size, st.st_mtime, status, None, json.dumps(meta),
                     str(npz) if npz else None, dt.datetime.now().isoformat(timespec="seconds"),
                     meta.get("duration")))
            return f, msg
        except Exception as e:
            if STOP.is_set():
                return f, None
            db_exec(db, "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(f), st.st_size, st.st_mtime, "error", str(e)[:500], None, None,
                     dt.datetime.now().isoformat(timespec="seconds"), None))
            return f, f"ERROR {e}"
        finally:
            with lock:
                state["current"].discard(f.name)

    report()
    with ThreadPoolExecutor(max_workers=max(1, int(cfg["workers"]))) as ex:
        futs = {ex.submit(work, it): it for it in todo}
        try:
            for fu in as_completed(futs):
                f, msg = fu.result()
                if msg is None:
                    continue
                with lock:
                    state["done"] += 1
                    state["bytes"] += futs[fu][1].st_size
                    if msg.startswith("ERROR"):
                        state["errors"] += 1
                el = time.time() - t_start
                eta = el / state["bytes"] * (total_bytes - state["bytes"]) if state["bytes"] else 0
                log(f"[{state['done']}/{total}] {f.name}: {msg}   (ETA {eta/60:.0f} min)")
                report()
        except KeyboardInterrupt:
            log("\n[stop] Ctrl+C - progress saved. Run the same command again to resume.")
            request_stop()
            for fu in futs:
                fu.cancel()
            raise
    if STOP.is_set():
        log("[stop] stopped - progress saved, Start again to resume.")
        return False
    errs = db_exec(db, "SELECT COUNT(*) FROM files WHERE status='error'")[0][0]
    log(f"[analyze] finished. errors: {errs}")
    return True


# --------------------------------------------------------------------------- selection
def movavg(x, n):
    n = max(1, int(n))
    if n <= 1 or len(x) < 2:
        return x.astype(np.float32)
    k = np.ones(n, np.float32) / n
    pad = n // 2
    xp = np.pad(x, (pad, n - 1 - pad), mode="edge")
    return np.convolve(xp, k, mode="valid").astype(np.float32)


def runs_of(mask):
    """[(start, end_exclusive)] of True runs."""
    if len(mask) == 0:
        return []
    m = np.concatenate([[False], mask.astype(bool), [False]])
    d = np.diff(m.astype(np.int8))
    starts = np.where(d == 1)[0]
    ends = np.where(d == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def bridge(mask, gap):
    m = mask.copy()
    inv = runs_of(~m)
    for s, e in inv:
        if s > 0 and e < len(m) and (e - s) <= gap:
            m[s:e] = True
    return m


def compute_frame_qc(d, meta, cfg):
    fps = meta["analysis_fps"]
    W = meta["analysis_w"]
    n = len(d["sharp"])
    dx, dy, resp = d["dx"].copy(), d["dy"].copy(), d["resp"]
    # scene cuts: histogram jump + pixel change, local peak
    sc = d["scene"]
    mad = d["mad"]
    local = movavg(mad, fps) + 1e-3
    cut = (((sc > cfg["scene_threshold"]) & (mad > 0.05))
           | ((mad > 0.10) & (d["resp"] < 0.10) & (mad > 3 * local)))
    cut_idx = [i for i in np.where(cut)[0] if sc[i] >= sc[max(0, i-2):i+3].max()]
    # unreliable motion samples (cuts / low phase-corr response) -> neighbour value
    bad = (resp < 0.03)
    for i in cut_idx:
        bad[i] = True
    if bad.any() and (~bad).any():
        good_i = np.where(~bad)[0]
        dx[bad] = np.interp(np.where(bad)[0], good_i, dx[good_i])
        dy[bad] = np.interp(np.where(bad)[0], good_i, dy[good_i])
    # shake = high-frequency jitter around the smooth camera path (consistent pans stay low),
    # computed shot-by-shot so a cut never smears into its neighbours
    bounds = [0] + list(cut_idx) + [n]
    shake = np.zeros(n, np.float32)
    pan = np.zeros(n, np.float32)
    for a, b in zip(bounds[:-1], bounds[1:]):
        a2 = a + 1 if a in cut_idx else a       # first sample after a cut has no valid motion
        if b - a2 <= 0:
            continue
        sx, sy = dx[a2:b], dy[a2:b]
        sdx, sdy = movavg(sx, fps), movavg(sy, fps)
        jit = np.sqrt((sx - sdx) ** 2 + (sy - sdy) ** 2) * 100.0 / W
        shake[a2:b] = np.sqrt(movavg(jit ** 2, fps * 0.8))
        pan[a2:b] = np.sqrt(sdx ** 2 + sdy ** 2) * 100.0 / W
        if a2 > a:
            shake[a], pan[a] = shake[a2], pan[a2]
    # sharpness relative to the best of the same shot
    rel = np.ones(n, np.float32)
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a <= 0:
            continue
        ref = np.percentile(d["sharp"][a:b], 90) + 1e-6
        rel[a:b] = d["sharp"][a:b] / ref
    rel_s = movavg(rel, max(1, fps // 3))
    sharp_ok = (rel_s >= cfg["blur_rel"]) & (d["sharp"] >= cfg["blur_abs"])
    shake_ok = shake <= cfg["shake_threshold"]
    whip_ok = pan <= cfg["whip_threshold"]
    expo_ok = (movavg(d["luma"], fps // 2) >= cfg["dark_luma"]) & (movavg(d["bright"], fps // 2) <= cfg["bright_clip_pct"])
    near_cut = np.zeros(n, bool)
    for i in cut_idx:
        near_cut[max(0, i - 2): i + 3] = True
    good = sharp_ok & shake_ok & whip_ok & expo_ok & ~near_cut
    # per-frame quality 0..1
    q = (0.45 * np.clip(rel_s, 0, 1.2) / 1.2
         + 0.35 * np.clip(1 - shake / max(cfg["shake_threshold"], 1e-3), 0, 1)
         + 0.20 * np.clip(1 - np.abs(movavg(d["luma"], fps) - 118) / 118, 0, 1))
    return dict(good=good, q=q.astype(np.float32), shake=shake, pan=pan, rel=rel_s,
                sharp_ok=sharp_ok, shake_ok=shake_ok & whip_ok, expo_ok=expo_ok,
                cuts=cut_idx, fps=fps, n=n)


def speech_ratio(audio_db):
    if audio_db is None or len(audio_db) < 10:
        return 0.0, None
    floor = np.percentile(audio_db, 10)
    active = audio_db > max(floor + 12, -48)
    act = movavg(active.astype(np.float32), 5) > 0.4
    return float(act.mean()), act


def pick_windows(q, s, e, L, max_n, spacing):
    """Greedy best non-overlapping windows of length L inside [s, e)."""
    out = []
    if e - s < L:
        return out
    cs = np.concatenate([[0], np.cumsum(q[s:e])])
    scores = (cs[L:] - cs[:-L]) / L          # window starting at s+k
    used = np.zeros(len(scores), bool)
    for _ in range(max_n):
        cand = np.where(~used, scores, -1)
        k = int(np.argmax(cand))
        if cand[k] < 0:
            break
        out.append((s + k, s + k + L, float(scores[k])))
        lo, hi = max(0, k - L - spacing + 1), min(len(scores), k + L + spacing)
        used[lo:hi] = True
    return out


def select_clip(meta, d, cfg, transcript=None):
    qc = compute_frame_qc(d, meta, cfg)
    fps, n = qc["fps"], qc["n"]
    sp_ratio, _ = speech_ratio(d.get("audio_db"))
    motion_med = float(np.median(qc["shake"] + qc["pan"])) if n else 0
    dur = meta.get("duration") or n / fps
    is_aroll = (sp_ratio >= cfg["aroll_min_speech_ratio"] and dur >= cfg["aroll_min_duration_sec"]
                and motion_med <= cfg["aroll_max_motion"] and len(qc["cuts"]) <= max(2, dur / 120))
    ana = None
    if transcript and transcript.get("segments"):
        import ai_audio
        ana = ai_audio.analyze(transcript, dead_air=cfg.get("dead_air_sec", 1.2),
                               clip_pct=transcript.get("clip_pct"), clip_win=transcript.get("clip_win", 0.5))
        talk = sum(u["end"] - u["start"] for u in ana["units"])
        if talk >= 0.3 * dur and dur >= cfg["aroll_min_duration_sec"] and motion_med <= cfg["aroll_max_motion"] * 1.5:
            is_aroll = True
    good = bridge(qc["good"], int(cfg["gap_bridge_sec"] * fps) if not is_aroll else int(1.5 * fps))
    for c in qc["cuts"]:
        good[max(0, c - 1): c + 2] = False
    trim = int(cfg["edge_trim_sec"] * fps)
    minlen = int(cfg["min_select_sec"] * fps)
    cands = []
    for s, e in runs_of(good):
        s2, e2 = s + trim, e - trim
        if e2 - s2 < minlen:
            continue
        if is_aroll or (e2 - s2) <= cfg["broll_max_sec"] * fps:
            cands.append((s2, e2, float(qc["q"][s2:e2].mean())))
        else:
            cands += pick_windows(qc["q"], s2, e2, int(cfg["broll_target_sec"] * fps),
                                  cfg["max_selects_per_clip"], int(cfg["min_spacing_sec"] * fps))
    if not is_aroll:
        cands = sorted(cands, key=lambda c: -c[2])[: cfg["max_selects_per_clip"]]
    cands.sort()
    selects = []
    if is_aroll and ana is not None:
        # speech-driven A-roll: retakes / false starts / fillers removed, dead air cut
        import ai_audio
        for u in ai_audio.merge_units(ana["units"], max_gap=cfg.get("dead_air_sec", 1.2)):
            if u["end"] - u["start"] < 0.8:
                continue
            s, e = int(u["start"] * fps), max(int(u["start"] * fps) + 1, min(n, int(u["end"] * fps)))
            vid_ok = float(qc["good"][s:e].mean()) if e > s else 0
            sc = float(qc["q"][s:e].mean()) if e > s else 0.5
            flags = list(u["flags"]) + (["video issue"] if vid_ok < 0.5 else [])
            selects.append({"start": round(u["start"], 3), "end": round(min(u["end"], dur), 3),
                            "score": round(100 * (0.6 * sc + 0.4 * vid_ok) - (15 if "audio clipping" in flags else 0)),
                            "shake": round(float(qc["shake"][s:e].mean()), 2) if e > s else 0,
                            "sharp_rel": round(float(qc["rel"][s:e].mean()), 2) if e > s else 0,
                            "text": u["text"][:400], "flags": flags})
        cands = []
    for s, e, sc in cands:
        st, en = s / fps, min(e / fps, dur)
        sel = {
            "start": round(st, 3), "end": round(en, 3), "score": round(sc * 100),
            "shake": round(float(qc["shake"][s:e].mean()), 2),
            "sharp_rel": round(float(qc["rel"][s:e].mean()), 2),
        }
        if ana is not None:
            words = [x["text"] for x in ana["units"] if x["end"] > st and x["start"] < en]
            if words:
                sel["text"] = " ".join(words)[:300]
        selects.append(sel)
    pct = lambda m: round(float((~m).mean() * 100), 1) if n else 0.0
    stats = {
        "shaky_pct": pct(qc["shake_ok"]), "blur_pct": pct(qc["sharp_ok"]),
        "exposure_bad_pct": pct(qc["expo_ok"]), "usable_pct": round(float(qc["good"].mean() * 100), 1) if n else 0,
        "speech_ratio": round(sp_ratio, 2), "scene_cuts": len(qc["cuts"]),
        "median_shake": round(float(np.median(qc["shake"])), 2) if n else 0,
    }
    if ana is not None:
        stats["transcript"] = dict(ana["stats"], language=transcript.get("language"))
    # compact strip for the report: 0 good, 1 shaky, 2 blur, 3 exposure
    status = np.zeros(n, np.int8)
    status[~qc["expo_ok"]] = 3
    status[~qc["sharp_ok"]] = 2
    status[~qc["shake_ok"]] = 1
    return {"type": "A" if is_aroll else "B", "selects": selects, "stats": stats,
            "strip": status, "cuts": [c / fps for c in qc["cuts"]]}


def load_npz(p):
    z = np.load(p)
    return {k: z[k] for k in z.files}


def rle_strip(status, bins=240):
    """Down-sample the per-sample QC status to <= bins cells, run-length encoded [[code, cells], ...]."""
    n = len(status)
    if n == 0:
        return []
    if n > bins:
        edges = np.linspace(0, n, bins + 1).astype(int)
        cells = []
        for a, b in zip(edges[:-1], edges[1:]):
            seg = status[a:max(b, a + 1)]
            cnt = np.bincount(seg, minlength=4)
            cells.append(int(np.argmax(cnt)))
    else:
        cells = [int(x) for x in status]
    out = []
    for c in cells:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


GENERIC_DIRS = {"m4root", "private", "dcim", "clip", "sub", "xdroot", "avchd", "bpav", "contents", "stream",
                "bdmv", "video", "media", "card", "footage", "raw"}


def _generic(name):
    n = name.lower()
    return n in GENERIC_DIRS or __import__("re").match(r"^\d{3}[a-z_]+", n) is not None


def group_label(path, roots):
    """Camera/card/day label: first meaningful folder below the footage root (e.g. 'Cam01(06-11-2024)')."""
    p = Path(path)
    for r in roots:
        try:
            rel = p.relative_to(r)
        except ValueError:
            continue
        parts = [x for x in rel.parts[:-1]]
        if parts and not _generic(parts[0]):
            return parts[0]
        return Path(r).name or str(r)
    return p.parent.name


def cmd_select(out_dir, cfg, progress=None):
    prog = progress or (lambda d: None)
    db = open_db(out_dir)
    rows = db_exec(db, "SELECT path, meta, npz FROM files WHERE status='done'")
    errs = db_exec(db, "SELECT path, error FROM files WHERE status='error'")
    try:
        roots = [Path(r) for r in json.loads((out_dir / "roots.json").read_text(encoding="utf-8"))]
    except Exception:
        roots = []
    clips = []
    strips = {}
    cache_refs = {}
    for i, (path, meta_s, npz) in enumerate(rows):
        if i % 20 == 0:
            prog({"phase": "select", "done": i, "total": len(rows)})
        if not Path(path).exists():
            continue
        meta = json.loads(meta_s)
        try:
            d = load_npz(npz)
        except Exception as e:
            log(f"[warn] cache unreadable for {path}: {e}")
            continue
        tr = None
        try:
            import ai_audio
            tr = ai_audio.load_transcript(out_dir, cache_name(path)[:-4])
        except Exception:
            tr = None
        r = select_clip(meta, d, cfg, tr)
        cache_refs[path] = (meta, npz)
        strip, cuts = r.pop("strip"), r.pop("cuts")
        strips[path] = (strip, cuts)
        clips.append(dict(path=path, name=Path(path).name, proxy=meta.get("proxy"), fps=meta["fps"],
                          duration=meta["duration"], start_tc=meta.get("start_tc"), width=meta["width"],
                          height=meta["height"], codec=meta.get("codec"), folder=str(Path(path).parent),
                          has_audio=meta.get("has_audio", False), audio_channels=meta.get("audio_channels", 0),
                          group=group_label(path, roots), id=cache_name(path)[:-4],
                          qc=rle_strip(strip), cuts=[round(c, 2) for c in cuts], **r))
    clips.sort(key=lambda c: (c["group"], c["path"]))
    if cfg.get("thumbnails", True):
        make_thumbs(out_dir, clips, prog)
        try:
            import ai_visual
            ai_visual.tag_project(out_dir, clips, lambda c: (cache_refs[c["path"]][0], load_npz(cache_refs[c["path"]][1])),
                                  cfg, log=log, progress=prog)
        except Exception as e:
            log(f"[tags] skipped: {e}")
    out = {"tool": "chhanni", "version": VERSION, "generated": dt.datetime.now().isoformat(timespec="seconds"),
           "roots": [str(r) for r in roots],
           "settings": {k: cfg[k] for k in cfg if k not in ("proxy_dirs",)}, "clips": clips,
           "errors": [{"path": p, "error": e} for p, e in errs]}
    (out_dir / "selects.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    remember_project(out_dir)
    nsel = sum(len(c["selects"]) for c in clips)
    sel_sec = sum(s["end"] - s["start"] for c in clips for s in c["selects"])
    src_sec = sum(c["duration"] or 0 for c in clips)
    log(f"[select] {len(clips)} clips -> {nsel} selects, {sel_sec/60:.1f} min from {src_sec/3600:.2f} h of footage")
    log(f"[select] A-roll clips: {sum(c['type']=='A' for c in clips)}, "
        f"B-roll clips: {sum(c['type']=='B' for c in clips)}, no usable section: {sum(not c['selects'] for c in clips)}")
    make_report(out_dir, out, strips, cfg)
    prog({"phase": "finished"})
    return out


def app_data_dir():
    ad = Path(os.environ.get("APPDATA", Path.home())) / "Chhanni"
    ad.mkdir(parents=True, exist_ok=True)
    return ad


def remember_project(out_dir):
    """Recent projects list shared by the app and the Resolve / Premiere panels."""
    try:
        ad = app_data_dir()
        (ad / "last_selects.txt").write_text(str(out_dir / "selects.json"), encoding="utf-8")
        pf = ad / "projects.json"
        lst = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else []
        lst = [p for p in lst if Path(p.get("out", "")).resolve() != Path(out_dir).resolve()]
        name = out_dir.name
        try:
            name = json.loads((out_dir / "project.json").read_text(encoding="utf-8")).get("name") or name
        except Exception:
            pass
        lst.insert(0, {"out": str(out_dir), "name": name, "updated": dt.datetime.now().isoformat(timespec="seconds")})
        pf.write_text(json.dumps(lst[:30], indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log(f"[warn] could not save recent projects: {e}")


def make_thumbs(out_dir, clips, prog):
    tdir = out_dir / "thumbs"
    tdir.mkdir(exist_ok=True)
    jobs = []
    for c in clips:
        src = c["proxy"] or c["path"]
        dur = c["duration"] or 0
        c["poster"] = f"{c['id']}_p.jpg"
        jobs.append((src, dur * 0.5, tdir / c["poster"]))
        for k, s in enumerate(c["selects"]):
            s["thumb"] = f"{c['id']}_{int(s['start']*10)}.jpg"
            jobs.append((src, (s["start"] + s["end"]) / 2, tdir / s["thumb"]))
    todo = [j for j in jobs if not j[2].exists()]
    if not todo:
        return
    log(f"[thumbs] {len(todo)} thumbnails ...")
    done = [0]
    lk = threading.Lock()

    def one(j):
        if STOP.is_set():
            return
        thumb(*j)
        with lk:
            done[0] += 1
            if done[0] % 10 == 0:
                prog({"phase": "thumbs", "done": done[0], "total": len(todo)})
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(one, todo))


# --------------------------------------------------------------------------- report
def thumb(src, t, dst, width=480):
    if dst.exists():
        return True
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", str(src),
           "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5", "-y", str(dst)]
    return subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW).returncode == 0 and dst.exists()


def strip_svg(strip, cuts, selects, dur, fps, w=600, h=18):
    n = len(strip)
    if n == 0 or not dur:
        return ""
    cols = {0: "#3d9a5b", 1: "#d9822b", 2: "#9b59b6", 3: "#555"}
    parts = []
    # run-length encode
    i = 0
    while i < n:
        j = i
        while j < n and strip[j] == strip[i]:
            j += 1
        x, ww = i / n * w, max(0.5, (j - i) / n * w)
        parts.append(f'<rect x="{x:.1f}" y="0" width="{ww:.1f}" height="{h}" fill="{cols[int(strip[i])]}"/>')
        i = j
    for s in selects:
        x, ww = s["start"] / dur * w, max(1, (s["end"] - s["start"]) / dur * w)
        parts.append(f'<rect x="{x:.1f}" y="{h+2}" width="{ww:.1f}" height="5" fill="#2f7cf6"/>')
    for c in cuts:
        x = c / dur * w
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h}" stroke="#fff" stroke-width="1"/>')
    return f'<svg width="{w}" height="{h+8}" viewBox="0 0 {w} {h+8}">{"".join(parts)}</svg>'


def make_report(out_dir, data, strips, cfg):
    clips = data["clips"]
    tot_src = sum(c["duration"] or 0 for c in clips)
    tot_sel = sum(s["end"] - s["start"] for c in clips for s in c["selects"])
    rows = []
    for c in sorted(clips, key=lambda c: c["path"]):
        st = c["stats"]
        strip, cuts = strips.get(c["path"], (np.zeros(0), []))
        svg = strip_svg(strip, cuts, c["selects"], c["duration"], c["fps"])
        th = "".join(
            f'<figure><img loading="lazy" src="thumbs/{s["thumb"]}"><figcaption>{fmt_t(s["start"])}–{fmt_t(s["end"])} · {s["score"]}</figcaption></figure>'
            for s in c["selects"] if s.get("thumb")) or ('<span class="none">No usable section</span>' if not c["selects"] else "")
        badge = '<b class="a">A-roll</b>' if c["type"] == "A" else '<b class="b">B-roll</b>'
        rows.append(f'''<section class="clip" data-type="{c["type"]}" data-n="{len(c["selects"])}">
<div class="hd">{badge}<span class="nm" title="{html.escape(c["path"])}">{html.escape(Path(c["path"]).name)}</span>
<span class="mt">{fmt_t(c["duration"])} · {c["width"]}×{c["height"]} · {c["fps"]}fps · {html.escape(str(c.get("codec")))}{' · proxy' if c.get("proxy") else ''}</span></div>
<div class="fd">{html.escape(c["folder"])}</div>
<div class="bar">{svg}</div>
<div class="st">usable {st["usable_pct"]}% · shaky {st["shaky_pct"]}% · blur {st["blur_pct"]}% · exposure {st["exposure_bad_pct"]}% · speech {int(st["speech_ratio"]*100)}% · cuts {st["scene_cuts"]}</div>
<div class="th">{th}</div></section>''')
    err_html = "".join(f"<li>{html.escape(e['path'])} — {html.escape(e['error'] or '')}</li>" for e in data["errors"])
    page = f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Chhanni report</title>
<style>
body{{font:14px/1.45 system-ui,Segoe UI,sans-serif;background:#16181c;color:#e6e6e6;margin:0;padding:24px 32px}}
h1{{font-size:22px;margin:0 0 4px}} .sum{{color:#aab;margin-bottom:14px}}
.lg span{{display:inline-block;margin-right:14px}} .lg i{{display:inline-block;width:12px;height:12px;margin-right:5px;vertical-align:-1px;border-radius:2px}}
.ctl{{margin:12px 0 18px}} .ctl button{{background:#262a31;color:#ddd;border:1px solid #3a3f48;border-radius:6px;padding:5px 12px;margin-right:6px;cursor:pointer}}
.ctl button.on{{background:#2f7cf6;border-color:#2f7cf6;color:#fff}}
.clip{{background:#1e2127;border:1px solid #2b2f37;border-radius:10px;padding:12px 14px;margin-bottom:12px}}
.hd{{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}} .nm{{font-weight:600}} .mt,.fd,.st{{color:#9aa3b2;font-size:12.5px}}
b.a,b.b{{font-size:11px;padding:2px 7px;border-radius:4px}} b.a{{background:#b0435a}} b.b{{background:#3b6fb6}}
.bar{{margin:8px 0 4px}} .th{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
figure{{margin:0}} figure img{{width:200px;border-radius:5px;display:block;background:#000}} figcaption{{font-size:11.5px;color:#9aa3b2;margin-top:2px}}
.none{{color:#d9822b;font-size:13px}}
</style></head><body>
<h1>Chhanni — footage QC report</h1>
<div class="sum">{len(clips)} clips · {tot_src/3600:.2f} h source → {sum(len(c["selects"]) for c in clips)} selects ({tot_sel/60:.1f} min) · generated {data["generated"]}</div>
<div class="lg"><span><i style="background:#3d9a5b"></i>good</span><span><i style="background:#d9822b"></i>shaky / whip pan</span><span><i style="background:#9b59b6"></i>blur / soft</span><span><i style="background:#555"></i>too dark / blown out</span><span><i style="background:#2f7cf6"></i>selected</span><span>white line = scene cut</span></div>
<div class="ctl"><button class="on" data-f="all">All</button><button data-f="A">A-roll</button><button data-f="B">B-roll</button><button data-f="none">No usable</button></div>
{"".join(rows)}
{f"<h2>Errors</h2><ul>{err_html}</ul>" if err_html else ""}
<script>
document.querySelectorAll('.ctl button').forEach(b=>b.onclick=()=>{{
 document.querySelectorAll('.ctl button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 const f=b.dataset.f;document.querySelectorAll('.clip').forEach(c=>{{
  c.style.display=(f==='all'||(f==='none'?c.dataset.n==='0':c.dataset.type===f))?'':'none'}})}});
</script></body></html>'''
    (out_dir / "report.html").write_text(page, encoding="utf-8")
    log(f"[report] {out_dir / 'report.html'}")


def fmt_t(s):
    s = float(s or 0)
    return f"{int(s//3600)}:{int(s%3600//60):02d}:{int(s%60):02d}" if s >= 3600 else f"{int(s//60)}:{int(s%60):02d}"


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="Chhanni - footage QC + selects for DaVinci Resolve")
    ap.add_argument("command", choices=["analyze", "select", "all", "status"])
    ap.add_argument("folders", nargs="*", help="footage folders / drives")
    ap.add_argument("--out", required=True, help="output folder (database, cache, report, selects.json)")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--hwaccel", choices=["auto", "cuda", "none"])
    ap.add_argument("--shake", type=float, dest="shake_threshold", help="shake tolerance (default 0.9)")
    ap.add_argument("--blur", type=float, dest="blur_rel", help="blur strictness 0-1 (default 0.45)")
    ap.add_argument("--max-selects", type=int, dest="max_selects_per_clip")
    ap.add_argument("--no-thumbs", action="store_true")
    ap.add_argument("--no-proxies", action="store_true")
    a = ap.parse_args()
    out_dir = Path(a.out).resolve()
    ov = {"workers": a.workers, "hwaccel": a.hwaccel, "shake_threshold": a.shake_threshold,
          "blur_rel": a.blur_rel, "max_selects_per_clip": a.max_selects_per_clip}
    if a.no_thumbs:
        ov["thumbnails"] = False
    if a.no_proxies:
        ov["use_proxies"] = False
    cfg = load_config(out_dir, ov)
    if not shutil.which(FFMPEG) and not Path(FFMPEG).exists():
        sys.exit("ffmpeg not found. Run setup.bat first.")
    if a.command in ("analyze", "all"):
        a.folders = [str(p) for p in normalize_roots(a.folders)]
        if not a.folders:
            sys.exit("Koi valid footage folder nahi mila.")
        (out_dir).mkdir(parents=True, exist_ok=True)
        (out_dir / "roots.json").write_text(json.dumps([str(Path(f).resolve()) for f in a.folders]), encoding="utf-8")
        try:
            cmd_analyze(a.folders, out_dir, cfg)
        except KeyboardInterrupt:
            sys.exit(1)
    if a.command in ("select", "all"):
        cmd_select(out_dir, cfg)
        log(f"\nNext: in DaVinci Resolve run  Workspace > Scripts > Chhanni Import  "
            f"(or resolve_import.py) and pick {out_dir / 'selects.json'}")
    if a.command == "status":
        db = open_db(out_dir)
        for st, n, s in db_exec(db, "SELECT status, COUNT(*), SUM(seconds) FROM files GROUP BY status"):
            log(f"{st:8s} {n:6d} files  {((s or 0)/3600):.2f} h")


if __name__ == "__main__":
    main()
