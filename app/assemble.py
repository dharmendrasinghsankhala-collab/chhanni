"""
Chhanni assembly: builds alternative multi-track timeline versions.

Modes
  documentary : script / concept order, best takes on V1, matching B-roll covering on V2
  film        : scene dialogue, best take per line with angle (shot-size) variety, no overlays
  music_video : B-roll cut on the music's beats, energy-driven cut lengths, music on A1
  real_estate : spatial flow (aerial -> exterior -> rooms -> outdoor -> aerial), smooth moves, optional music
  hero_reel   : best hero shots, beat-snapped if music given
Version item: {path,name,start,end,rec,fps,track:"V1"|"V2",media:"av"|"v"|"a",score,beat,text,label}
"""
import json
import os
import time
import uuid
from pathlib import Path

import script as S

ROOM_ORDER = ["Facade", "Building exterior", "Garden", "Living room", "Dining", "Kitchen", "Hallway", "Staircase",
              "Bedroom", "Bathroom", "Office", "Balcony / terrace", "Pool"]


def _item(p, start, end, rec, track="V1", media="av", beat=None, text=None, label=None):
    return {"path": p["path"], "name": p["name"], "clip": p.get("clip"), "start": round(start, 3),
            "end": round(end, 3), "rec": round(rec, 3), "fps": p.get("fps"), "track": track, "media": media,
            "score": p.get("score", p.get("hero", 0)), "beat": beat, "text": text, "label": label,
            "thumb": p.get("thumb")}


def _fps_of(clips):
    return {c["id"]: c["fps"] for c in clips}


def _version(name, mode, items, beats=None, music=None, notes=None):
    dur = max([it["rec"] + it["end"] - it["start"] for it in items], default=0)
    return {"id": uuid.uuid4().hex[:8], "name": name, "mode": mode, "items": items, "beats": beats or [],
            "music": music, "duration": round(dur, 2), "notes": notes or [], "created": time.strftime("%Y-%m-%d %H:%M")}


# ------------------------------------------------------------------ documentary / film
def _cover(pool, text, rec, dur, used, out_dir, group=None, min_len=2.5, max_len=4.5):
    """B-roll pieces covering [rec, rec+dur] on V2."""
    items, t = [], rec
    cands = S.match_visual(text, [p for p in pool if p["key"] not in used], out_dir, used, k=12)
    if group:
        cands.sort(key=lambda p: (-(p["rank"] + (0.08 if p["group"] == group else 0))))
    for p in cands:
        if t >= rec + dur - 1.0:
            break
        L = min(max_len, rec + dur - t, p["end"] - p["start"])
        if L < min_len:
            continue
        mid = (p["start"] + p["end"]) / 2
        items.append(_item(p, mid - L / 2, mid + L / 2, t, "V2", "v", text=text[:80], label=p.get("move")))
        used.add(p["key"])
        t += L
    return items


MIN_SIM = 0.5      # below this a script line is treated as not recorded (VO / pickup needed)
ON_CAMERA = __import__("re").compile(r"(camera|कैमरे|कैमरा|on cam|to cam|ptc|देखकर|face)", __import__("re").I)


def _fresh(takes, used_takes):
    """Same line said several times in the script (Staff 1..4) -> use a different take each time."""
    def clash(t):
        return any(u["clip"] == t["clip"] and min(u["end"], t["end"]) - max(u["start"], t["start"]) > 0.3 for u in used_takes)
    fresh = [t for t in takes if not clash(t)]
    return fresh or takes


