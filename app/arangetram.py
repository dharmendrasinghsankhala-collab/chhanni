"""
Two-camera classical dance programme editor (Bharatanatyam Arangetram).
  analyse : audio sync of B-cam clips to the continuous A-cam, programme sections (dance / speech / applause),
            onset + tempo features, per-camera visual tags (CLIP) and camera motion
  plan    : cut list for MASTER (whole programme) and PERFORMANCE (dance items only)
usage: python arangetram.py analyse|plan <config.json>
config: {"out": dir, "fps": 29.97, "a_cam": [[path, timeline_start_frame], ...], "b_cam": [path, ...], "b_tc": {path: "HH:MM:SS;FF"}}
"""
import json, math, os, subprocess, sys, time
from pathlib import Path
import numpy as np

CFG = json.load(open(sys.argv[2], encoding="utf-8"))
OUT = Path(CFG["out"]); OUT.mkdir(parents=True, exist_ok=True)
FPS = 30000 / 1001
SR = 16000
HOP = 160                   # 10 ms
NOWIN = 0x08000000


def log(*a):
    with open(OUT / "log.txt", "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + " ".join(map(str, a)) + "\n")


def status(**k):
    (OUT / "status.json").write_text(json.dumps(dict(k, t=time.time())), encoding="utf-8")


def audio(path, ch=None):
    af = ["-ac", "1"] if ch is None else ["-af", f"pan=mono|c0=c{ch}"]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", *af, "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, creationflags=NOWIN).stdout
    return np.frombuffer(raw, np.float32)


def feats(y):
    """10 ms frames: rms, full-band spectral flux, low-band flux (mridangam / bass), mid (voice) energy."""
    n = len(y) // HOP - 3
    win = np.hanning(512).astype(np.float32)
    fq = np.fft.rfftfreq(512, 1 / SR)
    lo, mid = fq < 200, (fq > 300) & (fq < 3000)
    rms = np.zeros(n, np.float32); fl = np.zeros(n, np.float32); fll = np.zeros(n, np.float32); me = np.zeros(n, np.float32)
    prev = None
    for s in range(0, n, 20000):
        e = min(n, s + 20000)
        idx = np.arange(512)[None, :] + HOP * np.arange(s, e)[:, None]
        idx = np.minimum(idx, len(y) - 1)
        fr = y[idx]
        S = np.log1p(10 * np.abs(np.fft.rfft(fr * win, axis=1))).astype(np.float32)
        rms[s:e] = np.sqrt((fr ** 2).mean(1))
        if prev is not None:
            S0 = np.vstack([prev[None], S[:-1]])
        else:
            S0 = np.vstack([S[:1], S[:-1]])
        d = np.maximum(0, S - S0)
        fl[s:e] = d.sum(1); fll[s:e] = d[:, lo].sum(1); me[s:e] = S[:, mid].mean(1)
        prev = S[-1]
    return {"rms": rms, "flux": fl, "lflux": fll, "mid": me}


def norm_flux(f):
    f = f - np.convolve(f, np.ones(51) / 51, "same")
    f = np.maximum(f, 0)
    return (f / (np.percentile(f, 99) + 1e-6)).astype(np.float32)


def xcorr_best(ref, q, lo=None, hi=None):
    """offset (frames, 10ms) of q inside ref maximising correlation; returns (lag, z-score)."""
    if lo is None:
        lo, hi = 0, len(ref) - len(q)
    lo = max(0, lo); hi = min(len(ref) - len(q), hi)
    if hi <= lo:
        return None, 0
    seg = ref[lo:hi + len(q)]
    n = 1 << int(np.ceil(np.log2(len(seg) + len(q))))
    c = np.fft.irfft(np.fft.rfft(seg, n) * np.conj(np.fft.rfft(q, n)), n)[:hi - lo + 1]
    k = int(np.argmax(c))
    z = (c[k] - c.mean()) / (c.std() + 1e-9)
    return lo + k, float(z)


def tc_sec(tc):
    h, m, s, f = [int(x) for x in tc.replace(";", ":").split(":")]
    return h * 3600 + m * 60 + s + f / FPS


