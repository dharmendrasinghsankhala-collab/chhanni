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


# ------------------------------------------------------------------ plan
def _runs(mask, min_len, max_gap):
    """contiguous True runs (indices) allowing gaps <= max_gap, keep runs >= min_len."""
    runs, s, last = [], None, None
    for i, v in enumerate(mask):
        if v:
            if s is None:
                s = i
            elif i - last - 1 > max_gap:
                runs.append((s, last + 1)); s = i
            last = i
    if s is not None:
        runs.append((s, last + 1))
    return [(a, b) for a, b in runs if b - a >= min_len]


def _beats(flux, lo_bpm=55, hi_bpm=180):
    """tempo + DP beat tracking on a 100 Hz onset curve. returns beat frame indices (10 ms)."""
    f = flux - flux.mean()
    ac = np.correlate(f[:min(len(f), 6000)], f[:min(len(f), 6000)], "full")[min(len(f), 6000) - 1:]
    lags = np.arange(len(ac))
    bpm = 6000 / np.maximum(lags, 1)
    ok = (bpm >= lo_bpm) & (bpm <= hi_bpm)
    if not ok.any():
        return np.array([], int), 0
    w = np.exp(-0.5 * (np.log2(bpm / 90) / 1.2) ** 2)
    period = int(lags[ok][np.argmax((ac * w)[ok])])
    score = flux.astype(np.float64).copy(); back = np.full(len(flux), -1)
    for t in range(len(flux)):
        lo, hi = int(t - 1.6 * period), int(t - 0.6 * period)
        if hi <= 0:
            continue
        lo = max(lo, 0)
        prev = np.arange(lo, hi)
        pen = -60 * np.log((t - prev) / period) ** 2
        k = int(np.argmax(score[prev] + pen)); score[t] = flux[t] + score[prev][k] + pen[k]; back[t] = prev[k]
    t = int(np.argmax(score[-period:]) + len(score) - period)
    out = []
    while t >= 0:
        out.append(t); t = back[t]
    return np.array(out[::-1]), 6000.0 / period


