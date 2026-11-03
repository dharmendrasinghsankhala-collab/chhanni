"""
Chhanni reel mode - talking-head / promo reels (9:16 or 16:9).

 - tight word-level cuts: silences above `pause` and filler words removed, never cutting mid-word
 - zoom punch-ins on jump cuts (100% <-> zoom%)
 - B-roll cover (none / some / more), hint-aware ([BASEMENT ...] directions)
 - captions from Whisper word timings (word-by-word or N-word chunks) + SRT export
 - hook title at the start, CTA end card, background music (ducked), optional FFmpeg audio enhance
Every option can be changed later by an instruction ("30 sec karo", "zoom hatao", "B-roll zyada" ...).
"""
import os
import subprocess
from pathlib import Path

import ai_audio
import script as S

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
FILLER_WORDS = {"um", "umm", "uh", "uhh", "hmm", "hm", "mm", "er", "erm", "ah", "aa", "a", "e", "eh", "am", "an",
                "अं", "उम", "हम्म", "आ", "ए"}

DEFAULTS = {
    # cleanup
    "remove_bad_takes": True, "drop_fillers": True, "remove_silences": True, "pause": 0.35, "drop_clipped": True,
    "stable_broll": True,
    # visual
    "broll": "some", "broll_sec": 2.8, "zoom": True, "zoom_level": 1.12, "color_balance": False, "smart_reframe": False,
    # text
    "hook": True, "hook_text": "", "captions": True, "caption_words": 3, "lower_third": False, "lower_third_text": "",
    "cta": False, "cta_text": "", "cta_sec": 3.0,
    # audio
    "enhance_audio": False, "music": "", "music_db": -20.0, "sfx": "", "sfx_db": -12.0,
    # structure / output
    "target_sec": 0, "auto_hook": False, "aspect": "9:16", "variations": True, "markers": True,
    # instruction overrides
    "remove_beats": [], "alt_take": {}, "first_beat": None}
ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
MOVING_OK = {"Static (tripod)", "Slider / gimbal move", "Smooth pan", "Smooth tilt / crane", "Drone / aerial",
             "Smooth handheld / gimbal"}


def _is_filler(w):
    t = ai_audio.norm_text(w)
    return not t or t in FILLER_WORDS or (len(t) <= 3 and set(t) <= set("aeimnuh"))


def tighten(words, start, end, pause=0.35, drop_fillers=True):
    """Split one take into spoken pieces at word boundaries."""
    ws = [w for w in words if w.get("w") and w["e"] > start + 0.02 and w["s"] < end - 0.02]
    if not ws:
        return [{"start": start, "end": end, "words": []}]
    pieces, cur, dropped = [], [], False
    for w in ws:
        if drop_fillers and _is_filler(w["w"]):
            dropped = True
            continue
        if cur and (w["s"] - cur[-1]["e"] > pause or dropped):
            pieces.append(cur)
            cur = []
        cur.append(w)
        dropped = False
    if cur:
        pieces.append(cur)
    out = []
    for p in pieces:
        s = max(start, p[0]["s"] - 0.06)
        e = min(end, p[-1]["e"] + 0.10)
        if e - s < 0.25:
            continue
        out.append({"start": round(s, 3), "end": round(e, 3), "words": p})
    # glue pieces that are separated by almost nothing (avoids 1-frame jump cuts)
    glued = []
    for p in out:
        if glued and p["start"] - glued[-1]["end"] < 0.08:
            glued[-1]["end"] = p["end"]
            glued[-1]["words"] += p["words"]
        else:
            glued.append(p)
    return glued


def _words_for(out_dir, clip_id, cache):
    if clip_id not in cache:
        tr = ai_audio.load_transcript(out_dir, clip_id)
        cache[clip_id] = [w for s in (tr or {}).get("segments", []) for w in s.get("words", []) if w.get("w")]
    return cache[clip_id]


