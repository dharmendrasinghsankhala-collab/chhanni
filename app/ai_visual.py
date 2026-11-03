"""
Chhanni visual intelligence.
Per select: shot size (faces), camera movement (from the motion cache), light & colour (golden hour, night,
dynamic range), hero score, and optional CLIP zero-shot tags (drone, rooms, scenes...) + embeddings for
script-to-visual matching.  CLIP is optional (AI pack); everything else is plain OpenCV/numpy.
"""
import json
import os
import threading
from pathlib import Path

import cv2
import numpy as np

# ------------------------------------------------------------------ faces -> shot size
_CASCADES = {}
_lock = threading.Lock()


def _cascade(name):
    with _lock:
        if name not in _CASCADES:
            cands = [os.path.join(getattr(cv2, "data", None).haarcascades if hasattr(cv2, "data") else "", name),
                     str(Path(__file__).with_name("models") / name), os.path.join(os.environ.get("CHHANNI_HAAR", ""), name)]
            c = None
            for p in cands:
                if p and os.path.exists(p):
                    c = cv2.CascadeClassifier(p)
                    if not c.empty():
                        break
                    c = None
            _CASCADES[name] = c
        return _CASCADES[name]


_YUNET = {}


def _yunet():
    with _lock:
        if "d" not in _YUNET:
            _YUNET["d"] = None
            m = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
            if m.exists() and hasattr(cv2, "FaceDetectorYN"):
                try:
                    _YUNET["d"] = cv2.FaceDetectorYN.create(str(m), "", (320, 320), 0.7, 0.3, 50)
                except Exception:
                    _YUNET["d"] = None
        return _YUNET["d"]


