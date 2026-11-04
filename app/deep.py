"""
Chhanni Deep Intelligence - "what is in this footage", per shot, the way an editor / DOP would log it.

Per clip:
  shots      TransNetV2 cut detection (fallback: colour-histogram cuts); long takes are split into ~8 s moments
  camera     optical-flow camera motion: static / pan / tilt / zoom / dolly-push / roll / handheld + speed
  frame      best frame per segment (sharpness + exposure + CLIP aesthetics) saved as keyframe
  look       CLIP zero-shot: shot size, angle, lighting, interior/exterior, time of day, location, subject
  faces      YuNet detection + SFace identity embedding (-> people.py clusters) + facial emotion (CLIP)
  sound      CLAP: sound events + music mood (per segment)
  meaning    Qwen3-VL (Ollama, local): description, action, mood, on-screen text, editorial use, hook potential
Results: <project>/deep/<clip_id>.json (+ .npz with CLIP / face embeddings), keyframes in deep/kf/.
Everything degrades gracefully: a missing model just leaves its fields empty.
"""
import base64
import json
import math
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np

import engine

DEEP_VERSION = 1
APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = APP_DIR / "models"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
OLLAMA = "http://127.0.0.1:11434"


def log(*a):
    engine.log(*a)


# ------------------------------------------------------------------ zero-shot vocab
LOOK = {
    "size": [("EWS", "an extreme wide establishing shot of a vast landscape or city"),
             ("WS", "a wide shot showing people full body in their surroundings"),
             ("MS", "a medium shot of a person from the waist up"),
             ("MCU", "a medium close-up of a person from the chest up"),
             ("CU", "a close-up of a person's face"),
             ("ECU", "an extreme close-up of eyes or a small detail"),
             ("INSERT", "a close-up insert shot of an object or hands")],
    "angle": [("eye", "a shot at eye level"), ("low", "a low angle shot looking up"),
              ("high", "a high angle shot looking down"), ("aerial", "an aerial drone shot from high in the sky"),
              ("top", "an overhead top-down shot")],
    "light": [("daylight", "natural soft daylight"), ("golden", "warm golden hour sunset light"),
              ("night", "a night scene lit by practical lamps"), ("lowkey", "dark moody low-key lighting with deep shadows"),
              ("highkey", "bright evenly lit high-key image"), ("silhouette", "a backlit silhouette"),
              ("neon", "colourful neon lights"), ("overcast", "flat grey overcast light")],
    "place": [("interior", "an indoor scene"), ("exterior", "an outdoor scene")],
    "time": [("day", "daytime"), ("golden", "sunset or sunrise"), ("night", "night time"), ("blue", "blue hour dusk")],
    "scene": [("desert", "a desert with sand dunes"), ("village", "an Indian village"), ("city", "a busy city street"),
              ("palace", "a palace, fort or haveli"), ("temple", "a temple or religious place"),
              ("market", "a market or bazaar"), ("home", "inside a home"), ("office", "an office or workplace"),
              ("stage", "a stage performance or concert"), ("studio", "a studio with a plain background"),
              ("nature", "a forest, trees or fields"), ("mountain", "mountains or hills"),
              ("water", "a river, lake or sea"), ("road", "a road or vehicle travel"), ("sky", "the sky or clouds"),
              ("farm", "a farm with animals or crops"), ("school", "a classroom or school"),
              ("hospital", "a hospital or clinic"), ("factory", "a factory or workshop"), ("kitchen", "a kitchen or food")],
    "subject": [("person", "a single person"), ("two", "two people together"), ("group", "a group of people"),
                ("crowd", "a large crowd"), ("hands", "close-up of hands doing something"),
                ("animal", "an animal"), ("landscape", "a landscape with no people"),
                ("architecture", "a building or architecture"), ("object", "an object or product"),
                ("food", "food"), ("vehicle", "a vehicle"), ("text", "text, a sign or graphics on screen"),
                ("instrument", "a musical instrument being played")],
}
AESTHETIC = (["a beautiful cinematic film still, professional lighting and composition, award winning cinematography",
              "a stunning well composed photograph with shallow depth of field"],
             ["a boring amateur snapshot, badly lit and poorly framed", "a blurry, dark, low quality video frame"])