def documentary(out_dir, clips, mapped, opts):
    pool = S.broll_pool(clips)
    fps = _fps_of(clips)
    versions = []
    variants = [("Story - best takes", 0, True, 4.5), ("Story - alternate takes", 1, True, 4.0),
                ("Tight cut", 0, False, 3.0)]
    for name, take_i, cover, blen in variants:
        used, items, rec, rows, used_takes = set(), [], 0.0, [], []
        for b in mapped["beats"]:
            if b["kind"] in ("section", "note"):
                if b["kind"] == "section":
                    rows.append({"i": b["i"], "kind": "section", "text": b["text"]})
                continue
            takes = _fresh(b.get("takes") or [], used_takes)
            if b["kind"] == "dialogue" and takes and takes[0]["sim"] < MIN_SIM:
                rows.append({"i": b["i"], "kind": "dialogue", "text": b["text"], "missing": True, "alts": takes[:4],
                             "rec": round(rec, 2)})
                continue
            if b["kind"] == "dialogue" and takes:
                if name == "Tight cut" and takes[0]["sim"] < 0.6:
                    continue
                t = takes[min(take_i, len(takes) - 1)]
                used_takes.append(t)
                p = dict(t, fps=fps.get(t["clip"]), score=int(t["score"] * 100))
                dur = t["end"] - t["start"]
                items.append(_item(p, t["start"], t["end"], rec, "V1", "av", b["i"], t["text"][:120], "dialogue"))
                hint = b.get("hint") or ""
                on_cam = bool(ON_CAMERA.search(hint))
                if cover and not on_cam and ((hint and dur > 3) or dur > 7):
                    lead = 1.0 if hint else 2.0
                    spoken = {u["clip"] for u in used_takes} | {x["clip"] for x in b.get("takes") or []}
                    items += _cover([q for q in pool if q["clip"] not in spoken], (hint + " " + b["text"]).strip(),
                                    rec + lead, dur - lead - 0.3, used, out_dir, t.get("group"), max_len=blen)
                rows.append({"i": b["i"], "kind": "dialogue", "text": b["text"], "take": t, "alts": takes[:4],
                             "rec": round(rec, 2)})
                rec += dur + 0.25
            else:
                L = b.get("dur") or (blen if b["kind"] == "visual" else 3.5)
                cands = b.get("broll") or S.match_visual(b["text"], pool, out_dir, used)
                placed = 0.0
                for p in cands:
                    if p["key"] in used or placed >= L - 0.5:
                        continue
                    seg = min(L - placed, p["end"] - p["start"], blen)
                    if seg < 1.5:
                        continue
                    mid = (p["start"] + p["end"]) / 2
                    items.append(_item(p, mid - seg / 2, mid + seg / 2, rec + placed, "V1", "v", b["i"], b["text"][:80],
                                       p.get("move")))
                    used.add(p["key"])
                    placed += seg
                rows.append({"i": b["i"], "kind": b["kind"], "text": b["text"], "broll": cands[:4],
                             "missing": b.get("missing", False), "rec": round(rec, 2)})
                rec += placed
        notes = []
        miss = [r for r in rows if r.get("missing")]
        if miss:
            notes.append(f"{len(miss)} script lines kisi take se pakka match nahi hui - timeline mein laal marker hai. Table mein closest take dekhiye, script ke shabd bole gaye shabdon jaise kijiye, ya VO record kijiye.")
        if not mapped.get("has_transcripts"):
            notes.append("Transcripts nahi hain - dialogue match ke liye pehle 'Transcribe' chalaiye.")
        versions.append(_version(name, "documentary", items, rows, notes=notes))
    return versions