def plan():
    FA = dict(np.load(OUT / "afeat.npz"))
    S = np.load(OUT / "sound.npz"); P = S["P"]; L = list(S["labels"]); W = float(S["win"])
    sync = json.load(open(OUT / "sync.json"))
    V = json.load(open(OUT / "visual.json"))
    VL = V["labels"]
    n = len(P)
    music = P[:, L.index("dance_music")] + P[:, L.index("nritta")] + P[:, L.index("abhinaya")]
    speech = P[:, L.index("speech")]
    clap = P[:, L.index("applause")]
    sm = lambda x: np.convolve(x, np.ones(3) / 3, "same")
    music, speech, clap = sm(music), sm(speech), sm(clap)
    rms_w = np.array([FA["rms"][int(i * W * 100):int((i + 1) * W * 100)].mean() for i in range(n)])
    loud = rms_w > 0.25 * np.median(rms_w)
    is_dance = (music > 0.55) & loud
    is_speech = (speech > 0.45) & ~is_dance & loud
    dance_items = _runs(is_dance, int(75 / W), int(14 / W))
    speech_runs = _runs(is_speech | (clap > 0.4), int(15 / W), int(10 / W))
    # remove speech runs inside dance items
    speech_runs = [r for r in speech_runs if not any(a <= r[0] and r[1] <= b for a, b in dance_items)]
    sec = lambda i: i * W
    # ---- camera helpers (seconds on the timeline)
    def vis_at(cam_entries, t):
        """visual sample (probs, motion, sharp) nearest to timeline second t for the given camera list."""
        best = None
        for path, e in cam_entries:
            t0 = e["tl0"] / FPS
            k = int(round((t - t0) / 2.0))
            if 0 <= k < len(e["p"]):
                best = (path, e, k); break
        return best

    A = [(p, e) for p, e in V["a"].items()]
    B = [(p, e) for p, e in V["b"].items() if p in sync]
    bspan = {p: (sync[p]["tl_frame"] / FPS, sync[p]["tl_frame"] / FPS + sync[p]["dur"]) for p, _ in B}

    def b_clip_for(t0, t1):
        for p, (a, b) in bspan.items():
            if a + 0.3 <= t0 and t1 <= b - 0.3:
                return p
        return None

    def q_b(t0, t1):
        p = b_clip_for(t0, t1)
        if not p:
            return None, -9
        e = V["b"][p]
        ks = [k for k in range(len(e["p"])) if t0 <= e["tl0"] / FPS + 2 * k <= t1]
        if not ks:
            return p, 0
        pr = np.array([e["p"][k] for k in ks]); mo = np.array([e["motion"][k] for k in ks])
        dancer = pr[:, VL.index("dancer_full")] + pr[:, VL.index("dancer_close")]
        talk = pr[:, VL.index("speaker")] + pr[:, VL.index("felicitation")]
        bad = pr[:, VL.index("blur")] + pr[:, VL.index("blocked")] + pr[:, VL.index("stage_empty")]
        return p, float(dancer.mean() + 0.6 * talk.mean() - 1.2 * bad.mean() - 0.08 * max(0, mo.max() - 8))

    def q_a(t0, t1):
        hit = vis_at(A, (t0 + t1) / 2)
        if not hit:
            return -9
        _, e, k = hit
        pr = e["p"][k]
        return float(0.6 - 1.5 * pr[VL.index("blocked")])

    def acam(t):
        for p, st in CFG["a_cam"][::-1]:
            if t * FPS >= st:
                return p, st
        return CFG["a_cam"][0]

    beats_all = {}
    shots_by_item = []
    flux = norm_flux(FA["lflux"] + 0.5 * FA["flux"])
    for ii, (a, b) in enumerate(dance_items):
        ta, tb = sec(a), sec(b)
        fseg = flux[int(ta * 100):int(tb * 100)]
        beats, bpm = _beats(fseg)
        bt = ta + beats / 100.0
        strength = fseg[np.clip(beats, 0, len(fseg) - 1)] if len(beats) else np.array([])
        nr = P[a:b, L.index("nritta")]; ab = P[a:b, L.index("abhinaya")]
        shots, t, k = [], ta, 0
        while t < tb - 0.5:
            w = int((t - ta) / W)
            nritta = float(nr[min(w, len(nr) - 1)]) >= float(ab[min(w, len(ab) - 1)])
            lo, hi = (4.5, 8.5) if nritta else (8.0, 15.0)
            if k == 0:
                lo, hi = 6.0, 10.0
            cand = [(i, x) for i, x in enumerate(bt) if t + lo <= x <= t + hi]
            if t + hi >= tb - 3 or not cand:
                t1 = tb if t + hi >= tb - 3 else t + (lo + hi) / 2
            else:
                beat_period = 60.0 / max(bpm, 1)
                def sc(c):
                    i, x = c
                    phase = round((x - ta) / beat_period) % 8 == 0         # avartanam start (Adi tala)
                    return strength[i] + (0.6 if phase else 0) - 0.02 * abs(x - t - (lo + hi) / 2)
                t1 = max(cand, key=sc)[1]
            shots.append([round(t, 3), round(t1, 3), nritta]); t = t1; k += 1
        # camera choice: close (B) carries the dance, wide (A) opens / closes the item and breathes in regularly
        out, since_a = [], 0.0
        for j, (t0, t1, nritta) in enumerate(shots):
            pb, qb = q_b(t0, t1); qa = q_a(t0, t1)
            b_ok, a_ok = bool(pb) and qb > 0.25, qa > 0
            limit = 14.0 if nritta else 32.0
            if (j == 0 or j == len(shots) - 1) and a_ok:
                cam = "A"
            elif not b_ok:
                cam = "A"
            elif since_a >= limit and a_ok and (t1 - t0) <= 12:
                cam = "A"
            else:
                cam = "B"
            since_a = 0.0 if cam == "A" else since_a + (t1 - t0)
            out.append({"t0": t0, "t1": t1, "cam": cam, "b": pb if cam == "B" else None,
                        "kind": "nritta" if nritta else "abhinaya"})
        # merge consecutive same-camera shots
        merged = []
        for s_ in out:
            if merged and merged[-1]["cam"] == s_["cam"] and merged[-1]["b"] == s_["b"]:
                merged[-1]["t1"] = s_["t1"]
            else:
                merged.append(dict(s_))
        shots_by_item.append({"item": ii + 1, "t0": ta, "t1": tb, "bpm": round(bpm, 1), "shots": merged})
        log(f"item {ii + 1}: {round((tb - ta) / 60, 1)} min, {round(bpm)} bpm, {len(merged)} shots")
    # ---- speech / felicitation: few calm cuts at pauses
    talk_items = []
    for a, b in speech_runs:
        ta, tb = sec(a), sec(b)
        r = FA["rms"][int(ta * 100):int(tb * 100)]
        quiet = np.convolve(r, np.ones(40) / 40, "same")
        shots, t = [], ta
        while t < tb - 1:
            lo, hi = t + 20, t + 40
            if hi >= tb - 8:
                t1 = tb
            else:
                seg = quiet[int((lo - ta) * 100):int((hi - ta) * 100)]
                t1 = lo + float(np.argmin(seg)) / 100 if len(seg) else hi
            pb, qb = q_b(t, t1)
            cam = "B" if pb and qb > 0.15 else "A"
            if shots and shots[-1]["cam"] == cam and shots[-1]["b"] == (pb if cam == "B" else None):
                shots[-1]["t1"] = t1
            else:
                shots.append({"t0": round(t, 3), "t1": round(t1, 3), "cam": cam, "b": pb if cam == "B" else None,
                              "kind": "talk"})
            t = t1
        talk_items.append({"t0": ta, "t1": tb, "shots": shots})
    # ---- master: everything meaningful in programme order, dead gaps > 40 s compressed to 3 s
    blocks = sorted([("dance", d["t0"], d["t1"], d["shots"], d["item"]) for d in shots_by_item] +
                    [("talk", s_["t0"], s_["t1"], s_["shots"], None) for s_ in talk_items], key=lambda x: x[1])
    master, cursor = [], None
    for kind, t0, t1, shots, item in blocks:
        pre, post = (6.0, 8.0) if kind == "dance" else (2.0, 3.0)
        a0, a1 = max(0, t0 - pre), t1 + post
        if cursor is not None and a0 < cursor:
            a0 = cursor
        if cursor is not None and a0 - cursor <= 40:          # small gap: keep it (wide)
            if a0 > cursor:
                master.append({"t0": cursor, "t1": a0, "cam": "A", "b": None, "kind": "between"})
        if a0 < t0:
            master.append({"t0": a0, "t1": t0, "cam": "A", "b": None, "kind": "lead"})
        blk = [dict(s_, item=item) for s_ in shots if s_["t1"] > a0]
        if blk and blk[0]["t0"] < a0:
            blk[0]["t0"] = a0
        master += blk
        master.append({"t0": t1, "t1": a1, "cam": "A", "b": None, "kind": "tail"})
        cursor = a1
    perf = []
    for d in shots_by_item:
        a0, a1 = max(0, d["t0"] - 3.0), d["t1"] + 5.0
        seq = [{"t0": a0, "t1": d["t0"], "cam": "A", "b": None, "kind": "lead"}] + \
              [dict(s_, item=d["item"]) for s_ in d["shots"]] + \
              [{"t0": d["t1"], "t1": a1, "cam": "A", "b": None, "kind": "tail"}]
        perf += seq
    res = {"items": [{k: v for k, v in d.items() if k != "shots"} for d in shots_by_item],
           "talks": [{"t0": s_["t0"], "t1": s_["t1"]} for s_ in talk_items], "master": master, "perf": perf,
           "a_cam": CFG["a_cam"], "sync": sync}
    json.dump(res, open(OUT / "plan.json", "w"), indent=1)
    nb = sum(1 for s_ in master if s_["cam"] == "B")
    log(f"plan: {len(dance_items)} dance items, {len(talk_items)} speech/felicitation blocks, "
        f"master {len(master)} shots ({nb} close), perf {len(perf)} shots")
    status(stage="planned")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "analyse":
            analyse()
            plan()
        elif sys.argv[1] == "plan":
            plan()
    except Exception:
        import traceback
        log("ERROR", traceback.format_exc())
        status(stage="error", err=traceback.format_exc()[-800:])