EMOTIONS = [("happy", "a happy smiling face"), ("laughing", "a person laughing out loud"), ("sad", "a sad face"),
            ("crying", "a person crying with tears"), ("angry", "an angry face"), ("surprised", "a surprised face"),
            ("fear", "a scared, fearful face"), ("calm", "a calm neutral face"),
            ("serious", "a serious thoughtful face"), ("singing", "a person singing passionately")]
SOUNDS = [("speech", "people talking"), ("singing", "a person singing"), ("music", "music playing"),
          ("applause", "applause and clapping"), ("crowd", "crowd noise"), ("laughter", "people laughing"),
          ("crying", "a person crying"), ("silence", "silence or quiet room tone"), ("wind", "wind blowing"),
          ("rain", "rain"), ("birds", "birds chirping"), ("water", "flowing water or waves"),
          ("traffic", "city traffic and horns"), ("engine", "a vehicle engine"), ("footsteps", "footsteps"),
          ("animals", "animal sounds"), ("percussion", "drums and percussion"), ("bells", "temple bells"),
          ("machinery", "machines working"), ("impact", "a loud bang or impact")]
MOODS = [("happy", "happy upbeat music"), ("sad", "sad melancholic music"), ("tense", "tense suspenseful music"),
         ("epic", "epic cinematic orchestral music"), ("romantic", "romantic soft music"),
         ("calm", "calm peaceful ambient music"), ("folk", "Indian folk music"), ("devotional", "devotional spiritual music"),
         ("energetic", "energetic dance music")]


# ------------------------------------------------------------------ model holder (lazy, fault tolerant)
class Models:
    def __init__(self, cfg):
        self.cfg = cfg or {}
        self._c = {}
        self._txt = {}

    def _get(self, key, fn):
        if key not in self._c:
            try:
                self._c[key] = fn()
            except Exception as e:
                log(f"[deep] {key} not available: {str(e)[:200]}")
                self._c[key] = None
        return self._c[key]

    # CLIP (shared with ai_visual)
    def clip(self):
        def load():
            import ai_visual
            return ai_visual._load_clip()
        return self._get("clip", load)

    def clip_text(self, group, prompts):
        if group not in self._txt:
            import ai_visual
            self._txt[group] = ai_visual.clip_text(prompts)
        return self._txt[group]

    def clip_images(self, imgs_rgb):
        m = self.clip()
        if not m or not imgs_rgb:
            return None
        model, pre, tok, dev, torch = m
        from PIL import Image
        out = []
        with torch.no_grad():
            for i in range(0, len(imgs_rgb), 32):
                x = torch.stack([pre(Image.fromarray(im)) for im in imgs_rgb[i:i + 32]]).to(dev)
                e = model.encode_image(x)
                e = e / e.norm(dim=-1, keepdim=True)
                out.append(e.float().cpu().numpy())
        return np.concatenate(out)

    def transnet(self):
        def load():
            import torch
            from transnetv2_pytorch import TransNetV2
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            try:
                m = TransNetV2(device=dev)
            except TypeError:
                m = TransNetV2()
                m = m.to(dev)
            m.eval()
            return (m, dev, torch)
        return self._get("transnet", load)

    def face_det(self):
        def load():
            import cv2
            p = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
            if not p.exists():
                raise RuntimeError("YuNet model missing")
            return cv2.FaceDetectorYN.create(str(p), "", (320, 320), 0.7, 0.3, 200)
        return self._get("face_det", load)

    def face_rec(self):
        def load():
            import cv2
            p = MODEL_DIR / "face_recognition_sface_2021dec.onnx"
            if not p.exists():
                raise RuntimeError("SFace model missing (Models page se install karein)")
            return cv2.FaceRecognizerSF.create(str(p), "")
        return self._get("face_rec", load)

    def clap(self):
        def load():
            import torch
            from transformers import ClapModel, ClapProcessor
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            m = ClapModel.from_pretrained("laion/clap-htsat-unfused").to(dev).eval()
            p = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
            with torch.no_grad():
                te = {}
                for name, vocab in (("sounds", SOUNDS), ("moods", MOODS)):
                    t = p(text=[v for _, v in vocab], return_tensors="pt", padding=True)
                    e = _emb(m.get_text_features(**{k: v.to(dev) for k, v in t.items()}))
                    te[name] = (e / e.norm(dim=-1, keepdim=True)).float().cpu().numpy()
            return (m, p, dev, torch, te)
        return self._get("clap", load)

    def vlm(self):
        def load():
            mode = self.cfg.get("vlm_mode", "auto")
            if mode == "off":
                return None
            name = self.cfg.get("vlm_model") or "qwen3-vl:8b-instruct"
            with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=2) as r:
                tags = [m.get("name") for m in json.loads(r.read()).get("models", [])]
            if not any(t == name or t.startswith(name) for t in tags):
                raise RuntimeError(f"{name} pulled nahi hai (Models page)")
            return name
        return self._get("vlm", load)


