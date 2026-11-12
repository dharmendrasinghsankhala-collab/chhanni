"""
Chhanni Photo Cull - Bharatanatyam / dance event edition.
Non-destructive: never moves / edits / deletes the camera files.
Stage 1  technical: sharpness (subject + global), exposure, faces, CLIP embedding + zero-shot category, EXIF time
Stage 2  bursts: same moment (time + visual similarity) -> keep the best frame of each burst
Stage 3  Qwen3-VL (local Ollama) on burst winners: category, pose, peak moment, full body / limbs cut,
         eyes, expression, composition, hero score, dancer box
Stage 4  ratings + colour labels + keywords + crop/straighten -> XMP sidecars (Lightroom / Bridge / Capture One),
         hard-linked select folders, report.json
usage: python cull.py <photo_folder> [stage]
"""
import base64, json, math, os, sys, time, urllib.request
from pathlib import Path
import numpy as np
import cv2

SRC = Path(sys.argv[1])
STAGE = sys.argv[2] if len(sys.argv) > 2 else "all"
OUT = SRC.parent / "_Chhanni Cull"
OUT.mkdir(exist_ok=True)
LOG = OUT / "log.txt"
YUNET = Path(os.path.expandvars(r"%LOCALAPPDATA%\Chhanni\app\app\models\face_detection_yunet_2023mar.onnx"))
OLLAMA = "http://127.0.0.1:11434"
VLM = "qwen3-vl:8b-instruct"


def log(*a):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + " ".join(map(str, a)) + "\n")


def status(**k):
    (OUT / "status.json").write_text(json.dumps(dict(k, t=time.time())), encoding="utf-8")


CATS = [
    ("solo_full", "a classical Indian Bharatanatyam dancer performing a full body dance pose on stage"),
    ("araimandi", "a Bharatanatyam dancer in araimandi, half-sitting with knees bent outward and arms stretched"),
    ("jump", "a dancer jumping in the air on stage"),
    ("abhinaya", "a close-up of a Bharatanatyam dancer's face with an expressive emotion, eyes and eyebrows"),
    ("mudra", "a close-up of a classical dancer's hands showing a hand gesture mudra"),
    ("feet", "a close-up of dancing feet with ankle bells and red alta"),
    ("namaskaram", "a dancer with palms joined in prayer, namaskaram"),
    ("pushpanjali", "a dancer offering flowers on stage"),
    ("guru", "a dancer touching the feet of her teacher or receiving blessings"),
    ("musicians", "Indian classical musicians playing on stage, orchestra"),
    ("audience", "the audience sitting in an auditorium"),
    ("ceremony", "a felicitation ceremony on stage with people giving a memento or bouquet"),
    ("portrait", "a posed portrait photo of a dancer in costume looking at the camera"),
    ("group", "a group photo of several people posing together"),
    ("stage_wide", "a wide shot of the whole stage with lights and decoration"),
    ("details", "details of jewellery, costume, decoration or a lamp"),
    ("speech", "a person speaking at a microphone on stage"),
    ("junk", "a blurry accidental photo of the floor or ceiling"),
]
AES = (["a stunning professional dance photograph, perfect moment, dramatic stage lighting, sharp",
        "an award winning performance photograph with graceful lines and beautiful composition"],
       ["a blurry, badly timed amateur snapshot", "an out of focus, poorly framed photo"])


def exif_info(p):
    from PIL import Image
    try:
        im = Image.open(p)
        ex = im.getexif()
        ie = ex.get_ifd(0x8769)
        t = ie.get(36867) or ex.get(306) or ""
        sub = ie.get(37521) or "0"
        ts = time.mktime(time.strptime(t, "%Y:%m:%d %H:%M:%S")) + float("0." + str(sub).strip()[:3] or 0) if t else 0
        return {"time": ts, "orient": int(ex.get(274) or 1), "shutter": float(ie.get(33434) or 0),
                "f": float(ie.get(33437) or 0), "iso": int(ie.get(34855) or 0), "focal": float(ie.get(37386) or 0),
                "size": list(im.size)}
    except Exception as e:
        return {"time": 0, "orient": 1, "err": str(e)[:80]}