def film(out_dir, clips, mapped, opts):
    fps = _fps_of(clips)
    order = {"WIDE": 0, "WS": 1, "MS": 2, "MCU": 3, "CU": 4}
    out = []
    for name, variety in (("Best performance", 0.0), ("Angle variety", 0.12), ("Alternate takes", -1)):
        items, rec, rows, prev, used_takes = [], 0.0, [], None, []
        for b in mapped["beats"]:
            if b["kind"] in ("section", "note"):
                continue
            takes = _fresh(b.get("takes") or [], used_takes)
            if b["kind"] != "dialogue" or not takes:
                rows.append({"i": b["i"], "kind": b["kind"], "text": b["text"], "missing": b["kind"] == "dialogue"})
                continue
            if variety < 0:
                t = takes[1] if len(takes) > 1 else takes[0]
            else:
                def sc(t):
                    bonus = 0
                    if prev and t.get("shot") and t["shot"] != prev.get("shot"):
                        bonus += variety
                    if prev and t["clip"] == prev["clip"]:
                        bonus -= variety / 2
                    return t["score"] + bonus
                t = max(takes, key=sc)
            used_takes.append(t)
            p = dict(t, fps=fps.get(t["clip"]), score=int(t["score"] * 100))
            items.append(_item(p, t["start"], t["end"], rec, "V1", "av", b["i"], t["text"][:120], t.get("shot")))
            rows.append({"i": b["i"], "kind": "dialogue", "text": b["text"], "take": t, "alts": takes[:4], "rec": round(rec, 2)})
            rec += t["end"] - t["start"] + 0.1
            prev = t
        out.append(_version(name, "film", items, rows))
    return out


# ------------------------------------------------------------------ music video
def music_video(out_dir, clips, music, opts, ffmpeg):
    import beats as B
    m = B.analyze(music, ffmpeg)
    bt, en = m["beats"], m["energy"]
    if len(bt) < 8:
        raise RuntimeError("Music mein beats nahi mile.")
    pool = [p for p in S.broll_pool(clips, include_aroll=opts.get("use_aroll", False)) if p["end"] - p["start"] >= 1.0]
    if not pool:
        raise RuntimeError("B-roll selects nahi hain.")
    pool.sort(key=lambda p: -p["hero"])
    moving = {"Drone / aerial", "Slider / gimbal move", "Smooth pan", "Smooth tilt / crane", "Smooth handheld / gimbal"}
    out = []
    for name, rule in (("Energy cut", "energy"), ("Every 2 beats", 2), ("Calm - every 4 beats", 4)):
        items, i, last_clip, uses = [], 0, None, {}
        down = set(m["downbeats"])
        while i < len(bt) - 1:
            e = en[i] if i < len(en) else 0.5
            nb = (1 if e > 0.75 else 2 if e > 0.4 else 4) if rule == "energy" else rule
            j = min(len(bt) - 1, i + nb)
            L = bt[j] - bt[i]
            want_move = e > 0.6
            best, bsc = None, -1e9
            for p in pool:
                if p["clip"] == last_clip or p["end"] - p["start"] < L * 0.98:
                    continue
                sc = p["hero"] / 100 - 0.35 * uses.get(p["key"], 0)
                if want_move and p.get("move") in moving:
                    sc += 0.15
                if bt[i] in down and p.get("is_hero"):
                    sc += 0.1
                if sc > bsc:
                    best, bsc = p, sc
            if best is None:          # nothing long enough - shorten the cut to one beat
                if nb > 1:
                    rule_nb = 1
                    j = i + rule_nb
                    L = bt[j] - bt[i]
                    best = max(pool, key=lambda p: (p["end"] - p["start"] >= L, -uses.get(p["key"], 0), p["hero"]))
                else:
                    i += 1
                    continue
            k = uses.get(best["key"], 0)
            span = best["end"] - best["start"]
            off = (span - L) * (0.5 if k == 0 else (0.15 if k % 2 else 0.85))
            items.append(_item(best, best["start"] + off, best["start"] + off + L, bt[i], "V1", "v", label=best.get("move")))
            uses[best["key"]] = k + 1
            last_clip = best["clip"]
            i = j
        items.append({"path": music, "name": os.path.basename(music), "start": 0.0, "end": m["duration"],
                      "rec": 0.0, "fps": None, "track": "A1", "media": "a", "score": 0, "label": "music"})
        # video starts at first beat: shift everything so the first beat is at 0 and trim music accordingly
        out.append(_version(f"{name} ({m['tempo']:.0f} bpm)", "music_video", items, music=music,
                            notes=[f"Tempo {m['tempo']} bpm, {len(bt)} beats"]))
    return out