def _emb(out):
    """transformers v4 returns a tensor, v5 returns a ModelOutput."""
    if hasattr(out, "norm"):
        return out
    for k in ("text_embeds", "audio_embeds", "image_embeds", "pooler_output"):
        v = getattr(out, k, None)
        if v is not None:
            return v
    return out[0]


def vlm_ping():
    m = Models({"vlm_mode": "auto"})
    try:
        import llm
        m.cfg["vlm_model"] = llm.load_settings().get("vlm_model")
    except Exception:
        pass
    name = m.vlm()
    if not name:
        return "VLM not ready"
    img = np.zeros((64, 64, 3), np.uint8)
    img[:, :32] = (40, 120, 220)
    t0 = time.time()
    r = vlm_describe(name, img, timeout=300)
    return f"{name} ok in {time.time() - t0:.1f}s: {str(r)[:120]}"


# ------------------------------------------------------------------ decoding
def _decode(src, fps, W=320, H=180):
    cmd = [engine.FFMPEG, "-v", "error", "-hwaccel", "auto", "-i", str(src), "-an", "-vf",
           f"fps={fps},scale={W}:{H}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    engine._track(p)
    n = W * H * 3
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield np.frombuffer(b, np.uint8).reshape(H, W, 3)
            if engine.STOP.is_set():
                break
    finally:
        try:
            p.kill()
        except Exception:
            pass
        engine._track(p, False)


def _keyframe(src, t, dst, width=960):
    cmd = [engine.FFMPEG, "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
           "-vf", f"scale={width}:-2", "-q:v", "3", str(dst)]
    subprocess.run(cmd, creationflags=NO_WINDOW, timeout=120)
    return Path(dst).exists()


def _read_rgb(path):
    import cv2
    im = cv2.imread(str(path))
    return None if im is None else cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


# ------------------------------------------------------------------ shots
def detect_shots(small48, fps, M):
    """small48: [N,27,48,3] uint8. Returns list of (start_frame, end_frame) inclusive."""
    N = len(small48)
    if N < 3:
        return [(0, max(0, N - 1))]
    tn = M.transnet()
    if tn is not None:
        try:
            m, dev, torch = tn
            with torch.no_grad():
                fr = torch.from_numpy(np.ascontiguousarray(small48))
                try:
                    res = m.predict_frames(fr, quiet=True)
                except TypeError:
                    res = m.predict_frames(fr)
                single = res[0]
                single = single.detach().cpu().numpy() if hasattr(single, "detach") else np.asarray(single)
            single = single.reshape(-1)[:N]
            cuts = [i for i in range(1, N) if single[i] > 0.5 and single[i] >= single[max(0, i - 1)]]
            return _cuts_to_shots(cuts, N, int(fps * 0.4))
        except Exception as e:
            log(f"[deep] TransNetV2 failed, using histogram cuts: {str(e)[:160]}")
    # fallback: colour-histogram + pixel difference spikes
    import cv2
    hs, gs = [], []
    for f in small48:
        hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]).flatten()
        hs.append(h / (h.sum() + 1e-6))
        gs.append(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32))
    d = np.zeros(N)
    for i in range(1, N):
        d[i] = 0.5 * np.abs(hs[i] - hs[i - 1]).sum() + float(np.abs(gs[i] - gs[i - 1]).mean()) / 64.0
    cuts = []
    for i in range(1, N):
        lft, rgt = d[max(1, i - 6):i], d[i + 1:i + 7]
        ref = min(float(np.median(lft)) if len(lft) else 0.0, float(np.median(rgt)) if len(rgt) else 0.0)
        if d[i] > max(0.25, 4 * ref + 0.05) and d[i] == d[max(1, i - 2):i + 3].max():
            cuts.append(i)
    return _cuts_to_shots(cuts, N, int(fps * 0.4))