def faces(img_bgr):
    """[(x, y, w, h)] - YuNet DNN detector (OpenCV 4.5.4+/5.x), Haar cascades as fallback."""
    h, w = img_bgr.shape[:2]
    det = _yunet()
    if det is not None:
        with _lock:
            det.setInputSize((w, h))
            _, f = det.detect(img_bgr)
        return [] if f is None else [(int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in f]
    if not hasattr(cv2, "CascadeClassifier"):
        return []
    g = cv2.equalizeHist(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY))
    out = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        c = _cascade(name)
        if c is None:
            continue
        found = c.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(max(14, h // 30), max(14, h // 30)))
        for (x, y, fw, fh) in (found if len(found) else []):
            if not any(abs(x - a) < fw * 0.5 and abs(y - b) < fh * 0.5 for a, b, _, _ in out):
                out.append((int(x), int(y), int(fw), int(fh)))
    return out


def shot_size(img_bgr, fs):
    """CU / MCU / MS / WS (people) / WIDE (no faces). Uses the short side, so vertical 9:16 works too."""
    ref = min(img_bgr.shape[:2])
    if not fs:
        return "WIDE"
    r = max(f[3] for f in fs) / ref
    if r >= 0.30:
        return "CU"
    if r >= 0.17:
        return "MCU"
    if r >= 0.08:
        return "MS"
    return "WS"


SHOT_LABEL = {"CU": "Close-up", "MCU": "Medium close-up", "MS": "Medium", "WS": "Wide (people)", "WIDE": "Wide / establishing"}


# ------------------------------------------------------------------ light & colour
def light(img_bgr):
    ycc = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YCrCb)
    y = ycc[..., 0].astype(np.float32)
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    sat = float(hsv[..., 1].mean())
    b, g, r = [img_bgr[..., i].astype(np.float32) for i in range(3)]
    lit = y > 50
    warm = float((r - b)[lit].mean()) if lit.any() else 0.0
    p2, p50, p98 = np.percentile(y, [2, 50, 98])
    dr = float((p98 - p2) / 255.0)
    clip_hi = float((y >= 250).mean() * 100)
    clip_lo = float((y <= 5).mean() * 100)
    tags = []
    mean = float(y.mean())
    bright = (y > 110) & (hsv[..., 1] > 60)
    hue = hsv[..., 0]
    orange = float(((hue >= 5) & (hue <= 28) & bright).sum() / max(1, (y > 110).sum()))
    if mean < 45:
        tags.append("Night / low light")
    elif warm > 18 and orange > 0.30 and 55 < mean < 185:
        tags.append("Golden hour")
    elif warm < -12 and 35 < mean < 120 and sat > 45:
        tags.append("Blue hour")
    if dr > 0.82 and clip_hi < 2 and clip_lo < 3:
        tags.append("Rich dynamic range")
    if sat > 125:
        tags.append("Vivid colour")
    return {"luma": round(mean, 1), "sat": round(sat, 1), "warm": round(warm, 1), "dr": round(dr, 2),
            "clip_hi": round(clip_hi, 2), "clip_lo": round(clip_lo, 2), "orange": round(orange, 2), "tags": tags}


# ------------------------------------------------------------------ camera movement (from cache)
def movement(d, meta, s_sec, e_sec, path=""):
    fps = meta["analysis_fps"]
    W = meta["analysis_w"]
    a, b = int(s_sec * fps), max(int(s_sec * fps) + 2, int(e_sec * fps))
    dx, dy, resp = d["dx"][a:b], d["dy"][a:b], d["resp"][a:b]
    ok = resp > 0.03
    name = os.path.basename(path).upper()
    low = path.lower()
    drone = name.startswith("DJI_") or any(k in low for k in ("drone", "deron", "aerial", "mavic", "\\dji", "/dji"))
    if ok.sum() < 3:
        return {"move": "Drone / aerial" if drone else "Unknown", "pan": 0, "jit": 0}
    dx, dy = dx[ok], dy[ok]
    k = max(1, int(fps))
    ker = np.ones(k) / k
    sx = np.convolve(dx, ker, mode="same")
    sy = np.convolve(dy, ker, mode="same")
    jit = float(np.sqrt(((dx - sx) ** 2 + (dy - sy) ** 2).mean()) * 100 / W)
    speed = np.sqrt(sx ** 2 + sy ** 2) * 100 / W
    pan = float(np.median(speed))
    # direction consistency: |mean vector| / mean |vector|
    cons = float(np.hypot(sx.mean(), sy.mean()) / (np.hypot(sx, sy).mean() + 1e-6))
    horiz = abs(sx.mean()) >= abs(sy.mean())
    if drone:
        mv = "Drone / aerial"
    elif pan < 0.12 and jit < 0.35:
        mv = "Static (tripod)"
    elif jit < 0.30 and cons > 0.75 and pan < 1.2:
        mv = "Slider / gimbal move"
    elif jit < 0.45 and cons > 0.6:
        mv = "Smooth pan" if horiz else "Smooth tilt / crane"
    elif jit < 0.45:
        mv = "Smooth handheld / gimbal"
    else:
        mv = "Handheld"
    return {"move": mv, "pan": round(pan, 2), "jit": round(jit, 2), "cons": round(cons, 2)}


MOVE_BONUS = {"Drone / aerial": 1.0, "Slider / gimbal move": 1.0, "Smooth pan": 0.8, "Smooth tilt / crane": 0.9,
              "Smooth handheld / gimbal": 0.6, "Static (tripod)": 0.55, "Handheld": 0.25, "Unknown": 0.4}


def hero_score(sel, li, mv, shot, clip_aes=None):
    tech = sel["score"] / 100.0
    lq = 0.5 + 0.25 * min(1, max(0, (li["dr"] - 0.55) / 0.35)) + (0.25 if "Golden hour" in li["tags"] else 0) \
        + (0.1 if "Blue hour" in li["tags"] else 0) - (0.2 if li["clip_hi"] > 6 else 0)
    lq = min(1, max(0, lq))
    mb = MOVE_BONUS.get(mv["move"], 0.4)
    comp = {"CU": 0.8, "MCU": 0.85, "MS": 0.7, "WS": 0.7, "WIDE": 0.65}.get(shot, 0.6)
    if clip_aes is None:
        s = 0.42 * tech + 0.24 * lq + 0.24 * mb + 0.10 * comp
    else:
        s = 0.34 * tech + 0.20 * lq + 0.20 * mb + 0.08 * comp + 0.18 * clip_aes
    return int(round(100 * min(1, max(0, s))))


# ------------------------------------------------------------------ optional CLIP
CLIP_VOCAB = {
    "scene": {
        "Aerial view": "an aerial drone view from high above",
        "Landscape": "a wide landscape of nature, hills, fields or desert",
        "City / street": "a city street with buildings and traffic",
        "Interior": "the interior of a room",
        "Building exterior": "the outside facade of a building or house",
        "Crowd": "a crowd of many people",
        "Portrait": "a portrait of a person's face",
        "Hands / detail": "a close-up detail shot of hands or an object",
        "Food": "food and cooking",
        "Vehicle": "a car, bike or vehicle",
        "Animal": "an animal such as a dog, cow, horse, camel or bird",
        "People": "people standing, walking or talking",
        "Fashion / clothing": "clothes, garments and fashion on display",
        "Shop / store": "the inside of a shop or showroom with products on shelves",
        "Wedding": "a wedding with a groom or bride in traditional clothes",
        "Sign / logo": "a sign board, logo or text",
        "Office / work": "people working at a desk or computer",
        "Water": "water, a river, lake or sea",
        "Sky": "the sky with clouds or sunset",
        "Monument / fort / temple": "an old fort, palace, temple or monument",
        "Stage / performance": "a performer on stage with lights",
        "Dance": "people dancing",
    },
    "room": {
        "Kitchen": "a kitchen", "Bedroom": "a bedroom with a bed", "Bathroom": "a bathroom",
        "Living room": "a living room with a sofa", "Dining": "a dining room with a dining table",
        "Balcony / terrace": "a balcony or terrace", "Pool": "a swimming pool", "Garden": "a garden or lawn",
        "Facade": "the front exterior of a house", "Staircase": "a staircase", "Hallway": "a hallway or corridor",
        "Office": "an office or study room",
    },
}
AESTHETIC = ("a beautiful cinematic film still, professional cinematography", "an amateur boring blurry snapshot")
_CLIP = {"model": None, "tried": False}


def clip_available():
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _load_clip():
    if _CLIP["tried"]:
        return _CLIP["model"]
    _CLIP["tried"] = True
    try:
        import open_clip
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k", device=dev)
        model.eval()
        tok = open_clip.get_tokenizer("ViT-B-32")
        _CLIP["model"] = (model, pre, tok, dev, torch)
    except Exception as e:
        print("[clip] not available:", e)
        _CLIP["model"] = None
    return _CLIP["model"]


def clip_text(texts):
    m = _load_clip()
    if not m:
        return None
    model, pre, tok, dev, torch = m
    with torch.no_grad():
        t = model.encode_text(tok(texts).to(dev))
        t = t / t.norm(dim=-1, keepdim=True)
    return t.float().cpu().numpy()


def clip_images(paths, batch=32):
    m = _load_clip()
    if not m:
        return None
    model, pre, tok, dev, torch = m
    from PIL import Image
    out = []
    for i in range(0, len(paths), batch):
        ims = []
        for p in paths[i:i + batch]:
            try:
                ims.append(pre(Image.open(p).convert("RGB")))
            except Exception:
                ims.append(pre(Image.new("RGB", (224, 224))))
        with torch.no_grad():
            e = model.encode_image(torch.stack(ims).to(dev))
            e = e / e.norm(dim=-1, keepdim=True)
        out.append(e.float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 512), np.float32)


