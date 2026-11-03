"""
Chhanni self-updater (GitHub).

The repo holds the Chhanni folder contents at its root plus version.json:
  {"version": "3.4", "notes": "...", "requirements": ["numpy", "opencv-python"]}
Settings: %APPDATA%\\Chhanni\\update.json  {"repo": "owner/chhanni", "branch": "main", "token": "", "auto": true}
Install dir: %LOCALAPPDATA%\\Chhanni\\app  (code only - projects, transcripts, settings and AI models live elsewhere)
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def local_version():
    try:
        return json.loads((APP_ROOT / "version.json").read_text(encoding="utf-8")).get("version", "0")
    except Exception:
        return "0"


def _cfg_path():
    d = Path(os.environ.get("APPDATA", Path.home())) / "Chhanni"
    d.mkdir(parents=True, exist_ok=True)
    return d / "update.json"


def load_cfg():
    try:
        c = json.loads(_cfg_path().read_text(encoding="utf-8"))
    except Exception:
        c = {}
    c.setdefault("repo", "")
    c.setdefault("branch", "main")
    c.setdefault("token", "")
    c.setdefault("auto", True)
    return c


def save_cfg(new):
    c = load_cfg()
    c.update({k: v for k, v in new.items() if v is not None})
    _cfg_path().write_text(json.dumps(c, indent=1), encoding="utf-8")
    return c


def public_cfg():
    c = dict(load_cfg())
    c["has_token"] = bool(c.get("token"))
    c.pop("token", None)
    return c


def _get(url, token=None, raw=False, timeout=30):
    h = {"User-Agent": "Chhanni-updater"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if raw:
        h["Accept"] = "application/vnd.github.raw"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _vtuple(v):
    out = []
    for p in str(v).split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def check():
    c = load_cfg()
    if not c["repo"]:
        return {"configured": False, "local": local_version()}
    url = f"https://api.github.com/repos/{c['repo']}/contents/version.json?ref={c['branch']}"
    remote = json.loads(_get(url, c["token"], raw=True))
    state = {"configured": True, "repo": c["repo"], "local": local_version(), "remote": remote.get("version"),
             "notes": remote.get("notes", ""), "checked": time.strftime("%Y-%m-%d %H:%M")}
    state["available"] = _vtuple(state["remote"]) > _vtuple(state["local"])
    (Path(_cfg_path().parent) / "update_state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
    return state


def install(log=print):
    """Download the repo zip, swap the app folder (backup kept), re-install panels, return new version."""
    c = load_cfg()
    if not c["repo"]:
        raise RuntimeError("Update repo set nahi hai (Settings > Updates).")
    log(f"[update] downloading {c['repo']}@{c['branch']} ...")
    data = _get(f"https://api.github.com/repos/{c['repo']}/zipball/{c['branch']}", c["token"], timeout=180)
    tmp = Path(tempfile.mkdtemp(prefix="chhanni_upd_"))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    tops = [p for p in tmp.iterdir() if p.is_dir()]
    src = tops[0] if len(tops) == 1 else tmp
    if not (src / "app" / "server.py").exists():
        raise RuntimeError("Repo mein Chhanni ka code nahi mila (app/server.py).")
    new_ver = json.loads((src / "version.json").read_text(encoding="utf-8")).get("version", "?")
    # requirements (only installs what is missing / listed)
    req = json.loads((src / "version.json").read_text(encoding="utf-8")).get("requirements") or []
    if req:
        log("[update] python packages check ...")
        subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-m", "pip", "install", "--quiet"] + req,
                       capture_output=True, creationflags=NO_WINDOW)
    dst = APP_ROOT
    bak = dst.with_name(dst.name + ".bak")
    if bak.exists():
        shutil.rmtree(bak, ignore_errors=True)
    # copy new files over (python files in use are fine on Windows to overwrite once loaded)
    shutil.copytree(dst, bak, ignore=shutil.ignore_patterns("__pycache__"))
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    for keep in ("ffmpeg_path.txt",):
        p = bak / "app" / keep
        if p.exists() and not (dst / "app" / keep).exists():
            shutil.copy2(p, dst / "app" / keep)
    shutil.rmtree(tmp, ignore_errors=True)
    log("[update] Resolve / Premiere panels ...")
    r = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), str(dst / "app" / "install_helper.py"), "--quiet"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    log(r.stdout.strip()[-500:])
    log(f"[update] Chhanni {new_ver} install ho gaya. Premiere panel ke liye Premiere ek baar restart karein.")
    return {"version": new_ver, "backup": str(bak)}


def rollback():
    bak = APP_ROOT.with_name(APP_ROOT.name + ".bak")
    if not bak.exists():
        raise RuntimeError("Backup nahi mila.")
    for item in bak.iterdir():
        target = APP_ROOT / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    return {"version": local_version()}


def restart_later(delay=1.5):
    """Start a fresh engine after this process exits (the app window reconnects by itself)."""
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):
        cand = pyw[:-10] + "pythonw.exe"
        pyw = cand if os.path.exists(cand) else pyw
    code = (f"import time,subprocess; time.sleep({delay}); "
            f"subprocess.Popen([r'{pyw}', r'{APP_ROOT / 'Chhanni.pyw'}', '--server'], cwd=r'{APP_ROOT}')")
    subprocess.Popen([sys.executable, "-c", code], creationflags=NO_WINDOW | 0x00000008, cwd=str(APP_ROOT))
