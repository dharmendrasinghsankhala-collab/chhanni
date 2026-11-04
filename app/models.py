"""
Model Manager - every AI model Chhanni can use, all free / open, all running on this PC.
Status checks are cheap (no model loading). Installs run in a background thread with a log.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = APP_DIR / "models"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
OLLAMA = "http://127.0.0.1:11434"
VLM_DEFAULT = "qwen3-vl:8b"

SFACE_URLS = [
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
]

CATALOG = [
    {"id": "qc", "group": "Core", "name": "Footage QC (shake, blur, exposure, audio)", "what": "FFmpeg + OpenCV",
     "size": "built-in", "use": "Har clip ke kharab hisse hatana, best sections"},
    {"id": "whisper", "group": "Audio", "name": "Whisper large-v3 (speech to text)", "what": "faster-whisper",
     "size": "~3 GB", "use": "Interview / dialogue transcript, Hindi-English-Rajasthani, script matching"},
    {"id": "clap", "group": "Audio", "name": "CLAP (sound events + music mood)", "what": "LAION CLAP via transformers",
     "size": "~0.8 GB", "use": "Music, taali, bheed, hawa, gaadi, silence... aur music ka mood (sad / epic / happy)"},
    {"id": "clip", "group": "Vision", "name": "CLIP ViT-B/32 (visual tags + search)", "what": "open_clip",
     "size": "~0.6 GB", "use": "Shot size, angle, lighting, location, din/raat, objects, aesthetic score, text se search"},
    {"id": "shots", "group": "Vision", "name": "TransNetV2 (cut / shot detection)", "what": "transnetv2-pytorch",
     "size": "~0.1 GB", "use": "Film / ad / documentary ko shots mein todna, reference style analysis"},
    {"id": "faces", "group": "Vision", "name": "YuNet + SFace (faces + people pehchaan)", "what": "OpenCV DNN",
     "size": "~40 MB", "use": "Chehre, kaun sa insaan kis shot mein hai, facial emotion"},
    {"id": "motion", "group": "Vision", "name": "Camera motion (pan / tilt / zoom / dolly / handheld)",
     "what": "OpenCV optical flow", "size": "built-in", "use": "Camera kya kar raha hai, kitni speed se"},
    {"id": "vlm", "group": "Understanding", "name": "Qwen3-VL 8B (scene description, action, mood)",
     "what": "Ollama (local)", "size": "~6 GB", "use": "Shot mein kya ho raha hai shabdon mein, AI Team ka dimaag"},
]


# ------------------------------------------------------------------ status
def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def ollama_tags(timeout=1.5):
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=timeout) as r:
            return [m.get("name") for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def ollama_exe():
    p = shutil.which("ollama")
    if p:
        return p
    la = os.environ.get("LOCALAPPDATA", "")
    for c in (Path(la) / "Programs" / "Ollama" / "ollama.exe", Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe"):
        if c.exists():
            return str(c)
    return None


def sface_path():
    return MODEL_DIR / "face_recognition_sface_2021dec.onnx"


def status():
    torch_ok = _has("torch")
    tags = ollama_tags()
    vlm_model = vlm_name()
    st = {
        "qc": {"ok": True},
        "whisper": {"ok": _has("faster_whisper")},
        "clap": {"ok": torch_ok and _has("transformers") and _hf_cached("laion/clap-htsat-unfused"),
                 "partial": torch_ok and _has("transformers")},
        "clip": {"ok": torch_ok and _has("open_clip")},
        "shots": {"ok": torch_ok and _has("transnetv2_pytorch")},
        "faces": {"ok": (MODEL_DIR / "face_recognition_sface_2021dec.onnx").exists()
                  and (MODEL_DIR / "face_detection_yunet_2023mar.onnx").exists()},
        "motion": {"ok": True},
        "vlm": {"ok": bool(tags) and any(t and t.split(":")[0] == vlm_model.split(":")[0] and
                                         (t == vlm_model or t.startswith(vlm_model)) for t in tags),
                "ollama": tags is not None, "ollama_installed": bool(ollama_exe()) or tags is not None,
                "model": vlm_model, "pulled": tags or []},
    }
    try:
        import torch
        st["gpu"] = {"cuda": bool(torch.cuda.is_available()),
                     "name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
    except Exception:
        st["gpu"] = {"cuda": False, "name": None}
    st["torch"] = torch_ok
    return st


def vlm_name():
    try:
        import llm
        return llm.load_settings().get("vlm_model") or VLM_DEFAULT
    except Exception:
        return VLM_DEFAULT


def _hf_cached(repo):
    base = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"
    d = base / ("models--" + repo.replace("/", "--"))
    return d.exists() and any(d.glob("snapshots/*/*"))


# ------------------------------------------------------------------ installs
INSTALL = {"running": False, "item": None, "log": [], "error": None, "done": []}
_ILOCK = threading.Lock()


def _ilog(msg):
    with _ILOCK:
        INSTALL["log"].append([time.time(), str(msg)[:400]])
        INSTALL["log"] = INSTALL["log"][-300:]


def _python():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        c = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(c):
            return c
    return exe


def _run(cmd, timeout=3600):
    _ilog("$ " + " ".join(cmd if isinstance(cmd, list) else [cmd])[:300])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=NO_WINDOW)
    t0 = time.time()
    last = ""
    for line in p.stdout:
        line = line.rstrip()
        if line and line != last and not line.startswith("  ━"):
            _ilog(line[-300:])
            last = line
        if time.time() - t0 > timeout:
            p.kill()
            raise RuntimeError("timeout")
    rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"command failed ({rc})")


def _pip(*pkgs):
    _run([_python(), "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", *pkgs])


def _ensure_torch():
    if _has("torch"):
        return
    _ilog("PyTorch (CUDA) install ho raha hai - ~2.5 GB, thoda samay lagega ...")
    _run([_python(), "-m", "pip", "install", "--disable-pip-version-check", "torch", "torchvision",
          "--index-url", "https://download.pytorch.org/whl/cu126"])


def _download(urls, dst, min_bytes):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    for u in urls:
        try:
            _ilog(f"download {u}")
            tmp = dst.with_suffix(".part")
            req = urllib.request.Request(u, headers={"User-Agent": "Chhanni"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            if tmp.stat().st_size >= min_bytes:
                tmp.replace(dst)
                _ilog(f"ok {dst.name} ({tmp.stat().st_size if tmp.exists() else dst.stat().st_size} bytes)")
                return True
            _ilog("file too small (mirror gave a pointer) - next mirror")
            tmp.unlink()
        except Exception as e:
            _ilog(f"failed: {e}")
    return False


def _install_ollama():
    if ollama_tags() is not None:
        return
    exe = ollama_exe()
    if not exe:
        _ilog("Ollama install ho raha hai (winget) ...")
        try:
            _run(["winget", "install", "-e", "--id", "Ollama.Ollama", "--accept-package-agreements",
                  "--accept-source-agreements", "--silent"], timeout=1800)
        except Exception as e:
            _ilog(f"winget: {e}")
        exe = ollama_exe()
        if not exe:
            raise RuntimeError("Ollama install nahi ho paya. ollama.com/download se install karke dobara try karein.")
    if ollama_tags() is None:
        _ilog("Ollama server start ho raha hai ...")
        subprocess.Popen([exe, "serve"], creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(1)
            if ollama_tags() is not None:
                break
    if ollama_tags() is None:
        raise RuntimeError("Ollama chal nahi raha.")


def _pull(model):
    _ilog(f"{model} download ho raha hai (pehli baar ~6 GB) ...")
    req = urllib.request.Request(OLLAMA + "/api/pull", data=json.dumps({"model": model, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    last = -1
    with urllib.request.urlopen(req, timeout=7200) as r:
        for line in r:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("error"):
                raise RuntimeError(o["error"])
            if o.get("total") and o.get("completed"):
                pct = int(100 * o["completed"] / o["total"])
                if pct // 5 != last // 5:
                    _ilog(f"{o.get('status', 'pull')} {pct}%")
                    last = pct
            elif o.get("status") and o.get("status") != "pulling manifest":
                _ilog(o["status"])


def install(item):
    if item == "whisper":
        _ensure_torch()
        _pip("faster-whisper", "nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10")
    elif item == "clip":
        _ensure_torch()
        _pip("open_clip_torch", "pillow")
    elif item == "shots":
        _ensure_torch()
        _pip("transnetv2-pytorch", "ffmpeg-python")
    elif item == "clap":
        _ensure_torch()
        _pip("transformers>=4.44", "librosa", "soundfile")
        _ilog("CLAP model download ho raha hai ...")
        _run([_python(), "-c", "from transformers import ClapModel, ClapProcessor;"
              "ClapModel.from_pretrained('laion/clap-htsat-unfused');ClapProcessor.from_pretrained('laion/clap-htsat-unfused');"
              "print('CLAP ready')"])
    elif item == "faces":
        if not sface_path().exists() and not _download(SFACE_URLS, sface_path(), 20_000_000):
            raise RuntimeError("SFace model download nahi hua.")
    elif item == "vlm":
        _install_ollama()
        model = vlm_name()
        if model not in (ollama_tags() or []):
            _pull(model)
        try:
            import llm
            s = llm.load_settings()
            upd = {"vlm_model": model, "vlm_mode": s.get("vlm_mode") or "auto"}
            if s.get("llm_provider") in (None, "", "none"):
                upd.update(llm_provider="ollama", llm_model=model)
            llm.save_settings(upd)
        except Exception:
            pass
    elif item in ("qc", "motion"):
        pass
    else:
        raise RuntimeError(f"unknown model {item}")


def install_async(items):
    with _ILOCK:
        if INSTALL["running"]:
            raise RuntimeError("Ek install pehle se chal raha hai.")
        INSTALL.update(running=True, error=None, log=[], done=[])

    def go():
        try:
            for it in items:
                with _ILOCK:
                    INSTALL["item"] = it
                _ilog(f"=== {it} ===")
                try:
                    install(it)
                    with _ILOCK:
                        INSTALL["done"].append(it)
                    _ilog(f"[ok] {it} ready")
                except Exception as e:
                    _ilog(f"[error] {it}: {e}")
                    with _ILOCK:
                        INSTALL["error"] = f"{it}: {e}"
        finally:
            with _ILOCK:
                INSTALL["running"] = False
                INSTALL["item"] = None

    threading.Thread(target=go, daemon=True).start()


def install_state():
    with _ILOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in INSTALL.items()}


# ------------------------------------------------------------------ self tests (run in a subprocess - safe)
TEST_CODE = {
    "clip": "import ai_visual;assert ai_visual._load_clip();print(ai_visual.clip_text(['a dog']).shape)",
    "shots": "import deep;m=deep.Models({});print('transnet', m.transnet() is not None)",
    "clap": "import deep;m=deep.Models({});print('clap', m.clap() is not None)",
    "faces": "import deep;m=deep.Models({});print('faces', m.face_rec() is not None)",
    "vlm": "import deep;print(deep.vlm_ping())",
    "whisper": "import ai_audio;print(ai_audio.whisper_available())",
}


def selftest(item):
    code = TEST_CODE.get(item)
    if not code:
        return {"ok": True, "out": "built-in"}
    p = subprocess.run([_python(), "-c", "import sys;sys.path.insert(0,r'%s');" % APP_DIR + code],
                       capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW, cwd=str(APP_DIR))
    out = (p.stdout + p.stderr).strip()[-800:]
    return {"ok": p.returncode == 0, "out": out}