def _captions(pieces_on_tl, n_words):
    """Caption chunks on timeline time, from the word timings of each spoken piece."""
    # convert to timeline times
    out = []
    for it in pieces_on_tl:
        ws = it.get("_words") or []
        off = it["rec"] - it["start"]
        chunk = []
        for w in ws:
            chunk.append(w)
            if len(chunk) >= n_words or w["w"].rstrip().endswith(("।", ".", "!", "?", ",")):
                out.append({"start": round(max(it["rec"], chunk[0]["s"] + off), 3),
                            "end": round(min(it["rec"] + it["end"] - it["start"], chunk[-1]["e"] + off), 3),
                            "text": " ".join(x["w"].strip() for x in chunk),
                            "words": [{"w": x["w"].strip(), "s": round(x["s"] + off, 3), "e": round(x["e"] + off, 3)} for x in chunk]})
                chunk = []
        if chunk:
            out.append({"start": round(max(it["rec"], chunk[0]["s"] + off), 3),
                        "end": round(min(it["rec"] + it["end"] - it["start"], chunk[-1]["e"] + off), 3),
                        "text": " ".join(x["w"].strip() for x in chunk),
                        "words": [{"w": x["w"].strip(), "s": round(x["s"] + off, 3), "e": round(x["e"] + off, 3)} for x in chunk]})
    # no overlaps, small minimum duration
    for i in range(len(out) - 1):
        out[i]["end"] = min(out[i]["end"], out[i + 1]["start"])
    return [c for c in out if c["end"] - c["start"] >= 0.12]


