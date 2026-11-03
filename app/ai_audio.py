"""
Chhanni audio intelligence.
 - Local transcription with faster-whisper (GPU int8_float16, CPU fallback) - free, offline after first model download
 - Audio clipping detection
 - Transcript analysis: false starts, retakes (keeps the last take), filler words, dead air
 - Devanagari -> Roman transliteration so a Hinglish script can be matched with Hindi transcripts
"""
import difflib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ------------------------------------------------------------------ transliteration (Devanagari -> simple Roman)
_V = {"अ": "a", "आ": "aa", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
      "ऑ": "o", "ऍ": "e"}
_M = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o",
      "ॅ": "e", "ं": "n", "ँ": "n", "ः": "h", "्": ""}
_C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
      "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
      "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
      "ष": "sh", "स": "s", "ह": "h", "क़": "k", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "d", "ढ़": "rh", "फ़": "f", "य़": "y"}
_NUKTA = "़"


def translit(s):
    out = []
    chars = list(s)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if i + 1 < len(chars) and chars[i + 1] == _NUKTA:
            ch = ch + _NUKTA
            i += 1
        if ch in _C:
            out.append(_C[ch])
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt in _M:
                out.append(_M[nxt])
                i += 1
            elif nxt and (nxt in _C or nxt in _V):
                out.append("a")
        elif ch in _V:
            out.append(_V[ch])
        elif ch in _M:
            out.append(_M[ch])
        elif ch == "।":
            out.append(".")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


FILLERS = {"um", "umm", "uh", "uhh", "erm", "hmm", "mm", "aa", "aaa", "matlab", "basically", "actually",
           "you know", "i mean", "toh", "to", "na", "haan", "acha", "achha"}
_SOFT_FILLERS = {"to", "toh", "na", "haan", "acha", "achha", "actually", "basically"}   # only fillers when alone


def norm_text(s):
    s = translit(s.lower())
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    # light Hinglish spelling normalisation
    s = re.sub(r"(.)\1+", r"\1", s)                    # aaa -> a, mmm -> m
    s = s.replace("w", "v").replace("z", "j").replace("ph", "f")
    return " ".join(s.split())


def similarity(a, b):
    """0..1 fuzzy similarity for transliterated / normalised text (word + character level)."""
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    wa, wb = a.split(), b.split()
    seq = difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()
    cha = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()

    def grams(t):
        t = f" {t} "
        return {t[i:i + 3] for i in range(len(t) - 2)}
    ga, gb = grams(a), grams(b)
    jac = len(ga & gb) / max(1, len(ga | gb))
    cov = len(ga & gb) / max(1, len(ga))          # how much of the script line is present
    return max(seq, 0.5 * cha + 0.5 * jac, 0.85 * cov if len(wa) >= 4 else 0)


# ------------------------------------------------------------------ whisper
_MODEL = {"m": None, "key": None, "device": None}
_mlock = threading.Lock()


def _add_cuda_dll_dirs():
    """faster-whisper (CTranslate2) needs cuBLAS / cuDNN DLLs on Windows: use pip nvidia-* wheels or torch/lib."""
    if os.name != "nt":
        return
    import site
    roots = []
    try:
        roots += site.getsitepackages()
    except Exception:
        pass
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    for r in roots:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin", "torch/lib"):
            p = os.path.join(r, *sub.split("/"))
            if os.path.isdir(p):
                try:
                    os.add_dll_directory(p)
                    os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                except Exception:
                    pass


def whisper_available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def load_model(size="large-v3", log=print):
    with _mlock:
        if _MODEL["m"] is not None and _MODEL["key"] == size:
            return _MODEL["m"]
        _add_cuda_dll_dirs()
        from faster_whisper import WhisperModel
        last = None
        for dev, ct in (("cuda", "int8_float16"), ("cuda", "float16"), ("cpu", "int8")):
            try:
                log(f"[whisper] loading {size} on {dev} ({ct}) - first time downloads the model ...")
                m = WhisperModel(size, device=dev, compute_type=ct)
                # quick self-test so a broken CUDA install falls back now, not mid-project
                list(m.transcribe(np.zeros(16000, np.float32), language="en")[0])
                _MODEL.update(m=m, key=size, device=f"{dev}/{ct}")
                log(f"[whisper] ready on {dev}")
                return m
            except Exception as e:
                last = e
                log(f"[whisper] {dev}/{ct} failed: {str(e)[:160]}")
        raise RuntimeError(f"Whisper load failed: {last}")


def read_audio(src, ffmpeg, sr=16000):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src), "-map", "0:a:0", "-vn",
           "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0 or not r.stdout:
        return None
    return np.frombuffer(r.stdout, np.float32)