# ------------------------------------------------------------------ real estate
def real_estate(out_dir, clips, opts, music=None, ffmpeg="ffmpeg"):
    pool = S.broll_pool(clips)
    if not pool:
        raise RuntimeError("B-roll selects nahi hain.")
    smooth = {"Drone / aerial", "Slider / gimbal move", "Smooth pan", "Smooth tilt / crane", "Smooth handheld / gimbal",
              "Static (tripod)"}
    pool = [p for p in pool if p.get("move") in smooth or p["hero"] >= 70] or pool
    aerial = sorted([p for p in pool if p.get("move") == "Drone / aerial"], key=lambda p: -p["hero"])
    rest = [p for p in pool if p not in aerial]
    L = float(opts.get("shot_sec", 4.0))
    grid = None
    if music:
        import beats as B
        grid = B.analyze(music, ffmpeg)

    def lay(seq, name, limit=None):
        items, rec = [], 0.0
        for p in seq:
            if limit and rec >= limit:
                break
            ln = min(L, p["end"] - p["start"])
            if ln < 2.0:
                continue
            if grid:
                nxt = [b for b in grid["beats"] if b >= rec + ln - 0.25]
                if nxt and nxt[0] - rec <= p["end"] - p["start"]:
                    ln = nxt[0] - rec
            mid = (p["start"] + p["end"]) / 2
            items.append(_item(p, mid - ln / 2, mid + ln / 2, rec, "V1", "v", label=p.get("room") or p.get("move")))
            rec += ln
        if music:
            items.append({"path": music, "name": os.path.basename(music), "start": 0.0, "end": rec, "rec": 0.0,
                          "fps": None, "track": "A1", "media": "a", "score": 0, "label": "music"})
        return _version(name, "real_estate", items, music=music)

    shoot = sorted(rest, key=lambda p: p["order"])
    v1 = aerial[:1] + shoot + aerial[1:2]
    has_rooms = any(p.get("room") for p in rest)
    room_rank = {r: i for i, r in enumerate(ROOM_ORDER)}
    tour = sorted(rest, key=lambda p: (room_rank.get(p.get("room"), 50), p["order"]))
    v2 = aerial[:1] + tour + aerial[1:2]
    hi = sorted(pool, key=lambda p: -p["hero"])
    out = [lay(v1, "Walk-through (shoot order)"), lay(v2, "Room tour (CLIP rooms)" if has_rooms else "Room tour (needs AI pack - shoot order used)"),
           lay(aerial[:1] + [p for p in hi if p not in aerial[:1]], "Highlights 60s", limit=60)]
    return out


def hero_reel(out_dir, clips, opts, music=None, ffmpeg="ffmpeg"):
    pool = sorted(S.broll_pool(clips), key=lambda p: -p["hero"])
    items, rec, last = [], 0.0, None
    grid = None
    if music:
        import beats as B
        grid = B.analyze(music, ffmpeg)
    for p in pool:
        if rec >= float(opts.get("length", 60)):
            break
        if p["clip"] == last:
            continue
        ln = min(3.0, p["end"] - p["start"])
        if grid:
            nxt = [b for b in grid["beats"] if b >= rec + ln - 0.2]
            if nxt and nxt[0] - rec <= p["end"] - p["start"]:
                ln = nxt[0] - rec
        if ln < 1.2:
            continue
        mid = (p["start"] + p["end"]) / 2
        items.append(_item(p, mid - ln / 2, mid + ln / 2, rec, "V1", "v", label="hero"))
        rec += ln
        last = p["clip"]
    if music:
        items.append({"path": music, "name": os.path.basename(music), "start": 0.0, "end": rec, "rec": 0.0, "fps": None,
                      "track": "A1", "media": "a", "score": 0, "label": "music"})
    return [_version("Hero reel", "hero_reel", items, music=music)]