def srt(captions):
    def t(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{t(c['start'])} --> {t(c['end'])}\n{c['text']}\n" for i, c in enumerate(captions, 1))


def enhance_audio(out_dir, clip, ffmpeg):
    """Free 'Auphonic-like' chain: rumble cut, denoise, gentle compression, reel loudness (-14 LUFS)."""
    d = Path(out_dir) / "enhanced"
    d.mkdir(exist_ok=True)
    dst = d / f"{clip['id']}.wav"
    if dst.exists() and dst.stat().st_size > 1000:
        return str(dst)
    af = ("highpass=f=80,afftdn=nf=-25:tn=1,acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=2,"
          "loudnorm=I=-14:TP=-1.5:LRA=9")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", clip["path"], "-map", "0:a:0", "-vn",
           "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(dst)]
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    return str(dst) if r.returncode == 0 and dst.exists() else None


def _choose_takes(out_dir, clips, script_text, cfg, opts, concept):
    """-> rows: [{i, kind, text, hint, take|None, alts, missing}]"""
    if script_text.strip():
        if concept:
            story = S.story_from_concept(out_dir, clips, cfg, script_text, max(0.3, (opts.get("target_sec") or 45) / 60))
            mapped = S.resolve_refs(S.map_script(out_dir, story["beats"], clips, cfg), clips)
        else:
            mapped = S.map_script(out_dir, S.parse(script_text), clips, cfg)
        beats = mapped["beats"]
    else:
        beats = []
        for c in sorted([c for c in clips if c["type"] == "A" or any(s.get("text") for s in c["selects"])],
                        key=lambda c: (c.get("start_tc") or "", c["path"])):
            for s in c["selects"]:
                if s.get("text"):
                    beats.append({"kind": "dialogue", "text": s["text"], "ref": f"{c['id']}@{s['start']:.2f}-{s['end']:.2f}",
                                  "score": s["score"]})
        for i, b in enumerate(beats):
            b["i"] = i
        mapped = S.resolve_refs({"beats": beats, "has_transcripts": True}, clips)
        beats = mapped["beats"]
    import assemble as AS
    remove = set(int(x) for x in opts.get("remove_beats") or [])
    alt = {int(k): int(v) for k, v in (opts.get("alt_take") or {}).items()}
    rows, used = [], []
    for b in beats:
        if b["kind"] in ("section", "note"):
            if b["kind"] == "section":
                rows.append({"i": b["i"], "kind": "section", "text": b["text"]})
            continue
        if b["i"] in remove:
            rows.append({"i": b["i"], "kind": b["kind"], "text": b["text"], "removed": True})
            continue
        if b["kind"] != "dialogue":
            rows.append({"i": b["i"], "kind": "visual", "text": b["text"], "broll": b.get("broll") or [], "dur": b.get("dur"),
                         "title": b.get("title")})
            continue
        takes = b.get("takes") or []
        if opts.get("drop_clipped", True):
            takes = [t for t in takes if "audio clipping" not in (t.get("flags") or [])] or takes
        if opts.get("remove_bad_takes", True):
            takes = [t for t in takes if not {"retake", "false start"} & set(t.get("flags") or [])] or takes
        takes = AS._fresh(takes, used)
        if not takes or takes[0]["sim"] < AS.MIN_SIM:
            rows.append({"i": b["i"], "kind": "dialogue", "text": b["text"], "missing": True, "alts": takes[:4]})
            continue
        t = takes[min(alt.get(b["i"], 0), len(takes) - 1)]
        used.append(t)
        rows.append({"i": b["i"], "kind": "dialogue", "text": b["text"], "take": t, "alts": takes[:4], "hint": b.get("hint"),
                     "score": b.get("score", 0), "speaker": b.get("speaker")})
    if opts.get("auto_hook") and opts.get("first_beat") is None:
        # strongest opener: a short, confident line (question / exclamation) with a clean take
        def hookiness(r):
            t = r["take"]
            txt = (t.get("text") or r["text"]).strip()
            d = t["end"] - t["start"]
            return (txt.endswith(("?", "!", "?।", "!।")) * 0.4 + t["sim"] * 0.3 + t.get("quality", 0.8) * 0.2
                    + (0.2 if 1.0 <= d <= 4.5 else 0))
        cands = [r for r in rows if r.get("take")]
        if cands:
            opts = dict(opts, first_beat=max(cands, key=hookiness)["i"])
    fb = opts.get("first_beat")
    if fb is not None:
        k = next((j for j, r in enumerate(rows) if r["i"] == int(fb) and r.get("take")), None)
        if k is not None:
            rows.insert(0, rows.pop(k))
    return rows, mapped.get("has_transcripts", False)


def build(out_dir, clips, script_text, opts, ffmpeg, cfg, concept=False, name="Reel"):
    import assemble as AS
    o = dict(DEFAULTS)
    o.update({k: v for k, v in (opts or {}).items() if v is not None})
    rows, has_tr = _choose_takes(out_dir, clips, script_text, cfg, o, concept)
    fps = {c["id"]: c["fps"] for c in clips}
    byid = {c["id"]: c for c in clips}
    wcache = {}
    pool = S.broll_pool(clips)
    spoken = {r["take"]["clip"] for r in rows if r.get("take")}
    bpool = [p for p in pool if p["clip"] not in spoken] or pool
    if o["stable_broll"]:
        bpool = [p for p in bpool if (p.get("move") in MOVING_OK or not p.get("move")) and p["score"] >= 60] or bpool
    used_b = set()
    items, rec, last_clip, zoom_flip = [], 0.0, None, False
    dialogue_pieces = []
    # trim to target length (no script): keep best soundbites in story order
    if o["target_sec"] and not script_text.strip():
        budget = float(o["target_sec"]) - (o["cta_sec"] if o["cta_text"] else 0)
        ranked = sorted([r for r in rows if r.get("take")], key=lambda r: -r.get("score", 0))
        keep, tot = set(), 0.0
        for r in ranked:
            d = r["take"]["end"] - r["take"]["start"]
            if tot + d * 0.85 <= budget:
                keep.add(r["i"])
                tot += d * 0.85
        rows = [r for r in rows if not r.get("take") or r["i"] in keep]
    for r in rows:
        if r["kind"] == "section" or r.get("removed") or r.get("missing"):
            r["rec"] = round(rec, 2)
            continue
        if r["kind"] == "visual":
            L = r.get("dur") or 3.0
            q = r["text"] + (" sign board logo shop exterior" if r.get("title") else "")
            for p in (r.get("broll") or S.match_visual(q, bpool, out_dir, used_b))[:2]:
                if p["key"] in used_b:
                    continue
                seg = min(L, p["end"] - p["start"])
                if seg >= 1.2:
                    mid = (p["start"] + p["end"]) / 2
                    items.append(AS._item(p, mid - seg / 2, mid + seg / 2, rec, "V1", "v", r["i"], r["text"][:60], p.get("move")))
                    used_b.add(p["key"])
                    r["rec"] = round(rec, 2)
                    rec += seg
                    break
            last_clip = None
            continue
        t = r["take"]
        words = _words_for(out_dir, t["clip"], wcache)
        pieces = tighten(words, t["start"], t["end"], float(o["pause"]) if o["remove_silences"] else 9.0,
                         bool(o["drop_fillers"]))
        r["rec"] = round(rec, 2)
        r_start = rec
        for k, pc in enumerate(pieces):
            jump = last_clip == t["clip"]
            zoom_flip = (not zoom_flip) if jump else False
            it = AS._item(dict(t, fps=fps.get(t["clip"]), score=int(t["score"] * 100)), pc["start"], pc["end"], rec, "V1", "av",
                          r["i"], " ".join(w["w"] for w in pc["words"])[:120] or t["text"][:120], "dialogue")
            if o["zoom"] and zoom_flip:
                it["zoom"] = float(o["zoom_level"])
            it["_words"] = pc["words"]
            items.append(it)
            dialogue_pieces.append(it)
            rec += pc["end"] - pc["start"]
            last_clip = t["clip"]
        dur = rec - r_start
        hint = r.get("hint") or ""
        on_cam = bool(AS.ON_CAMERA.search(hint))
        want = o["broll"] == "more" and dur > 2.5 or (o["broll"] == "some" and ((hint and not on_cam and dur > 2.5) or dur > 6))
        if o["broll"] not in ("none", False) and want and not on_cam:
            lead = 0.6 if hint else 1.2
            items += AS._cover([q for q in bpool if q["key"] not in used_b], (hint + " " + r["text"]).strip(), r_start + lead,
                               dur - lead - 0.2, used_b, out_dir, t.get("group"), min_len=1.6, max_len=float(o["broll_sec"]))
    total = rec
    titles = []
    if o["cta"]:
        cand = S.match_visual("City sign board logo shop front exterior " + o["cta_text"], bpool, out_dir, used_b, k=3)
        if cand:
            p = cand[0]
            seg = min(float(o["cta_sec"]), p["end"] - p["start"])
            mid = (p["start"] + p["end"]) / 2
            items.append(AS._item(p, mid - seg / 2, mid + seg / 2, rec, "V1", "v", None, "CTA", "cta"))
            titles.append({"kind": "cta", "text": o["cta_text"] or "Visit us today", "rec": round(rec, 2), "dur": round(seg, 2)})
            total = rec + seg
    hook = (o["hook_text"] or "auto") if o["hook"] else ""
    if hook == "auto":
        first = next((r for r in rows if r.get("take")), None)
        hook = " ".join(((first["take"].get("text") or first["text"]) if first else "").split()[:7]) if first else ""
    if hook:
        titles.insert(0, {"kind": "hook", "text": hook, "rec": 0.0, "dur": round(min(3.0, max(1.5, total)), 2)})
    if o["lower_third"]:
        first = next((r for r in rows if r.get("take") and r.get("rec") is not None and (r.get("speaker") or "").lower()
                      not in ("", "vo") and not (r.get("speaker") or "").lower().startswith("staff")), None) \
            or next((r for r in rows if r.get("take") and r.get("rec") is not None), None)
        if first:
            txt = o["lower_third_text"] or (first.get("speaker") or "")
            if txt:
                titles.append({"kind": "lower_third", "text": txt, "rec": round(first["rec"] + 0.3, 2), "dur": 3.0})
    if o["sfx"] and os.path.exists(o["sfx"]):
        hits = sorted({round(it["rec"], 2) for it in items if it["track"] == "V2"} |
                      {round(it["rec"], 2) for it in items if it.get("zoom")})
        for h_ in hits[:40]:
            items.append({"path": o["sfx"], "name": os.path.basename(o["sfx"]), "start": 0.0, "end": 1.2, "rec": max(0, h_ - 0.15),
                          "fps": None, "track": "A3", "media": "a", "score": 0, "label": "sfx", "volume": float(o["sfx_db"])})
    if o["music"] and os.path.exists(o["music"]):
        items.append({"path": o["music"], "name": os.path.basename(o["music"]), "start": 0.0, "end": round(total, 3),
                      "rec": 0.0, "fps": None, "track": "A2", "media": "a", "score": 0, "label": "music",
                      "volume": float(o["music_db"])})
    captions = _captions(dialogue_pieces, max(1, int(o["caption_words"]))) if o["captions"] else []
    notes = []
    if o["enhance_audio"]:
        done = {}
        new = []
        for it in items:
            if it["media"] == "av" and it.get("clip") in byid:
                wav = done.get(it["clip"]) or enhance_audio(out_dir, byid[it["clip"]], ffmpeg)
                done[it["clip"]] = wav
                if wav:
                    v = dict(it, media="v")
                    a = dict(it, media="a", path=wav, name=os.path.basename(wav), track="A1", fps=None, zoom=None)
                    a.pop("_words", None)
                    new += [v, a]
                    continue
            new.append(it)
        items = new
        notes.append(f"Audio enhanced (FFmpeg: denoise, compression, -14 LUFS) for {len([w for w in done.values() if w])} clips")
    for it in items:
        it.pop("_words", None)
        it.pop("_cap_off", None)
    miss = [r for r in rows if r.get("missing")]
    if miss:
        notes.append(f"{len(miss)} script lines ka pakka take nahi mila - table mein closest take dekhiye.")
    if not has_tr:
        notes.append("Transcripts nahi hain - pehle Transcribe chalaiye.")
    v = AS._version(name, "reel", items, rows, music=o["music"] or None, notes=notes)
    v.update(captions=captions, titles=titles, aspect=o["aspect"], resolution=ASPECTS.get(o["aspect"], (1080, 1920)),
             opts=o, duration=round(total, 2), color_balance=bool(o["color_balance"]),
             smart_reframe=bool(o["smart_reframe"]), markers=bool(o["markers"]))
    return v