def _cuts_to_shots(cuts, N, min_len):
    shots, s = [], 0
    for c in cuts:
        if c - s >= max(1, min_len):
            shots.append((s, c - 1))
            s = c
    shots.append((s, N - 1))
    return shots


# ------------------------------------------------------------------ camera motion
def camera_motion(grays, step_sec):
    """grays: list of 160x90 float32 frames sampled every step_sec. Per-second rates (fraction of frame)."""
    import cv2
    if len(grays) < 2:
        return {"label": "static", "speed": "still", "handheld": False}
    H, W = grays[0].shape
    ys, xs = np.mgrid[8:H - 8:10, 8:W - 8:10]
    pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    ctr = np.array([W / 2.0, H / 2.0], np.float32)
    tx, ty, ls, rot, par = [], [], [], [], []
    for a, b in zip(grays[:-1], grays[1:]):
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        d = flow[pts[:, 1].astype(int), pts[:, 0].astype(int)]
        src_c = pts - ctr                       # centre-relative so zoom does not look like a pan
        dst = src_c + d
        Mx, inl = cv2.estimateAffinePartial2D(src_c, dst, method=cv2.RANSAC, ransacReprojThreshold=1.5)
        if Mx is None:
            continue
        a_, b_ = Mx[0, 0], Mx[1, 0]
        s = math.hypot(a_, b_)
        pred = src_c @ Mx[:, :2].T + Mx[:, 2]
        res = np.linalg.norm(dst - pred, axis=1)
        tx.append(Mx[0, 2] / W / step_sec)
        ty.append(Mx[1, 2] / H / step_sec)
        ls.append(math.log(max(s, 1e-3)) / step_sec)
        rot.append(math.atan2(b_, a_) / step_sec)
        par.append(float(np.median(res)))
    if not tx:
        return {"label": "static", "speed": "still", "handheld": False}
    tx, ty, ls, rot = map(np.array, (tx, ty, ls, rot))
    mtx, mty, mls, mrot = np.median(tx), np.median(ty), np.median(ls), np.median(rot)

    def jit(x):
        if len(x) < 3:
            return 0.0
        sm = np.convolve(x, np.ones(3) / 3, "same")
        return float(np.std(x - sm))
    jitter = max(jit(tx), jit(ty))
    moves = []
    if abs(mtx) > 0.03:
        moves.append("pan right" if mtx < 0 else "pan left")
    if abs(mty) > 0.03:
        moves.append("tilt up" if mty > 0 else "tilt down")
    if abs(mls) > 0.015:
        push = float(np.median(par)) > 0.45
        moves.append(("push in" if mls > 0 else "pull out") if push else ("zoom in" if mls > 0 else "zoom out"))
    if abs(mrot) > 0.02:
        moves.append("roll")
    rate = max(abs(mtx), abs(mty), abs(mls))
    speed = "still" if rate < 0.01 else "slow" if rate < 0.07 else "medium" if rate < 0.2 else "fast"
    if rate > 0.6:
        moves = ["whip " + (moves[0] if moves else "pan")]
    handheld = jitter > 0.03
    label = " + ".join(moves) if moves else ("handheld" if handheld else "static")
    return {"label": label, "speed": speed, "handheld": bool(handheld), "pan": round(float(mtx), 3),
            "tilt": round(float(mty), 3), "zoom": round(float(mls), 3), "jitter": round(jitter, 4)}


# ------------------------------------------------------------------ frame quality
def frame_quality(rgb):
    import cv2
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
    mean = float(g.mean())
    clip_hi = float((g > 250).mean())
    clip_lo = float((g < 6).mean())
    expo = 1.0 - min(1.0, abs(mean - 115) / 115) - 2 * clip_hi - clip_lo
    return sharp, mean, expo


def _softmax(x, k=100.0):
    x = k * (x - x.max(axis=-1, keepdims=True))
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