# ------------------------------------------------------------------ entry
def build(out_dir, clips, mode, script_text="", music=None, opts=None, ffmpeg="ffmpeg", cfg=None, concept=False):
    opts = opts or {}
    cfg = cfg or {}
    mapped = None
    if mode in ("documentary", "film"):
        if concept and script_text.strip():
            story = S.story_from_concept(out_dir, clips, cfg, script_text, float(opts.get("target_min", 3)))
            mapped = S.map_script(out_dir, story["beats"], clips, cfg)
            mapped = S.resolve_refs(mapped, clips)
        elif script_text.strip():
            mapped = S.map_script(out_dir, S.parse(script_text), clips, cfg)
        else:
            # no script: interview string-out (all A-roll soundbites in shoot order)
            beats = []
            for c in sorted([c for c in clips if c["type"] == "A"], key=lambda c: (c["group"], c["path"])):
                for s in c["selects"]:
                    beats.append({"kind": "dialogue", "text": s.get("text") or "", "ref": f"{c['id']}@{s['start']:.2f}-{s['end']:.2f}"})
            for i, b in enumerate(beats):
                b["i"] = i
            mapped = S.resolve_refs({"beats": beats, "has_transcripts": True}, clips)
        vs = documentary(out_dir, clips, mapped, opts) if mode == "documentary" else film(out_dir, clips, mapped, opts)
    elif mode == "music_video":
        if not music:
            raise RuntimeError("Music video ke liye music file chuniye.")
        vs = music_video(out_dir, clips, music, opts, ffmpeg)
    elif mode == "real_estate":
        vs = real_estate(out_dir, clips, opts, music, ffmpeg)
    elif mode == "hero_reel":
        vs = hero_reel(out_dir, clips, opts, music, ffmpeg)
    elif mode == "reel":
        import reel as R
        ro = dict(opts)
        if music:
            ro["music"] = music
        if opts.get("_single") or not opts.get("variations", True):
            vs = [R.build(out_dir, clips, script_text, ro, ffmpeg, cfg, concept, name=opts.get("_name") or "Reel")]
        else:
            vs = [R.build(out_dir, clips, script_text, ro, ffmpeg, cfg, concept, name="Reel"),
                  R.build(out_dir, clips, script_text, dict(ro, pause=0.25, broll="more", zoom_level=1.15), ffmpeg, cfg,
                          concept, name="Reel - punchy"),
                  R.build(out_dir, clips, script_text, dict(ro, pause=0.5, broll="none", zoom=False), ffmpeg, cfg, concept,
                          name="Reel - clean (no B-roll)")]
    else:
        raise RuntimeError("unknown mode")
    vd = Path(out_dir) / "versions"
    vd.mkdir(exist_ok=True)
    batch = time.strftime("%Y%m%d-%H%M%S")
    lineage = opts.get("_lineage") or {}
    for v in vs:
        v["batch"] = batch
        v["script"] = script_text[:20000]
        src_opts = {k: x for k, x in (v.get("opts") or opts).items() if not k.startswith("_")}
        v["source"] = {"mode": mode, "script": script_text[:20000], "music": music, "concept": bool(concept),
                       "opts": src_opts}
        v["root"] = lineage.get("root") or v["id"]
        v["parent"] = lineage.get("parent")
        v["instruction"] = lineage.get("instruction")
        v["gen"] = lineage.get("gen", 1)
        if v.get("captions"):
            ed = Path(out_dir) / "exports"
            ed.mkdir(exist_ok=True)
            import reel as R
            safe = "".join(ch for ch in v["name"] if ch not in '<>:"/\\|?*')
            sp = ed / f"{safe} {v['id']} captions.srt"
            sp.write_text(R.srt(v["captions"]), encoding="utf-8")
            v["captions_srt"] = str(sp)
        (vd / f"{v['id']}.json").write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    return vs


from plan import list_versions, load_version  # noqa: E402,F401