# ------------------------------------------------------------------ analyse
def analyse():
    t0 = time.time()
    # pick the A-cam audio channel with the most signal
    a0 = CFG["a_cam"][-1][0]
    lv = []
    for ch in range(4):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1200", "-t", "120", "-i", a0, "-vn", "-af",
                              f"pan=mono|c0=c{ch}", "-ar", "8000", "-f", "f32le", "-"], capture_output=True,
                             creationflags=NOWIN).stdout
        y = np.frombuffer(raw, np.float32)
        lv.append(float(np.sqrt((y ** 2).mean())) if len(y) else 0)
    ach = int(np.argmax(lv))
    log("A-cam channel levels", [round(20 * math.log10(v + 1e-9), 1) for v in lv], "-> using ch", ach)
    # continuous A-cam audio on the timeline clock
    parts, total = [], 0
    for path, start in CFG["a_cam"]:
        y = audio(path, ach)
        pad = int(round(start / FPS * SR)) - total
        if pad > 0:
            parts.append(np.zeros(pad, np.float32)); total += pad
        parts.append(y); total += len(y)
    A = np.concatenate(parts); del parts
    log("A audio", round(len(A) / SR / 60, 1), "min")
    status(stage="features")
    FA = feats(A)
    ref = norm_flux(FA["flux"])
    np.savez_compressed(OUT / "afeat.npz", **FA)
    # ---- sync B-cam clips
    status(stage="sync")
    sync = {}
    clips = sorted(CFG["b_cam"], key=lambda p: tc_sec(CFG["b_tc"][p]))
    feats_b = {}
    for p in clips:
        yb = audio(p)
        if len(yb) < SR * 3:
            continue
        feats_b[p] = norm_flux(feats(yb)["flux"])
    # long clips first, full search -> clock offset
    longest = sorted(feats_b, key=lambda p: -len(feats_b[p]))[:4]
    offs = []
    for p in longest:
        lag, z = xcorr_best(ref, feats_b[p])
        if lag is not None:
            offs.append((lag / 100.0 - tc_sec(CFG["b_tc"][p]), z, p))
            log("full search", Path(p).name, "tl", round(lag / 100, 2), "z", round(z, 1))
    good = [o for o in offs if o[1] > 8]
    clock = float(np.median([o[0] for o in good])) if good else None
    log("clock offset (tl - B timecode)", clock)
    for p, q in feats_b.items():
        if clock is not None:
            pred = int((tc_sec(CFG["b_tc"][p]) + clock) * 100)
            lag, z = xcorr_best(ref, q, pred - 3000, pred + 3000)
        else:
            lag, z = xcorr_best(ref, q)
        if lag is None:
            continue
        if z < 6 and clock is not None:      # weak audio -> trust the camera clock
            lag, how = int((tc_sec(CFG["b_tc"][p]) + clock) * 100), "timecode"
        else:
            how = "audio"
        sync[p] = {"tl_frame": round(lag / 100 * FPS, 2), "z": round(z, 1), "how": how, "dur": len(q) / 100}
        log("sync", Path(p).name, round(lag / 100, 2), "s", how, "z", round(z, 1))
    json.dump(sync, open(OUT / "sync.json", "w"), indent=1)
    # ---- sound classes (CLAP) every 4 s
    status(stage="sound classes")
    import torch
    from transformers import ClapModel, ClapProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    m = ClapModel.from_pretrained("laion/clap-htsat-unfused").to(dev).eval()
    pr = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
    LAB = [("dance_music", "Indian classical Carnatic music with mridangam drum, singing and violin for a dance"),
           ("nritta", "fast rhythmic Indian classical dance music with ankle bells and drum syllables"),
           ("abhinaya", "slow expressive Carnatic vocal song with soft accompaniment"),
           ("speech", "a person speaking into a microphone in a hall"),
           ("applause", "audience applause and clapping"),
           ("crowd", "people chatting, background noise in an auditorium"),
           ("silence", "silence, quiet room"),
           ("bells", "bells ringing, prayer chants")]

    def emb(o):
        return o if hasattr(o, "norm") else (getattr(o, "pooler_output", None) if getattr(o, "pooler_output", None) is not None else o[0])
    with torch.no_grad():
        t = pr(text=[x[1] for x in LAB], return_tensors="pt", padding=True)
        te = emb(m.get_text_features(**{k: v.to(dev) for k, v in t.items()}))
        te = (te / te.norm(dim=-1, keepdim=True)).float().cpu().numpy()
    W = 4.0
    nwin = int(len(A) / SR / W)
    P = np.zeros((nwin, len(LAB)), np.float32)
    for i in range(0, nwin, 24):
        chunk = []
        for k in range(i, min(nwin, i + 24)):
            seg = A[int(k * W * SR):int((k + 1) * W * SR)]
            chunk.append(np.interp(np.arange(0, len(seg), 1 / 3.0), np.arange(len(seg)), seg).astype(np.float32))
        try:
            inp = pr(audio=chunk, sampling_rate=48000, return_tensors="pt")
        except TypeError:
            inp = pr(audios=chunk, sampling_rate=48000, return_tensors="pt")
        with torch.no_grad():
            e = emb(m.get_audio_features(**{k: v.to(dev) for k, v in inp.items()}))
            e = (e / e.norm(dim=-1, keepdim=True)).float().cpu().numpy()
        s = 30 * (e @ te.T); s = np.exp(s - s.max(1, keepdims=True)); P[i:i + len(chunk)] = s / s.sum(1, keepdims=True)
        if i % 240 == 0:
            status(stage="sound classes", done=i, total=nwin)
    np.savez_compressed(OUT / "sound.npz", P=P, labels=np.array([x[0] for x in LAB]), win=W)
    del m
    torch.cuda.empty_cache()
    # ---- visual tags per camera (keyframes ~ every 2 s)
    status(stage="visual")
    import open_clip
    from PIL import Image
    cm, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k", device=dev)
    tok = open_clip.get_tokenizer("ViT-B-32")
    VL = [("dancer_full", "a classical Indian dancer full body on stage"),
          ("dancer_close", "a close-up of a classical dancer's face and upper body"),
          ("musicians", "Indian classical musicians sitting on stage playing"),
          ("speaker", "a woman speaking at a podium with a microphone"),
          ("felicitation", "people on stage giving a memento, shawl or bouquet, felicitation ceremony"),
          ("audience", "audience sitting in an auditorium"),
          ("stage_empty", "an empty stage with a screen"),
          ("blocked", "a person standing very close in front of the camera blocking the view"),
          ("blur", "a blurry motion-blurred frame while the camera is moving")]
    with torch.no_grad():
        tv = cm.encode_text(tok([x[1] for x in VL]).to(dev)).float()
        tv = (tv / tv.norm(dim=-1, keepdim=True)).cpu().numpy()

    def vis(path, step=2.0):
        W2, H2 = 384, 216
        cmd = ["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-hwaccel", "auto", "-i", str(path), "-an", "-vf",
               f"fps={1 / step},scale={W2}:{H2}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NOWIN)
        import cv2
        ims, mot, sh, prev = [], [], [], None
        while True:
            b = p.stdout.read(W2 * H2 * 3)
            if len(b) < W2 * H2 * 3:
                break
            im = np.frombuffer(b, np.uint8).reshape(H2, W2, 3)
            g = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY).astype(np.float32)
            sh.append(float(cv2.Laplacian(g, cv2.CV_32F).var()))
            if prev is not None:
                (dx, dy), _ = cv2.phaseCorrelate(prev, g)
                mot.append(float(math.hypot(dx, dy)))
            else:
                mot.append(0.0)
            prev = g
            ims.append(pre(Image.fromarray(im)))
        p.wait()
        probs = []
        with torch.no_grad():
            for i in range(0, len(ims), 64):
                e = cm.encode_image(torch.stack(ims[i:i + 64]).to(dev)).float()
                e = (e / e.norm(dim=-1, keepdim=True)).cpu().numpy()
                s = 100 * (e @ tv.T); s = np.exp(s - s.max(1, keepdims=True)); probs.append(s / s.sum(1, keepdims=True))
        return {"t": (np.arange(len(ims)) * step).tolist(), "p": np.concatenate(probs).round(3).tolist() if probs else [],
                "motion": np.round(mot, 2).tolist(), "sharp": np.round(sh, 1).tolist()}
    V = {"labels": [x[0] for x in VL], "a": {}, "b": {}}
    for path, start in CFG["a_cam"]:
        V["a"][path] = dict(vis(path), tl0=start)
        log("visual A", Path(path).name)
    for k, p in enumerate(sync):
        V["b"][p] = dict(vis(p), tl0=sync[p]["tl_frame"])
        status(stage="visual B", done=k, total=len(sync))
    json.dump(V, open(OUT / "visual.json", "w"))
    log("analyse done in", round(time.time() - t0), "s")
    status(stage="analysed")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "analyse":
            analyse()
    except Exception:
        import traceback
        log("ERROR", traceback.format_exc())
        status(stage="error", err=traceback.format_exc()[-800:])