# ------------------------------------------------------------------ audio (CLAP)
def audio_windows(src, M, dur, win=5.0):
    cl = M.clap()
    if cl is None:
        return []
    m, p, dev, torch, te = cl
    sr = 48000
    raw = subprocess.run([engine.FFMPEG, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-f",
                          "f32le", "-"], capture_output=True, creationflags=NO_WINDOW, timeout=1800).stdout
    y = np.frombuffer(raw, np.float32)
    if len(y) < sr:
        return []
    n = int(len(y) / (sr * win))
    step = max(1, n // 400)
    starts = list(range(0, n, step))
    out = []
    for i in range(0, len(starts), 16):
        chunk = [y[int(s * win * sr):int((s * win + win) * sr)] for s in starts[i:i + 16]]
        try:
            inp = p(audio=chunk, sampling_rate=sr, return_tensors="pt")
        except TypeError:
            inp = p(audios=chunk, sampling_rate=sr, return_tensors="pt")
        if "input_features" not in inp:
            inp = p(audios=chunk, sampling_rate=sr, return_tensors="pt")
        with torch.no_grad():
            e = _emb(m.get_audio_features(**{k: v.to(dev) for k, v in inp.items()}))
            e = (e / e.norm(dim=-1, keepdim=True)).float().cpu().numpy()
        ps = _softmax(e @ te["sounds"].T, 30)
        pm = _softmax(e @ te["moods"].T, 30)
        for j, s in enumerate(starts[i:i + 16]):
            rms = float(np.sqrt(np.mean(chunk[j] ** 2))) if len(chunk[j]) else 0.0
            ev = [(SOUNDS[k][0], round(float(ps[j, k]), 2)) for k in np.argsort(-ps[j])[:3] if ps[j, k] > 0.12]
            mood = MOODS[int(pm[j].argmax())][0] if any(n_ == "music" or n_ == "singing" for n_, _ in ev) else None
            out.append({"t": s * win, "events": ev, "mood": mood,
                        "db": round(20 * math.log10(rms + 1e-9), 1)})
    return out


# ------------------------------------------------------------------ VLM
VLM_PROMPT = ("You are logging footage for a film editor. Look at this video frame and reply ONLY with JSON: "
              '{"description": one short sentence, "action": what people/things are doing or "none", '
              '"subjects": [main subjects], "objects": [up to 6 notable objects], "setting": where, '
              '"mood": one or two words, "on_screen_text": visible text or "", '
              '"editorial_use": one of establishing|interview|b-roll|detail|action|reaction|transition|performance, '
              '"hook_potential": 0-10 how strong this image is as an opening hook}')


def vlm_describe(model, rgb, timeout=180):
    import cv2
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
    body = {"model": model, "prompt": VLM_PROMPT, "images": [base64.b64encode(buf.tobytes()).decode()],
            "stream": False, "format": "json", "think": False, "keep_alive": "10m",
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 300}}
    req = urllib.request.Request(OLLAMA + "/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = json.loads(r.read()).get("response", "")
    import llm
    d = llm.parse_json(txt)
    return d if isinstance(d, dict) else {"description": str(d)[:200]}


# ------------------------------------------------------------------ per clip
def analyze_clip(clip, out_dir, M, progress=None):
    out_dir = Path(out_dir)
    dd = out_dir / "deep"
    kfd = dd / "kf"
    kfd.mkdir(parents=True, exist_ok=True)
    src = clip.get("proxy") if clip.get("proxy") and Path(clip["proxy"]).exists() else clip["path"]
    cfps = float(clip.get("fps") or 25)
    dur = float(clip.get("duration") or 0)
    fps = min(cfps, 30.0) if cfps < 31 else cfps / round(cfps / 25.0)
    flow_every = max(1, int(round(fps / 4)))         # ~4 fps for camera motion
    look_every = max(1, int(round(fps * (1.0 if dur < 1200 else 2.0))))  # 1 frame / s for best-frame search
    small48, grays, looks = [], [], []
    import cv2
    t0 = time.time()
    for i, f in enumerate(_decode(src, fps)):
        small48.append(cv2.resize(f, (48, 27), interpolation=cv2.INTER_AREA))
        if i % flow_every == 0:
            grays.append((i, cv2.cvtColor(cv2.resize(f, (160, 90), interpolation=cv2.INTER_AREA),
                                          cv2.COLOR_RGB2GRAY).astype(np.float32)))
        if i % look_every == 0:
            sh, mean, expo = frame_quality(f)
            looks.append((i, sh, expo, f))
    if engine.STOP.is_set():
        return None
    N = len(small48)
    if N == 0:
        raise RuntimeError("video decode nahi hua")
    shots = detect_shots(np.stack(small48), fps, M)
    del small48
    # segments: shots, long shots split into ~8 s moments
    segs = []
    for si, (a, b) in enumerate(shots):
        L = (b - a + 1) / fps
        parts = 1 if L <= 12 else int(math.ceil(L / 8.0))
        for k in range(parts):
            sa = a + int((b - a + 1) * k / parts)
            sb = a + int((b - a + 1) * (k + 1) / parts) - 1
            segs.append({"shot": si, "part": k, "f0": sa, "f1": sb})
    # sharpness normaliser for this clip
    sh_all = np.array([x[1] for x in looks]) if looks else np.array([1.0])
    sh_ref = float(np.percentile(sh_all, 90)) + 1e-6
    kf_imgs = []
    for j, s in enumerate(segs):
        s["start"] = round(s["f0"] / fps, 3)
        s["end"] = round((s["f1"] + 1) / fps, 3)
        g = [x for x in grays if s["f0"] <= x[0] <= s["f1"]]
        s["camera"] = camera_motion([x[1] for x in g], flow_every / fps)
        cand = [x for x in looks if s["f0"] <= x[0] <= s["f1"]]
        if cand:
            best = max(cand, key=lambda x: min(1.0, x[1] / sh_ref) + 0.8 * x[2])
            t_best = best[0] / fps
            s["sharp"] = round(min(1.0, best[1] / sh_ref), 3)
            s["exposure"] = round(best[2], 3)
        else:
            t_best = (s["start"] + s["end"]) / 2
            s["sharp"], s["exposure"] = None, None
        t_best = min(max(t_best, s["start"] + 0.05), max(s["start"], s["end"] - 0.05))
        s["kf_t"] = round(t_best, 3)
        kf = kfd / f"{clip['id']}_{j:04d}.jpg"
        if not kf.exists():
            _keyframe(src, t_best, kf)
        s["kf"] = kf.name if kf.exists() else None
        img = _read_rgb(kf) if kf.exists() else None
        if img is None:
            best_small = cand and max(cand, key=lambda x: x[1])[3]
            img = best_small if best_small is not None and len(cand) else None
        kf_imgs.append(img)
        if progress and j % 10 == 0:
            progress({"sub": f"keyframes {j + 1}/{len(segs)}"})
    del grays, looks
    # ---- CLIP look + aesthetics
    valid = [i for i, im in enumerate(kf_imgs) if im is not None]
    E = M.clip_images([kf_imgs[i] for i in valid]) if valid else None
    clip_emb = np.zeros((len(segs), 512), np.float16)
    if E is not None:
        for gname, vocab in LOOK.items():
            T = M.clip_text("look_" + gname, [v for _, v in vocab])
            P = _softmax(E @ T.T)
            for r, i in enumerate(valid):
                k = int(P[r].argmax())
                segs[i][gname] = vocab[k][0]
                segs[i][gname + "_p"] = round(float(P[r, k]), 2)
        Tp = M.clip_text("aes_pos", AESTHETIC[0])
        Tn = M.clip_text("aes_neg", AESTHETIC[1])
        aes = (E @ Tp.T).mean(1) - (E @ Tn.T).mean(1)
        for r, i in enumerate(valid):
            clip_emb[i] = E[r].astype(np.float16)
            a = 1 / (1 + math.exp(-aes[r] * 40))
            sh = segs[i]["sharp"] if segs[i]["sharp"] is not None else 0.6
            ex = segs[i]["exposure"] if segs[i]["exposure"] is not None else 0.6
            segs[i]["aesthetic"] = round(a, 3)
            segs[i]["beauty"] = int(round(100 * (0.55 * a + 0.25 * sh + 0.2 * max(0, ex))))
    # ---- faces + emotion
    face_emb = []
    fd, fr = (M.face_det(), M.face_rec()) if M.cfg.get("deep_faces", True) else (None, None)
    crops = []
    if fd is not None:
        for i in valid:
            img = kf_imgs[i]
            h, w = img.shape[:2]
            bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            fd.setInputSize((w, h))
            _, faces = fd.detect(bgr)
            fl = []
            for f in (faces if faces is not None else [])[:12]:
                if f[14] < 0.75:
                    continue
                x, y, fw, fh = [float(v) for v in f[:4]]
                d = {"box": [round(x / w, 4), round(y / h, 4), round(fw / w, 4), round(fh / h, 4)],
                     "score": round(float(f[14]), 2), "emb": None}
                if fr is not None and fh >= 28:
                    try:
                        al = fr.alignCrop(bgr, f)
                        feat = fr.feature(al).reshape(-1).astype(np.float32)
                        feat /= (np.linalg.norm(feat) + 1e-9)
                        d["emb"] = len(face_emb)
                        face_emb.append(feat)
                    except Exception:
                        pass
                fl.append(d)
                m = 0.25
                x0, y0 = int(max(0, x - m * fw)), int(max(0, y - m * fh))
                x1, y1 = int(min(w, x + fw * (1 + m))), int(min(h, y + fh * (1 + m)))
                if x1 - x0 > 20 and y1 - y0 > 20:
                    crops.append(img[y0:y1, x0:x1])
                    d["_crop"] = len(crops) - 1
            fl.sort(key=lambda d: -d["box"][3])
            segs[i]["faces"] = fl
            if fl:   # face size beats CLIP for people shots
                fh = fl[0]["box"][3]
                segs[i]["size"] = "ECU" if fh > 0.5 else "CU" if fh > 0.28 else "MCU" if fh > 0.17 else \
                    "MS" if fh > 0.09 else segs[i].get("size", "WS")
    if crops:
        Ec = M.clip_images(crops)
        if Ec is not None:
            T = M.clip_text("emotion", [v for _, v in EMOTIONS])
            P = _softmax(Ec @ T.T)
            for i in valid:
                for d in segs[i].get("faces", []):
                    r = d.pop("_crop", None)
                    if r is not None:
                        d["emotion"] = EMOTIONS[int(P[r].argmax())][0]
                        d["emotion_p"] = round(float(P[r].max()), 2)
                fl = segs[i].get("faces") or []
                if fl and fl[0].get("emotion"):
                    segs[i]["emotion"], segs[i]["emotion_p"] = fl[0]["emotion"], fl[0]["emotion_p"]
    for s_ in segs:
        for d in s_.get("faces", []):
            d.pop("_crop", None)
    # ---- sound
    if clip.get("has_audio") and M.cfg.get("deep_audio", True):
        try:
            aw = audio_windows(src if Path(src).suffix.lower() != ".braw" else clip["path"], M, dur)
        except Exception as e:
            aw = []
            log(f"[deep] audio skipped for {clip['name']}: {str(e)[:120]}")
        for s in segs:
            ws = [w for w in aw if w["t"] < s["end"] and w["t"] + 5 > s["start"]]
            if ws:
                agg = {}
                for w in ws:
                    for n, p in w["events"]:
                        agg[n] = max(agg.get(n, 0), p)
                s["sound"] = [n for n, _ in sorted(agg.items(), key=lambda x: -x[1])[:3]]
                moods = [w["mood"] for w in ws if w["mood"]]
                s["music_mood"] = max(set(moods), key=moods.count) if moods else None
                s["loudness"] = round(float(np.mean([w["db"] for w in ws])), 1)
    # ---- VLM meaning
    vlm = M.vlm()
    if vlm:
        mode = M.cfg.get("vlm_mode", "auto")
        order = [i for i in valid if (segs[i]["end"] - segs[i]["start"]) >= (0 if mode == "all" else 1.5)]
        cap = len(order) if mode == "all" else max(3, int(dur / 6) + 1)
        if len(order) > cap:
            order = sorted(order, key=lambda i: -(segs[i].get("beauty") or 0))[:cap]
        for n_, i in enumerate(sorted(order)):
            if engine.STOP.is_set():
                break
            try:
                segs[i]["vlm"] = vlm_describe(vlm, kf_imgs[i])
            except Exception as e:
                log(f"[deep] VLM error: {str(e)[:120]}")
                break
            if progress and n_ % 3 == 0:
                progress({"sub": f"describing {n_ + 1}/{len(order)}"})
    res = {"v": DEEP_VERSION, "clip_id": clip["id"], "name": clip.get("name"), "path": clip["path"], "src": str(src),
           "fps": fps, "duration": dur, "n_shots": len(shots), "segments": segs,
           "secs": round(time.time() - t0, 1)}
    np.savez_compressed(dd / f"{clip['id']}.npz", clip=clip_emb,
                        face=np.array(face_emb, np.float32) if face_emb else np.zeros((0, 128), np.float32))
    (dd / f"{clip['id']}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


# ------------------------------------------------------------------ project
def _clips(out_dir):
    import plan
    data, _ = plan.load_project(out_dir)
    clips = data.get("clips", [])
    for c in clips:   # projects from older Chhanni versions have no clip id
        if not c.get("id"):
            c["id"] = engine.cache_name(c["path"])[:-4]
        c.setdefault("name", Path(c["path"]).name)
    return clips


def mark_current_failed(out_dir):
    dd = Path(out_dir) / "deep"
    cur = dd / "_current.json"
    if not cur.exists():
        return None
    c = json.loads(cur.read_text(encoding="utf-8"))
    fp = dd / "_failed.json"
    failed = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    failed[c["id"]] = {"name": c.get("name"), "why": "worker crashed", "t": time.time()}
    fp.write_text(json.dumps(failed, indent=1), encoding="utf-8")
    cur.unlink()
    return c.get("name")


def status(out_dir):
    out_dir = Path(out_dir)
    dd = out_dir / "deep"
    try:
        clips = _clips(out_dir)
    except Exception:
        clips = []
    done = {p.stem for p in dd.glob("*.json") if not p.name.startswith("_")} if dd.exists() else set()
    fp = dd / "_failed.json"
    failed = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    return {"clips": len(clips), "done": len([c for c in clips if c["id"] in done]), "failed": failed,
            "segments": None}


def run_project(out_dir, cfg, log=log, progress=None, stop=None):
    out_dir = Path(out_dir)
    dd = out_dir / "deep"
    dd.mkdir(exist_ok=True)
    clips = _clips(out_dir)
    fp = dd / "_failed.json"
    failed = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    todo = [c for c in clips if not (dd / f"{c['id']}.json").exists() and c["id"] not in failed]
    # talking / selected clips first - they matter most for the edit
    todo.sort(key=lambda c: (c.get("type") != "A", not c.get("selects"), c.get("path")))
    M = Models(cfg)
    prog = progress or (lambda d: None)
    total_sec = sum(float(c.get("duration") or 0) for c in todo) or 1
    done_sec, t_start = 0.0, time.time()
    log(f"[deep] {len(todo)} clips to analyse ({len(clips) - len(todo)} already done, {len(failed)} skipped)")
    for n, c in enumerate(todo):
        if (stop and stop.is_set()) or engine.STOP.is_set():
            return False
        (dd / "_current.json").write_text(json.dumps({"id": c["id"], "name": c.get("name")}), encoding="utf-8")
        el = time.time() - t_start
        eta = (el / done_sec * (total_sec - done_sec)) if done_sec > 0 else None
        prog({"phase": "deep", "done": n, "total": len(todo), "current": [c.get("name")], "eta": eta,
              "pct": round(100 * done_sec / total_sec, 1)})
        try:
            r = analyze_clip(c, out_dir, M, progress=lambda d: prog(dict({"phase": "deep", "done": n,
                                                                          "total": len(todo),
                                                                          "current": [c.get("name")]}, **d)))
            if r is None:
                return False
            vl = sum(1 for s in r["segments"] if s.get("vlm"))
            log(f"[deep] {c.get('name')}: {r['n_shots']} shots, {len(r['segments'])} segments"
                f"{f', {vl} described' if vl else ''} ({r['secs']}s)")
        except Exception as e:
            log(f"[deep] {c.get('name')} failed: {str(e)[:200]}")
            failed[c["id"]] = {"name": c.get("name"), "why": str(e)[:200], "t": time.time()}
            fp.write_text(json.dumps(failed, indent=1), encoding="utf-8")
        finally:
            try:
                (dd / "_current.json").unlink()
            except Exception:
                pass
        done_sec += float(c.get("duration") or 0)
    prog({"phase": "deep", "done": len(todo), "total": len(todo), "pct": 100})
    return True


def load_all(out_dir):
    """[(clip_json, npz)] for every analysed clip."""
    dd = Path(out_dir) / "deep"
    res = []
    for p in sorted(dd.glob("*.json")) if dd.exists() else []:
        if p.name.startswith("_"):
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            z = np.load(p.with_suffix(".npz"))
            res.append((j, {"clip": z["clip"], "face": z["face"]}))
        except Exception:
            continue
    return res