_VOCAB_EMB = {}


def clip_tags(emb):
    """emb: (N,512) image embeddings -> list of dicts {scene, room, aes}."""
    if "scene" not in _VOCAB_EMB:
        for g, d in CLIP_VOCAB.items():
            _VOCAB_EMB[g] = (list(d.keys()), clip_text(list(d.values())))
        _VOCAB_EMB["aes"] = clip_text(list(AESTHETIC))
    res = []
    for e in emb:
        r = {}
        for g in CLIP_VOCAB:
            names, T = _VOCAB_EMB[g]
            raw = T @ e
            sims = raw * 100
            p = np.exp(sims - sims.max())
            p /= p.sum()
            top = np.argsort(-p)[:2]
            # needs both a clear winner (softmax) and a real match (absolute cosine)
            min_raw = 0.235 if g == "scene" else 0.225
            r[g] = [(names[i], round(float(p[i]), 2)) for i in top if p[i] > 0.35 and raw[i] > min_raw]
        a = _VOCAB_EMB["aes"] @ e * 100
        pa = np.exp(a - a.max())
        r["aes"] = round(float(pa[0] / pa.sum()), 2)
        res.append(r)
    return res


# ------------------------------------------------------------------ project tagging
def tag_project(out_dir, clips, load_cache, cfg, log=print, progress=None):
    """Adds per-select 'shot', 'move', 'light', 'hero', 'tags' and clip-level 'tags'.  load_cache(clip) -> (meta, d)."""
    out_dir = Path(out_dir)
    tdir = out_dir / "thumbs"
    use_clip = cfg.get("use_clip", "auto")
    want_clip = (use_clip is True) or (use_clip == "auto" and clip_available())
    emb_file = out_dir / "clip_emb.npz"
    emb_cache = {}
    if want_clip and emb_file.exists():
        try:
            z = np.load(emb_file)
            emb_cache = {k: z[k] for k in z.files}
        except Exception:
            emb_cache = {}
    # CLIP embeddings for all thumbs not yet embedded
    if want_clip:
        need = []
        for c in clips:
            for s in c["selects"]:
                if s.get("thumb") and s["thumb"] not in emb_cache and (tdir / s["thumb"]).exists():
                    need.append(s["thumb"])
        if need and _load_clip():
            log(f"[clip] embedding {len(need)} thumbnails ...")
            E = clip_images([str(tdir / n) for n in need])
            for n, e in zip(need, E):
                emb_cache[n] = e.astype(np.float16)
            np.savez_compressed(emb_file, **emb_cache)
        elif need:
            want_clip = False
    n_done = 0
    total = sum(len(c["selects"]) for c in clips) or 1
    for c in clips:
        try:
            meta, d = load_cache(c)
        except Exception:
            meta, d = None, None
        ctags = set()
        for s in c["selects"]:
            n_done += 1
            if progress and n_done % 25 == 0:
                progress({"phase": "tags", "done": n_done, "total": total})
            img = cv2.imread(str(tdir / s["thumb"])) if s.get("thumb") else None
            if img is None:
                continue
            fs = faces(img)
            shot = shot_size(img, fs)
            li = light(img)
            mv = movement(d, meta, s["start"], s["end"], c["path"]) if d is not None else {"move": "Unknown"}
            tags = [SHOT_LABEL[shot], mv["move"]] + li["tags"]
            if len(fs) >= 3:
                tags.append("Group of people")
            aes = None
            if want_clip and s.get("thumb") in emb_cache:
                ct = clip_tags(np.asarray([emb_cache[s["thumb"]]], np.float32))[0]
                aes = ct["aes"]
                tags += [n for n, _ in ct["scene"]]
                if ct["room"]:
                    s["room"] = ct["room"][0][0]
                if any(n == "Aerial view" and p > 0.45 for n, p in ct["scene"]) and mv["move"] != "Drone / aerial":
                    mv["move"] = "Drone / aerial"
                    tags[1] = mv["move"]
            s["shot"] = shot
            s["move"] = mv["move"]
            s["light"] = li
            s["faces"] = len(fs)
            s["hero"] = hero_score(s, li, mv, shot, aes)
            s["tags"] = list(dict.fromkeys(tags))
            ctags.update(s["tags"])
        c["tags"] = sorted(ctags)
    # hero shots: best B-roll moments across the project
    broll = [s for c in clips if c["type"] == "B" for s in c["selects"] if "hero" in s]
    if broll:
        cut = max(75, float(np.percentile([s["hero"] for s in broll], 88)))
        for s in broll:
            if s["hero"] >= cut:
                s["tags"] = ["★ Hero shot"] + [t for t in s["tags"] if t != "★ Hero shot"]
                s["is_hero"] = True
    log(f"[tags] {n_done} selects tagged{' (+CLIP)' if want_clip else ''}, "
        f"{sum(1 for s in broll if s.get('is_hero'))} hero shots")
    return want_clip


def clip_search(out_dir, query, candidates):
    """Rank candidate thumbnails for a text query with CLIP. candidates: list of thumb names. -> {thumb: sim}"""
    emb_file = Path(out_dir) / "clip_emb.npz"
    if not emb_file.exists() or not clip_available():
        return None
    T = clip_text([query])
    if T is None:
        return None
    z = np.load(emb_file)
    res = {}
    for n in candidates:
        if n in z.files:
            res[n] = float(z[n].astype(np.float32) @ T[0])
    return res
