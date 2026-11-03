"""
Chhanni local server - the desktop app UI and the Resolve / Premiere panels all talk to this.
http://127.0.0.1:47321   (localhost only)
"""
import datetime as dt
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
import engine  # noqa: E402
import plan    # noqa: E402
import llm     # noqa: E402

PORT = 47321
import updater  # noqa: E402
VERSION = updater.local_version()
UI_DIR = APP_DIR / "ui"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PRESETS = {
    "tripod":   {"label": "Tripod / Gimbal", "hint": "Strict - sirf bilkul steady aur sharp shots",
                 "shake_threshold": 0.6, "blur_rel": 0.55, "whip_threshold": 4.5},
    "balanced": {"label": "Balanced", "hint": "Zyada tar documentary footage ke liye",
                 "shake_threshold": 0.9, "blur_rel": 0.45, "whip_threshold": 6.0},
    "handheld": {"label": "Handheld / Run-and-gun", "hint": "Natural handheld movement rakhta hai, sirf jhatke hatata hai",
                 "shake_threshold": 1.3, "blur_rel": 0.38, "whip_threshold": 8.0},
}
TUNABLE = ["shake_threshold", "blur_rel", "whip_threshold", "blur_abs", "dark_luma", "bright_clip_pct",
           "min_select_sec", "broll_target_sec", "broll_max_sec", "max_selects_per_clip",
           "aroll_min_speech_ratio", "workers", "use_proxies", "preset"]

JOB = {"running": False, "phase": "idle", "out": None, "progress": {}, "error": None,
       "started": None, "finished": None, "mode": None}
JOB_LOCK = threading.Lock()
LAST_SEEN = [time.time()]


def default_projects_root():
    return str(Path.home() / "Chhanni Projects")


def gpu_name():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


_GPU = {"name": None, "checked": False}