def load_reduced(p, orient=None):
    # NB: cv2.imread applies the EXIF orientation itself -> image comes back upright
    return cv2.imread(str(p), cv2.IMREAD_REDUCED_COLOR_4)


# ------------------------------------------------------------------ stage 1
def stage1(files):
    import torch, open_clip
    from PIL import Image
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k", device=dev)
    tok = open_clip.get_tokenizer("ViT-B-32")
    with torch.no_grad():
        def T(txts):
            e = model.encode_text(tok(txts).to(dev)).float()
            return (e / e.norm(dim=-1, keepdim=True)).cpu().numpy()
        Tc = T([c[1] for c in CATS]); Tp = T(AES[0]); Tn = T(AES[1])
    det = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 50)
    cache = OUT / "stage1.jsonl"
    done = set()
    if cache.exists():
        keep = []
        for l in cache.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(l)
                if d.get("orient", 1) in (6, 8) and not d.get("v2"):
                    continue
                done.add(d["name"]); keep.append(l)
            except Exception:
                pass
        cache.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    embs = dict(np.load(OUT / "emb.npz")) if (OUT / "emb.npz").exists() else {}
    todo = [f for f in files if f.stem not in done]
    log(f"stage1: {len(todo)} photos to analyse ({len(done)} cached)")
    t0 = time.time()
    batch_imgs, batch_rows = [], []

    def flush():
        if not batch_imgs:
            return
        with torch.no_grad():
            x = torch.stack(batch_imgs).to(dev)
            e = model.encode_image(x).float()
            e = (e / e.norm(dim=-1, keepdim=True)).cpu().numpy()
        P = np.exp(100 * (e @ Tc.T - (e @ Tc.T).max(1, keepdims=True)))
        P /= P.sum(1, keepdims=True)
        aes = (e @ Tp.T).mean(1) - (e @ Tn.T).mean(1)
        with open(cache, "a", encoding="utf-8") as f:
            for r, row in enumerate(batch_rows):
                k = int(P[r].argmax())
                row["cat"] = CATS[k][0]
                row["cat_p"] = round(float(P[r, k]), 3)
                row["cat2"] = CATS[int(np.argsort(-P[r])[1])][0]
                row["aes"] = round(float(1 / (1 + math.exp(-aes[r] * 40))), 3)
                embs[row["name"]] = e[r].astype(np.float16)
                f.write(json.dumps(row) + "\n")
        batch_imgs.clear(); batch_rows.clear()

    for n, p in enumerate(todo):
        ex = exif_info(p)
        im = load_reduced(p, ex.get("orient", 1))
        if im is None:
            continue
        h, w = im.shape[:2]
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(g, cv2.CV_64F)
        # sharpness of the sharpest region (the subject) = 95th pct of local Laplacian energy
        e = cv2.blur(np.abs(lap), (48, 48))
        sharp_peak = float(np.percentile(e, 99.5))
        det.setInputSize((w, h))
        _, faces = det.detect(im)
        fl = []
        for fc in (faces if faces is not None else [])[:10]:
            x, y, fw, fh = [float(v) for v in fc[:4]]
            x0, y0, x1, y1 = int(max(0, x)), int(max(0, y)), int(min(w, x + fw)), int(min(h, y + fh))
            fs = float(cv2.Laplacian(g[y0:y1, x0:x1], cv2.CV_64F).var()) if x1 - x0 > 8 and y1 - y0 > 8 else 0.0
            fl.append({"box": [round(x / w, 4), round(y / h, 4), round(fw / w, 4), round(fh / h, 4)],
                       "score": round(float(fc[14]), 2), "sharp": round(fs, 1),
                       "luma": round(float(g[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 0, 1)})
        fl.sort(key=lambda d: -d["box"][3])
        hi = float((g > 250).mean()); lo = float((g < 5).mean())
        row = {"v2": 1, "name": p.stem, "jpg": p.name, "w": w * 4, "h": h * 4, **ex, "sharp_peak": round(sharp_peak, 2),
               "sharp_global": round(float(lap.var()), 1), "luma": round(float(g.mean()), 1), "clip_hi": round(hi, 4),
               "clip_lo": round(lo, 4), "faces": fl}
        rgb = cv2.cvtColor(cv2.resize(im, (int(w * 336 / max(w, h)), int(h * 336 / max(w, h)))), cv2.COLOR_BGR2RGB)
        batch_imgs.append(pre(Image.fromarray(rgb)))
        batch_rows.append(row)
        if len(batch_imgs) >= 32:
            flush()
            np.savez(OUT / "emb.npz", **embs)
        if n % 20 == 0:
            el = time.time() - t0
            status(stage="1 technical + content", done=n, total=len(todo),
                   eta=round(el / max(1, n) * (len(todo) - n)))
    flush()
    np.savez(OUT / "emb.npz", **embs)
    log(f"stage1 done in {time.time() - t0:.0f}s")


def load_stage1():
    rows = {}
    for l in (OUT / "stage1.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(l); rows[r["name"]] = r
        except Exception:
            pass
    return rows


# ------------------------------------------------------------------ stage 2
def stage2():
    rows = load_stage1()
    E = dict(np.load(OUT / "emb.npz"))
    names = sorted(rows, key=lambda n: (rows[n]["time"], n))
    bursts, cur = [], []
    for n in names:
        if cur:
            p = cur[-1]
            dt = rows[n]["time"] - rows[p]["time"]
            sim = float(E[n].astype(np.float32) @ E[p].astype(np.float32))
            if dt <= 2.5 and sim >= 0.90:
                cur.append(n); continue
        if cur:
            bursts.append(cur)
        cur = [n]
    if cur:
        bursts.append(cur)
    # technical score, normalised over the whole shoot
    sp = np.array([rows[n]["sharp_peak"] for n in names])
    ref = float(np.percentile(sp, 90)) + 1e-6
    for n in names:
        r = rows[n]
        s = min(1.0, r["sharp_peak"] / ref)
        f = r["faces"][0] if r["faces"] else None
        fsharp = min(1.0, f["sharp"] / (np.percentile([rows[m]["faces"][0]["sharp"] for m in names if rows[m]["faces"]] or [1], 80) + 1e-6)) if f else None
        expo = 1.0 - 3 * max(0, r["clip_hi"] - 0.01) - max(0, r["clip_lo"] - 0.35)
        if f and f["luma"]:
            expo -= max(0, (45 - f["luma"]) / 90) + max(0, (f["luma"] - 235) / 40)   # face too dark / blown
        r["tech"] = round(float(max(0.0, 0.55 * (fsharp if fsharp is not None else s) + 0.25 * s + 0.2 * max(0, expo))), 3)
        r["blur"] = bool((fsharp is not None and fsharp < 0.08 and s < 0.35) or s < 0.08)
    for bi, b in enumerate(bursts):
        best = max(b, key=lambda n: rows[n]["tech"] + 0.3 * rows[n]["aes"])
        for n in b:
            rows[n]["burst"] = bi
            rows[n]["burst_n"] = len(b)
            rows[n]["burst_best"] = (n == best)
    json.dump(rows, open(OUT / "stage2.json", "w", encoding="utf-8"))
    log(f"stage2: {len(names)} photos -> {len(bursts)} moments/bursts; "
        f"{sum(1 for n in names if rows[n]['blur'])} blurry")
    return rows


# ------------------------------------------------------------------ stage 3
PROMPT = """You are a senior photo editor selecting images from a Bharatanatyam Arangetram (debut dance recital).
Judge this photo strictly, like a professional dance photographer. Reply ONLY with JSON:
{"category": one of solo_dance|abhinaya_closeup|mudra_closeup|feet_closeup|namaskaram|pushpanjali|guru_blessing|musicians|audience|ceremony|portrait|group|stage_wide|details|speech|other,
 "pose": short name of the dance pose/moment (e.g. araimandi, muzhumandi, jump, nataraja, side stretch, tribhanga, alarippu, spin, walk, expression, none),
 "peak_moment": 0-10 (10 = pose at its fullest: limbs fully extended, clean lines, symmetry, not mid-transition),
 "full_body_in_frame": true/false (head, both hands and both feet fully visible),
 "limbs_cut": true/false (hand, foot or head cut by the frame edge),
 "eyes": "open"|"closed"|"half"|"not visible",
 "expression": 0-10 (bhava / emotion on the face),
 "sharp_on_dancer": 0-10,
 "lighting": 0-10,
 "composition": 0-10,
 "distractions": short text or "",
 "hero": 0-10 (would this be a cover / album hero image),
 "dancer_box": [x1,y1,x2,y2] main dancer's full body box in 0-1000 relative coordinates, or [] if none,
 "why": one short sentence}"""


def vlm(img_bgr):
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    body = {"model": VLM, "prompt": PROMPT, "images": [base64.b64encode(buf.tobytes()).decode()], "stream": False,
            "format": "json", "think": False, "keep_alive": "15m",
            "options": {"temperature": 0.1, "num_ctx": 6144, "num_predict": 400}}
    req = urllib.request.Request(OLLAMA + "/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        txt = json.loads(r.read()).get("response", "")
    a, b = txt.find("{"), txt.rfind("}")
    return json.loads(txt[a:b + 1])


def stage3(rows):
    cache = OUT / "vlm.jsonl"
    done = {}
    if cache.exists():
        for l in cache.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(l); done[d["name"]] = d
            except Exception:
                pass
    cand = [n for n, r in rows.items() if r["burst_best"] and not r["blur"] and r["cat"] != "junk"]
    # also judge the 2nd best of long bursts (peak moments often hide there)
    for b in {r["burst"] for r in rows.values() if r["burst_n"] >= 4}:
        mem = sorted([n for n, r in rows.items() if r["burst"] == b and not r["burst_best"] and not r["blur"]],
                     key=lambda n: -rows[n]["tech"])
        cand += mem[:1]
    DANCE = {"solo_full", "araimandi", "jump", "abhinaya", "mudra", "feet", "namaskaram", "pushpanjali", "portrait"}
    cand = sorted(set(cand), key=lambda n: (rows[n]["cat"] not in DANCE, -rows[n]["tech"]))
    todo = [n for n in cand if n not in done]
    log(f"stage3: {len(cand)} candidates for Qwen3-VL ({len(todo)} new)")
    t0 = time.time()
    for k, n in enumerate(todo):
        r = rows[n]
        im = load_reduced(SRC / r["jpg"], r.get("orient", 1))
        h, w = im.shape[:2]
        s = 896 / max(h, w)
        im = cv2.resize(im, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        d = None
        for attempt in range(2):
            try:
                d = vlm(im)
                break
            except Exception as e:
                log(f"vlm error {n} (try {attempt + 1}): {str(e)[:120]}")
                time.sleep(3)
        if d is None:
            continue
        d["name"] = n
        with open(cache, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        done[n] = d
        if k % 5 == 0:
            el = time.time() - t0
            status(stage="2 Qwen3-VL judging", done=k, total=len(todo), eta=round(el / max(1, k) * (len(todo) - k)))
    log(f"stage3 done in {time.time() - t0:.0f}s")
    return done


# ------------------------------------------------------------------ stage 4
def straighten_angle(img):
    """Small tilt from strong near-horizontal lines (stage floor, backdrop, wall edges)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    e = cv2.Canny(g, 60, 160)
    lines = cv2.HoughLinesP(e, 1, np.pi / 720, threshold=120, minLineLength=img.shape[1] // 4, maxLineGap=12)
    if lines is None:
        return 0.0
    angs, wts = [], []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(a) < 6:
            angs.append(a); wts.append(math.hypot(x2 - x1, y2 - y1))
    if len(angs) < 2:
        return 0.0
    a = float(np.average(angs, weights=wts))
    return a if 0.4 <= abs(a) <= 4 else 0.0


def crop_for(r, d, ratio):
    """Crop (normalised l,t,r,b) around the dancer, keeping full body + breathing room, same aspect ratio."""
    box = d.get("dancer_box") or []
    if len(box) != 4 or not d.get("full_body_in_frame"):
        return None
    x1, y1, x2, y2 = [v / 1000.0 for v in box]
    if x2 - x1 < 0.05 or y2 - y1 < 0.1:
        return None
    W, H = r["w"], r["h"]          # already upright dimensions
    bh = (y2 - y1)
    top, bot = y1 - 0.12 * bh, y2 + 0.07 * bh
    ch = min(1.0, bot - top)
    cw = ch * H * ratio / W
    if cw > 1.0:
        cw = 1.0; ch = cw * W / (ratio * H)
    if cw < (x2 - x1) * 1.15:
        return None
    cx = (x1 + x2) / 2
    l = min(max(0.0, cx - cw / 2), 1 - cw)
    t = min(max(0.0, top - (ch - (bot - top)) / 2), 1 - ch)
    if cw * ch > 0.88:          # not worth cropping
        return None
    return [round(l, 4), round(t, 4), round(l + cw, 4), round(t + ch, 4)]


def xmp(rating, label, keywords, crop=None, angle=0.0, desc=""):
    kw = "".join(f"<rdf:li>{k}</rdf:li>" for k in keywords)
    crs = ""
    if crop or angle:
        l, t, rr, b = crop or [0, 0, 1, 1]
        crs = (f' crs:HasCrop="True" crs:CropLeft="{l}" crs:CropTop="{t}" crs:CropRight="{rr}" crs:CropBottom="{b}"'
               f' crs:CropAngle="{round(-angle, 2)}" crs:CropConstrainToWarp="0" crs:AlreadyApplied="False"')
    return f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Chhanni Cull">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmp:Rating="{rating}" xmp:Label="{label}"{crs}>
   <dc:subject><rdf:Bag>{kw}</rdf:Bag></dc:subject>
   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{desc}</rdf:li></rdf:Alt></dc:description>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""


def stage4(rows, V):
    for n, r in rows.items():
        d = V.get(n)
        r["vlm"] = d
        if d:
            def g(k, dv=0):
                try:
                    return float(d.get(k, dv))
                except Exception:
                    return dv
            dance = d.get("category") in ("solo_dance", "abhinaya_closeup", "mudra_closeup", "feet_closeup",
                                          "namaskaram", "pushpanjali")
            sc = (0.30 * g("hero") + 0.18 * g("peak_moment") + 0.12 * g("expression") + 0.12 * g("composition")
                  + 0.10 * g("lighting") + 0.08 * g("sharp_on_dancer")) / 9.0 + 0.25 * r["tech"] + 0.1 * r["aes"]
            if d.get("eyes") in ("closed", "half"):
                sc -= 0.25
            if d.get("limbs_cut") and d.get("category") == "solo_dance":
                sc -= 0.2
            if not dance:
                sc -= 0.08
            r["score"] = round(sc, 3)
        else:
            r["score"] = None
    judged = sorted([n for n in rows if rows[n]["score"] is not None], key=lambda n: -rows[n]["score"])
    N = len(rows)
    n_hero = max(12, int(round(N * 0.035)))
    n_best = max(40, int(round(N * 0.12)))
    hero = set(judged[:n_hero]); best = set(judged[n_hero:n_hero + n_best])
    sel = OUT.parent / "_Chhanni Selects"
    report = {"total": N, "hero": [], "best": [], "counts": {}}
    t0 = time.time()
    for k, (n, r) in enumerate(sorted(rows.items())):
        d = r.get("vlm") or {}
        if n in hero:
            rating, label, tier = 5, "Red", "01 Hero"
        elif n in best:
            rating, label, tier = 4, "Green", "02 Best"
        elif r["blur"]:
            rating, label, tier = 1, "", None
        elif not r["burst_best"]:
            rating, label, tier = 2, "", None
        elif r["score"] is not None and r["score"] >= 0.45:
            rating, label, tier = 3, "Yellow", "03 Good"
        else:
            rating, label, tier = 2, "", None
        cat = d.get("category") or r["cat"]
        pose = (d.get("pose") or "").strip().lower()
        kws = ["Chhanni", f"cull/{tier or ('reject-blur' if r['blur'] else 'duplicate' if not r['burst_best'] else 'ok')}",
               f"category/{cat}"]
        if pose and pose != "none":
            kws.append(f"pose/{pose}")
        if d.get("eyes") in ("closed", "half"):
            kws.append("check/eyes-" + d["eyes"])
        if d.get("limbs_cut"):
            kws.append("check/limbs-cut")
        crop, ang = None, 0.0
        if rating >= 4:
            ratio = r["w"] / r["h"]
            if r.get("orient", 1) == 1:      # crop coords only for un-rotated frames (safe in every raw editor)
                im = load_reduced(SRC / r["jpg"], 1)
                ang = straighten_angle(im) if im is not None else 0.0
                crop = crop_for(r, d, ratio)
        r["xmp"] = {"rating": rating, "label": label, "crop": crop, "angle": round(ang, 2), "tier": tier}
        arw = SRC / (n + ".ARW")
        desc = (d.get("why") or "").replace("&", "and").replace("<", "").replace(">", "")
        x = xmp(rating, label, kws, crop, ang, desc)
        xp = SRC / (n + ".xmp")
        if not xp.exists() or "Chhanni Cull" in xp.read_text(encoding="utf-8", errors="ignore"):
            xp.write_text(x, encoding="utf-8")
        if tier:
            dests = [sel / tier]
            if tier in ("01 Hero", "02 Best") and cat in ("solo_dance",) and pose and pose != "none":
                dests.append(sel / "04 Poses" / pose.replace("/", "-")[:40])
            elif tier in ("01 Hero", "02 Best") and cat not in ("solo_dance",):
                dests.append(sel / "05 Moments" / cat)
            for dd in dests:
                dd.mkdir(parents=True, exist_ok=True)
                for srcf in (arw,):
                    if srcf.exists() and not (dd / srcf.name).exists():
                        try:
                            os.link(srcf, dd / srcf.name)
                        except Exception:
                            import shutil
                            shutil.copy2(srcf, dd / srcf.name)
                (dd / (n + ".xmp")).write_text(x, encoding="utf-8")
            if tier == "01 Hero":
                report["hero"].append(n)
            elif tier == "02 Best":
                report["best"].append(n)
        report["counts"][str(rating)] = report["counts"].get(str(rating), 0) + 1
        if k % 50 == 0:
            status(stage="3 writing ratings / folders", done=k, total=N, eta=None)
    # quick-share previews of heroes (camera JPG with the crop + straighten applied)
    pv = sel / "01 Hero" / "_preview_jpg"
    pv.mkdir(parents=True, exist_ok=True)
    for n in report["hero"]:
        r = rows[n]
        im = cv2.imread(str(SRC / r["jpg"]), cv2.IMREAD_REDUCED_COLOR_2)
        if im is None:
            continue
        h, w = im.shape[:2]
        a = r["xmp"]["angle"]
        if a:
            M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
            im = cv2.warpAffine(im, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            c = abs(math.sin(math.radians(a)))
            m = int(c * max(w, h) / 2) + 2
            im = im[m:h - m, m:w - m]
            h, w = im.shape[:2]
        if r["xmp"]["crop"]:
            l, t, rr, b = r["xmp"]["crop"]
            im = im[int(t * h):int(b * h), int(l * w):int(rr * w)]
        cv2.imwrite(str(pv / (n + ".jpg")), im, [cv2.IMWRITE_JPEG_QUALITY, 92])
    json.dump({"report": report, "rows": rows}, open(OUT / "report.json", "w", encoding="utf-8"), ensure_ascii=False)
    log(f"stage4: hero {len(report['hero'])}, best {len(report['best'])}, ratings {report['counts']} in {time.time() - t0:.0f}s")
    return report


if __name__ == "__main__":
    try:
        files = sorted(p for p in SRC.iterdir() if p.suffix.lower() == ".jpg")
        if STAGE in ("all", "1"):
            stage1(files)
        rows = stage2()
        if STAGE in ("all", "1", "3"):
            V = stage3(rows)
        else:
            V = {}
            for l in (OUT / "vlm.jsonl").read_text(encoding="utf-8").splitlines():
                d = json.loads(l); V[d["name"]] = d
        stage4(rows, V)
        status(stage="done", done=1, total=1)
        log("DONE")
    except Exception:
        import traceback
        log("ERROR " + traceback.format_exc())
        status(stage="error", err=traceback.format_exc()[-600:])