def clipping_windows(src, ffmpeg, win=0.5):
    """% of samples at full scale per window, from the ORIGINAL sample rate (streamed, low memory)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src), "-map", "0:a:0", "-vn",
           "-ac", "1", "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    sr = 48000
    n = int(sr * win)
    out = []
    while True:
        buf = p.stdout.read(n * 2)
        if not buf:
            break
        a = np.frombuffer(buf[: len(buf) // 2 * 2], np.int16)
        out.append(float((np.abs(a.astype(np.int32)) >= 32500).mean() * 100))
    p.wait()
    return np.asarray(out, np.float32), win


def transcribe(audio, model, language=None, hotwords=None):
    kw = dict(beam_size=5, vad_filter=True, word_timestamps=True, condition_on_previous_text=False,
              vad_parameters={"min_silence_duration_ms": 400})
    if language and language != "auto":
        kw["language"] = language
    if hotwords:
        kw["hotwords"] = hotwords[:400]
    try:
        segs, info = model.transcribe(audio, **kw)
    except TypeError:
        kw.pop("hotwords", None)
        segs, info = model.transcribe(audio, **kw)
    out = []
    for s in segs:
        words = [{"s": round(w.start, 2), "e": round(w.end, 2), "w": w.word.strip(), "p": round(w.probability, 2)}
                 for w in (s.words or [])]
        out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                    "avg_logprob": round(s.avg_logprob, 2), "no_speech": round(s.no_speech_prob, 2), "words": words})
    return {"language": info.language, "lang_prob": round(info.language_probability, 2), "segments": out}


# ------------------------------------------------------------------ transcript analysis
def analyze(tr, dead_air=1.2, clip_pct=None, clip_win=0.5):
    """Marks false starts, retakes (all but the last take), fillers, clipping; returns speech units."""
    PLAUSIBLE = {"hi", "en", "ur", "pa", "gu", "mr", "bn", "ta", "te", "kn", "ml", "or", "as", "ne", "sa"}
    if tr.get("lang_setting", "auto") == "auto" and tr.get("language") not in PLAUSIBLE and tr.get("lang_prob", 1) < 0.8:
        segs = []          # noise / music mis-detected as some random language -> no speech
    else:
        segs = [s for s in tr["segments"] if s["text"] and s.get("no_speech", 0) < 0.6
                and s.get("avg_logprob", 0) > -1.0 and len(s["text"].strip(" .,!?-…")) > 1]
    if clip_pct is not None:
        clip_pct = np.asarray(clip_pct, np.float32)
    for s in segs:
        s["norm"] = norm_text(s["text"])
        s["flags"] = []
        w = s["norm"].split()
        if w and all(x in FILLERS for x in w) or (len(w) <= 2 and all(x in FILLERS | _SOFT_FILLERS for x in w)):
            s["flags"].append("filler")
        if clip_pct is not None and len(clip_pct):
            a, b = int(s["start"] / clip_win), int(s["end"] / clip_win) + 1
            c = float(clip_pct[a:b].max()) if b > a and a < len(clip_pct) else 0.0
            if c > 0.05:
                s["flags"].append("audio clipping")
            s["clip"] = round(c, 3)
    # false starts: short segment whose words begin the next segment
    for i in range(len(segs) - 1):
        a, b = segs[i]["norm"].split(), segs[i + 1]["norm"].split()
        if 1 <= len(a) <= 5 and len(b) > len(a) and segs[i + 1]["start"] - segs[i]["end"] < 4:
            k = sum(1 for x, y in zip(a, b) if x == y)
            if k >= max(1, min(2, len(a))) and k >= 0.6 * len(a):
                segs[i]["flags"].append("false start")
    # retakes: later segment repeating the same line within 3 minutes -> earlier is superseded
    takes = []
    for i, s in enumerate(segs):
        if len(s["norm"].split()) < 4 or "false start" in s["flags"]:
            continue
        for j in range(i + 1, len(segs)):
            t = segs[j]
            if t["start"] - s["end"] > 180:
                break
            if len(t["norm"].split()) < 3:
                continue
            r = difflib.SequenceMatcher(None, s["norm"].split(), t["norm"].split(), autojunk=False).ratio()
            if r >= 0.62:
                s["flags"].append("retake")
                s["superseded_by"] = j
                takes.append((i, j))
                break
    # speech units: kept segments, words trimmed, split at dead air
    units = []
    for i, s in enumerate(segs):
        if any(f in s["flags"] for f in ("retake", "false start", "filler")):
            continue
        ws = [w for w in s.get("words", []) if w["w"]]
        if not ws:
            units.append({"start": s["start"], "end": s["end"], "text": s["text"], "seg": [i]})
            continue
        cur = [ws[0]]
        for w in ws[1:]:
            if w["s"] - cur[-1]["e"] > dead_air:
                units.append(_unit(cur, i))
                cur = [w]
            else:
                cur.append(w)
        units.append(_unit(cur, i))
    for u in units:
        u["flags"] = sorted({f for k in u["seg"] for f in segs[k]["flags"]})
    stats = {"segments": len(segs), "false_starts": sum("false start" in s["flags"] for s in segs),
             "retakes": sum("retake" in s["flags"] for s in segs), "fillers": sum("filler" in s["flags"] for s in segs),
             "clipping": sum("audio clipping" in s["flags"] for s in segs)}
    return {"segments": segs, "units": units, "stats": stats, "takes": takes}


def _unit(ws, seg_i):
    text = " ".join(w["w"] for w in ws).strip()
    return {"start": round(ws[0]["s"], 2), "end": round(ws[-1]["e"], 2), "text": text, "seg": [seg_i]}


def merge_units(units, max_gap=1.2, pad=0.15, max_len=60):
    """Join neighbouring speech units into editable selects (dead air above max_gap is cut)."""
    out = []
    for u in units:
        if out and u["start"] - out[-1]["end"] <= max_gap and (u["end"] - out[-1]["start"]) <= max_len:
            out[-1]["end"] = u["end"]
            out[-1]["text"] += " " + u["text"]
            out[-1]["flags"] = sorted(set(out[-1]["flags"]) | set(u.get("flags", [])))
        else:
            out.append({"start": u["start"], "end": u["end"], "text": u["text"], "flags": list(u.get("flags", []))})
    for o in out:
        o["start"] = max(0.0, round(o["start"] - pad, 2))
        o["end"] = round(o["end"] + pad, 2)
    return out


# ------------------------------------------------------------------ project
def transcript_path(out_dir, clip_id):
    return Path(out_dir) / "transcripts" / f"{clip_id}.json"


def load_transcript(out_dir, clip_id):
    p = transcript_path(out_dir, clip_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def transcribe_project(out_dir, clips, ffmpeg, cfg, log=print, progress=None, stop=None, hotwords=None):
    """Transcribe clips that contain speech. Resumable (skips clips already transcribed with the same model)."""
    out_dir = Path(out_dir)
    (out_dir / "transcripts").mkdir(exist_ok=True)
    size = cfg.get("whisper_model", "large-v3")
    lang = cfg.get("language", "auto")
    min_speech = float(cfg.get("transcribe_min_speech", 0.15))
    todo = []
    for c in clips:
        if c["type"] != "A" and c["stats"].get("speech_ratio", 0) < min_speech:
            continue
        old = load_transcript(out_dir, c["id"])
        if old and old.get("model") == size and old.get("lang_setting") == lang:
            continue
        todo.append(c)
    log(f"[whisper] {len(todo)} clips to transcribe ({size}, language={lang})")
    if not todo:
        return 0
    if not whisper_available():
        raise RuntimeError("Whisper install nahi hai - AI setup > 'Install AI pack' chalaiye.")
    model = load_model(size, log)
    t0 = time.time()
    total_sec = sum(c["duration"] or 0 for c in todo) or 1
    done_sec = 0
    for i, c in enumerate(todo):
        if stop is not None and stop.is_set():
            log("[whisper] stopped")
            break
        src = c.get("proxy") or c["path"]
        audio = read_audio(src, ffmpeg)
        if audio is None and c.get("proxy"):
            audio = read_audio(c["path"], ffmpeg)
        if audio is None or len(audio) < 8000:
            continue
        try:
            tr = transcribe(audio, model, lang, hotwords)
        except Exception as e:
            log(f"[whisper] {c['name']}: {e}")
            continue
        try:
            cp, win = clipping_windows(c["path"], ffmpeg)
            tr["clip_pct"] = [round(float(x), 3) for x in cp]
            tr["clip_win"] = win
        except Exception:
            pass
        tr.update(model=size, lang_setting=lang, device=_MODEL["device"], clip=c["name"])
        transcript_path(out_dir, c["id"]).write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
        done_sec += c["duration"] or 0
        el = time.time() - t0
        eta = el / done_sec * (total_sec - done_sec) if done_sec else None
        n_words = sum(len(s["words"]) for s in tr["segments"])
        log(f"[whisper {i+1}/{len(todo)}] {c['name']}: {len(tr['segments'])} segments, {n_words} words, {tr['language']}")
        if progress:
            progress({"phase": "transcribe", "done": i + 1, "total": len(todo), "eta": eta, "elapsed": el,
                      "current": [c["name"]]})
    return len(todo)