def project_info(out):
    out = Path(out)
    info = {"out": str(out), "name": out.name, "exists": out.exists()}
    try:
        info.update(json.loads((out / "project.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    sp = out / "selects.json"
    if sp.exists():
        try:
            d = json.loads(sp.read_text(encoding="utf-8"))
            cl = d.get("clips", [])
            info["summary"] = {
                "clips": len(cl), "selects": sum(len(c["selects"]) for c in cl),
                "hours": round(sum(c.get("duration") or 0 for c in cl) / 3600, 2),
                "select_min": round(sum(s["end"] - s["start"] for c in cl for s in c["selects"]) / 60, 1),
                "a_roll": sum(c["type"] == "A" for c in cl), "no_usable": sum(not c["selects"] for c in cl),
                "generated": d.get("generated"), "errors": len(d.get("errors", []))}
        except Exception:
            pass
    return info


def save_project_meta(out, **kw):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "project.json"
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        meta = {"created": dt.datetime.now().isoformat(timespec="seconds")}
    meta.update(kw)
    p.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return meta


def effective_settings(out):
    cfg = engine.load_config(Path(out)) if out else dict(engine.DEFAULTS)
    cfg.setdefault("preset", "balanced")
    return cfg


def save_settings(out, settings):
    p = Path(out) / "config.json"
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    for k in TUNABLE:
        if k in settings and settings[k] is not None:
            v = settings[k]
            if k in ("workers", "max_selects_per_clip"):
                v = int(v)
            elif k == "use_proxies":
                v = bool(v)
            elif k != "preset":
                v = float(v)
            cur[k] = v
    p.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return cur


# ------------------------------------------------------------------ jobs
def _progress(d):
    with JOB_LOCK:
        ph = d.get("phase", JOB["phase"])
        if ph in ("select", "thumbs", "tags"):
            JOB["progress"]["phase"] = ph
            JOB["progress"]["sub"] = {"done": d.get("done", 0), "total": d.get("total", 0)}
        elif ph == "finished":
            JOB["progress"]["phase"] = ph
        else:
            JOB["progress"].update(d)
        JOB["phase"] = ph


def ai_cfg(out):
    cfg = engine.load_config(Path(out))
    st = llm.load_settings()
    for k in ("whisper_model", "language", "use_clip", "dead_air_sec"):
        if st.get(k) not in (None, ""):
            cfg[k] = st[k]
    return cfg


def run_job(out, folders, mode):
    out = Path(out)
    try:
        cfg = ai_cfg(out)
        if mode == "transcribe":
            import ai_audio
            data, review = plan.load_project(out)
            hot = None
            sp = out / "script.txt"
            if sp.exists():
                hot = " ".join(sp.read_text(encoding="utf-8").split())[:400]
            ai_audio.transcribe_project(out, data["clips"], engine.FFMPEG, cfg, log=engine.log, progress=_progress,
                                        stop=engine.STOP, hotwords=hot)
            if engine.STOP.is_set():
                with JOB_LOCK:
                    JOB["phase"] = "stopped"
                return
        if mode == "all":
            roots = [str(p) for p in engine.normalize_roots(folders)]
            if not roots:
                raise RuntimeError("Koi valid footage folder nahi mila.")
            (out / "roots.json").write_text(json.dumps(roots, ensure_ascii=False), encoding="utf-8")
            ok = engine.cmd_analyze(roots, out, cfg, progress=_progress)
            if not ok:
                with JOB_LOCK:
                    JOB["phase"] = "stopped"
                return
        engine.cmd_select(out, cfg, progress=_progress)
        with JOB_LOCK:
            JOB["phase"] = "finished"
    except Exception as e:
        engine.log(f"[error] {e}")
        with JOB_LOCK:
            JOB["phase"] = "error"
            JOB["error"] = str(e)
    finally:
        with JOB_LOCK:
            JOB["running"] = False
            JOB["finished"] = time.time()


def start_job(out, folders, mode):
    with JOB_LOCK:
        if JOB["running"]:
            raise RuntimeError("Ek run pehle se chal raha hai.")
        JOB.update(running=True, phase="starting", out=str(out), progress={}, error=None,
                   started=time.time(), finished=None, mode=mode)
    engine.STOP.clear()
    threading.Thread(target=run_job, args=(out, folders, mode), daemon=True).start()


# ------------------------------------------------------------------ helpers
def pick_folder(title="Footage folder chuniye"):
    code = ("import tkinter as tk; from tkinter import filedialog; r=tk.Tk(); r.withdraw(); "
            "r.attributes('-topmost', True); "
            f"p=filedialog.askdirectory(title={title!r}); print(p or '')")
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-5] + ".exe"
    r = subprocess.run([exe, "-c", code], capture_output=True, text=True, creationflags=NO_WINDOW)
    p = r.stdout.strip()
    return os.path.normpath(p) if p else None


def pick_file(title="File chuniye", kind="audio"):
    ft = "[('Audio', '*.wav *.mp3 *.aif *.aiff *.m4a *.flac *.aac'), ('All files', '*.*')]" if kind == "audio" \
        else "[('Text', '*.txt *.md *.fountain'), ('All files', '*.*')]"
    code = ("import tkinter as tk; from tkinter import filedialog; r=tk.Tk(); r.withdraw(); "
            "r.attributes('-topmost', True); "
            f"p=filedialog.askopenfilename(title={title!r}, filetypes={ft}); print(p or '')")
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-5] + ".exe"
    r = subprocess.run([exe, "-c", code], capture_output=True, text=True, creationflags=NO_WINDOW)
    p = r.stdout.strip()
    if p and kind == "text":
        try:
            return {"path": p, "text": Path(p).read_text(encoding="utf-8", errors="ignore")}
        except Exception:
            return {"path": p}
    return os.path.normpath(p) if p else None


def quick_scan(folders):
    roots = engine.normalize_roots(folders)
    files = engine.scan(roots)
    pidx = engine.build_proxy_index(roots, [])
    size = 0
    groups = {}
    with_proxy = 0
    for f in files:
        try:
            size += f.stat().st_size
        except OSError:
            pass
        g = engine.group_label(f, roots)
        groups[g] = groups.get(g, 0) + 1
        if f.stem.lower() in pidx:
            with_proxy += 1
    return {"roots": [str(r) for r in roots], "files": len(files), "bytes": size,
            "with_proxy": with_proxy, "groups": sorted(groups.items())}


def reveal(path):
    if os.name == "nt":
        p = os.path.normpath(path)
        if os.path.isdir(p):
            subprocess.Popen(["explorer", p])
        else:
            subprocess.Popen(["explorer", "/select,", p])


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    server_version = "Chhanni/" + VERSION

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "max-age=3600" if ctype.startswith(("image/", "video/", "audio/")) else "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_GET(self):
        LAST_SEEN[0] = time.time()
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/ping":
                return self._send(200, {"app": "chhanni", "version": VERSION})
            if u.path == "/api/state":
                with JOB_LOCK:
                    job = dict(JOB)
                    job["progress"] = dict(JOB["progress"])
                if "current" in job["progress"]:
                    job["progress"]["current"] = list(job["progress"]["current"])
                since = float(q.get("since", 0))
                logs = [{"t": t, "m": m} for t, m in list(engine.LOG) if t > since][-150:]
                if not _GPU["checked"]:
                    _GPU["name"], _GPU["checked"] = gpu_name(), True
                return self._send(200, {"job": job, "log": logs, "gpu": _GPU["name"],
                                        "cuda": engine.hw_caps().get("cuda"), "version": VERSION})
            if u.path == "/api/projects":
                lst = [project_info(p["out"]) for p in plan.recent_projects()]
                return self._send(200, {"projects": lst, "root": default_projects_root(),
                                        "presets": PRESETS})
            if u.path == "/api/project":
                out = q["out"]
                info = project_info(out)
                info["settings"] = effective_settings(out)
                return self._send(200, info)
            if u.path == "/api/results":
                out = q["out"]
                data, review = plan.load_project(out)
                if data.get("clips") and "qc" not in data["clips"][0]:
                    # project from Chhanni v1 -> rebuild selects in the new format (fast, uses the cache)
                    engine.cmd_select(Path(out), engine.load_config(Path(out)))
                    data, review = plan.load_project(out)
                return self._send(200, {"clips": data.get("clips", []), "review": review,
                                        "errors": data.get("errors", []), "generated": data.get("generated"),
                                        "settings": effective_settings(out)})
            if u.path == "/api/ai/status":
                import ai_audio
                import ai_visual
                st = llm.public_settings()
                info = {"whisper": ai_audio.whisper_available(), "clip": ai_visual.clip_available(), "settings": st,
                        "defaults": llm.DEFAULT_MODELS}
                if q.get("out"):
                    td = Path(q["out"]) / "transcripts"
                    info["transcripts"] = len(list(td.glob("*.json"))) if td.exists() else 0
                return self._send(200, info)
            if u.path == "/api/update/status":
                st = {}
                sp = Path(os.environ.get("APPDATA", Path.home())) / "Chhanni" / "update_state.json"
                try:
                    st = json.loads(sp.read_text(encoding="utf-8"))
                except Exception:
                    pass
                st.update(config=updater.public_cfg(), local=updater.local_version())
                return self._send(200, st)
            if u.path == "/api/script":
                p = Path(q["out"]) / "script.txt"
                return self._send(200, {"text": p.read_text(encoding="utf-8") if p.exists() else ""})
            if u.path == "/api/versions":
                import assemble
                return self._send(200, {"versions": assemble.list_versions(q["out"])})
            if u.path == "/api/version":
                import assemble
                return self._send(200, assemble.load_version(q["out"], q["id"]))
            if u.path == "/api/transcript":
                import ai_audio
                return self._send(200, ai_audio.load_transcript(q["out"], q["id"]) or {})
            if u.path == "/api/plan":
                inc = tuple((q.get("include") or "A,B").split(","))
                return self._send(200, plan.plan_for_host(q["out"], group_by=q.get("group_by", "group"), include=inc))
            if u.path.startswith("/thumbs/"):
                out = q.get("out")
                name = os.path.basename(unquote(u.path[len("/thumbs/"):]))
                p = Path(out) / "thumbs" / name
                if p.exists():
                    return self._send(200, p.read_bytes(), "image/jpeg")
                return self._send(404, b"", "text/plain")
            # static UI
            rel = unquote(u.path.lstrip("/")) or "index.html"
            p = (UI_DIR / rel).resolve()
            if UI_DIR in p.parents or p == UI_DIR / "index.html":
                if p.is_file():
                    ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
                    if ctype.startswith("text/") or ctype in ("application/javascript",):
                        ctype += "; charset=utf-8"
                    return self._send(200, p.read_bytes(), ctype)
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        LAST_SEEN[0] = time.time()
        u = urlparse(self.path)
        try:
            b = self._json()
            if u.path == "/api/browse":
                return self._send(200, {"path": pick_folder(b.get("title") or "Folder chuniye")})
            if u.path == "/api/scan":
                return self._send(200, quick_scan(b.get("folders", [])))
            if u.path == "/api/start":
                name = (b.get("name") or "").strip() or dt.datetime.now().strftime("Project %Y-%m-%d %H%M")
                out = b.get("out") or os.path.join(b.get("root") or default_projects_root(),
                                                  "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip())
                folders = b.get("folders") or []
                if not folders:
                    raise RuntimeError("Footage folder add kijiye.")
                Path(out).mkdir(parents=True, exist_ok=True)
                save_project_meta(out, name=name, folders=folders)
                save_settings(out, b.get("settings") or {})
                engine.remember_project(Path(out))
                start_job(out, folders, "all")
                return self._send(200, {"ok": True, "out": out})
            if u.path == "/api/reselect":
                out = b["out"]
                save_settings(out, b.get("settings") or {})
                start_job(out, [], "select")
                return self._send(200, {"ok": True})
            if u.path == "/api/resume":
                out = b["out"]
                meta = project_info(out)
                folders = meta.get("folders") or []
                if not folders:
                    try:
                        folders = json.loads((Path(out) / "roots.json").read_text(encoding="utf-8"))
                    except Exception:
                        folders = []
                start_job(out, folders, "all")
                return self._send(200, {"ok": True})
            if u.path == "/api/stop":
                engine.request_stop()
                return self._send(200, {"ok": True})
            if u.path == "/api/review":
                out = b["out"]
                review = plan.load_review(out)
                for k in ("off", "clip_off", "type", "star"):
                    if k in b.get("review", {}):
                        review[k] = b["review"][k]
                plan.save_review(out, review)
                return self._send(200, {"ok": True})
            if u.path == "/api/export":
                out = b["out"]
                inc = tuple(b.get("include") or ["A", "B"])
                kind = b.get("kind", "xml")
                if kind == "xml":
                    p = plan.plan_for_host(out, group_by=b.get("group_by", "group"), include=inc)
                    ed = Path(out) / "exports"
                    ed.mkdir(exist_ok=True)
                    fn = ed / f"{Path(out).name} - Chhanni selects {dt.datetime.now():%Y%m%d-%H%M}.xml"
                    plan.export_xmeml(p["sequences"], str(fn), Path(out).name)
                    return self._send(200, {"ok": True, "path": str(fn),
                                            "sequences": len(p["sequences"])})
                if kind == "resolve":
                    import resolve_bridge
                    rv = resolve_bridge.get_resolve()
                    lines = []
                    s = resolve_bridge.import_project(rv, out, group_by=b.get("group_by", "group"),
                                                      include=inc, slowmo_hfr=b.get("slowmo", True),
                                                      log=lambda m: (lines.append(m), engine.log(m)))
                    return self._send(200, {"ok": True, "summary": s, "log": lines})
                raise RuntimeError("unknown export")
            if u.path == "/api/update/config":
                updater.save_cfg({k: b.get(k) for k in ("repo", "branch", "auto")} | ({"token": b["token"]} if b.get("token") else {}))
                return self._send(200, {"ok": True, "config": updater.public_cfg()})
            if u.path == "/api/update/check":
                return self._send(200, updater.check())
            if u.path == "/api/update/install":
                with JOB_LOCK:
                    if JOB["running"]:
                        raise RuntimeError("Pehle chal raha kaam khatam hone dijiye.")
                lines = []
                r = updater.install(log=lambda m: (lines.append(m), engine.log(m)))
                updater.restart_later()
                threading.Thread(target=lambda: (time.sleep(0.8), os._exit(0)), daemon=True).start()
                return self._send(200, {"ok": True, "result": r, "log": lines})
            if u.path == "/api/update/rollback":
                r = updater.rollback()
                updater.restart_later()
                threading.Thread(target=lambda: (time.sleep(0.8), os._exit(0)), daemon=True).start()
                return self._send(200, {"ok": True, "result": r})
            if u.path == "/api/browse_file":
                return self._send(200, {"path": pick_file(b.get("title") or "File chuniye", b.get("kind", "audio"))})
            if u.path == "/api/ai/settings":
                st = {k: b.get(k) for k in ("llm_provider", "llm_model", "base_url", "whisper_model", "language", "use_clip")}
                if b.get("api_key"):
                    st["api_key"] = b["api_key"]
                llm.save_settings(st)
                return self._send(200, {"ok": True, "settings": llm.public_settings()})
            if u.path == "/api/ai/test_llm":
                return self._send(200, {"ok": llm.test()})
            if u.path == "/api/ai/install":
                bat = APP_DIR.parent / "setup_ai.bat"
                subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=str(APP_DIR.parent))
                return self._send(200, {"ok": True})
            if u.path == "/api/transcribe":
                start_job(b["out"], [], "transcribe")
                return self._send(200, {"ok": True})
            if u.path == "/api/script":
                (Path(b["out"]) / "script.txt").write_text(b.get("text", ""), encoding="utf-8")
                return self._send(200, {"ok": True})
            if u.path == "/api/assemble":
                import assemble
                out = b["out"]
                if b.get("script") is not None:
                    (Path(out) / "script.txt").write_text(b["script"], encoding="utf-8")
                data, review = plan.load_project(out)
                clips = [c for c in plan.effective_clips(data, review) if not c.get("missing")]
                vs = assemble.build(out, clips, b["mode"], b.get("script") or "", b.get("music") or None,
                                    b.get("opts") or {}, engine.FFMPEG, ai_cfg(out), bool(b.get("concept")))
                return self._send(200, {"versions": [{k: v[k] for k in ("id", "name", "mode", "duration", "notes")} for v in vs]})
            if u.path == "/api/export_version":
                import assemble
                v = assemble.load_version(b["out"], b["id"])
                if b.get("kind") == "xml":
                    ed = Path(b["out"]) / "exports"
                    ed.mkdir(exist_ok=True)
                    safe = "".join(ch for ch in v["name"] if ch not in '<>:"/\\|?*')
                    fn = ed / f"{safe} {dt.datetime.now():%Y%m%d-%H%M}.xml"
                    plan.export_version_xml(v, str(fn))
                    return self._send(200, {"ok": True, "path": str(fn)})
                import resolve_bridge
                rv = resolve_bridge.get_resolve()
                lines = []
                r = resolve_bridge.import_version(rv, b["out"], v, b.get("slowmo", True),
                                                  log=lambda m: (lines.append(m), engine.log(m)),
                                                  replace=b.get("replace", True))
                return self._send(200, {"ok": True, "result": r, "log": lines})
            if u.path == "/api/regenerate":
                import assemble
                import instruct
                out = b["out"]
                v = assemble.load_version(out, b["id"])
                src = v.get("source") or {"mode": v.get("mode"), "script": v.get("script", ""), "music": v.get("music"),
                                          "opts": v.get("opts") or {}, "concept": False}
                opts = dict(src.get("opts") or {})
                notes = []
                if b.get("changes"):
                    opts.update(b["changes"])
                    notes.append("switches updated")
                if (b.get("instruction") or "").strip():
                    ch, n2, used_llm = instruct.apply(b["instruction"], dict(v, opts=opts))
                    opts.update(ch)
                    notes += n2
                data, review = plan.load_project(out)
                clips = [c for c in plan.effective_clips(data, review) if not c.get("missing")]
                base = v["name"].split(" · v")[0]
                gen = int(v.get("gen") or 1) + 1
                opts.update(_single=True, _name=f"{base} · v{gen}",
                            _lineage={"root": v.get("root") or v["id"], "parent": v["id"], "instruction": b.get("instruction"),
                                      "gen": gen})
                music = opts.get("music") if "music" in opts else src.get("music")
                vs = assemble.build(out, clips, src.get("mode") or "reel", src.get("script") or "", music or None, opts,
                                    engine.FFMPEG, ai_cfg(out), bool(src.get("concept")))
                nv = vs[0]
                return self._send(200, {"version": {k: nv.get(k) for k in ("id", "name", "mode", "duration", "notes")},
                                        "changes": notes})
            if u.path == "/api/delete_version":
                vd = Path(b["out"]) / "versions"
                ids = b.get("ids") or [b.get("id")]
                n = 0
                for vid in ids:
                    p_ = vd / f"{vid}.json"
                    if vid and p_.exists():
                        p_.unlink()
                        n += 1
                return self._send(200, {"ok": True, "deleted": n})
            if u.path == "/api/smartbins":
                import resolve_bridge
                rv = resolve_bridge.get_resolve()
                lines = []
                r = resolve_bridge.smart_bins(rv, b["out"], log=lambda m: (lines.append(m), engine.log(m)))
                return self._send(200, {"ok": True, "result": r, "log": lines})
            if u.path == "/api/reveal":
                reveal(b["path"])
                return self._send(200, {"ok": True})
            if u.path == "/api/forget":
                pf = Path(plan.app_data_dir()) / "projects.json"
                lst = [p for p in plan.recent_projects() if os.path.normcase(p["out"]) != os.path.normcase(b["out"])]
                pf.write_text(json.dumps(lst, indent=1, ensure_ascii=False), encoding="utf-8")
                return self._send(200, {"ok": True})
            if u.path == "/api/quit":
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(400, {"error": str(e)})


def server_running():
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=1.5) as r:
            return json.loads(r.read()).get("app") == "chhanni"
    except Exception:
        return False


def serve(block=True):
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    if block:
        t.join()
    return httpd


def idle_seconds():
    with JOB_LOCK:
        if JOB["running"]:
            return 0
    return time.time() - LAST_SEEN[0]


if __name__ == "__main__":
    print(f"Chhanni server on http://127.0.0.1:{PORT}")
    serve(True)
