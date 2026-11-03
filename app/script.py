"""
Chhanni script engine: script / outline / concept text -> beats -> matched takes and B-roll.

Beat kinds
  dialogue : spoken line (interview soundbite, actor line, VO recorded on set)  -> matched to transcripts
  visual   : what we should SEE ("[drone over fort at sunset]", "SHOT: hands making chai", "EXT. MARKET - DAY")
  music    : a music cue / section marker (used by music-video mode)
Formats understood (mix freely):
  NAME: line of dialogue           VO: narration               "quoted line"
  [visual description] / (visual)  SHOT: / VISUAL: / B-ROLL:   INT. / EXT. scene headings
  - bullet / 1. numbered outline   # Section heading           (5s) duration hint anywhere in a line
"""
import json
import re
from pathlib import Path

import ai_audio

VISUAL_PREFIX = re.compile(r"^\s*(shot|visual|b-?roll|broll|cut to|insert|montage|drone|aerial|establishing)\s*[:\-]", re.I)
SCENE_HEAD = re.compile(r"^\s*(int\.|ext\.|int/ext\.|scene\s*\d*)", re.I)
SPEAKER = re.compile(r"^\s*([A-Za-zऀ-ॿ][\wऀ-ॿ .'-]{0,24})\s*:\s*(.+)$")
DUR = re.compile(r"\((\d+(?:\.\d+)?)\s*(s|sec|secs|seconds)\)", re.I)


VISUAL_WORDS = re.compile(r"\b(b-?roll|broll|shot|drone|aerial|close-?up|wide|montage|cutaway|insert|slider|gimbal|"
                          r"visual|footage|timelapse|establishing)\b", re.I)
TITLE_LINE = re.compile(r"^\s*(outro|intro|end ?card|title|super|logo|cta|address|contact)\b", re.I)


def _clean(line):
    line = line.replace("**", "").strip()
    line = re.sub(r"\*(\[[^\]]*\])\*", r"\1", line)          # *[Staff 1]* -> [Staff 1]
    line = re.sub(r"^\*+|\*+$", "", line).strip()
    line = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"\1", line)     # *word* -> word
    return line.strip()


def parse(text):
    """Script / outline -> beats. Markdown, screenplay and Hindi/Hinglish formats."""
    raw_lines = [l for l in (text or "").splitlines()]
    has_heading = any(l.strip().startswith("#") for l in raw_lines)
    seen_heading = False
    beats, speaker, hint = [], None, None
    for raw in raw_lines:
        line = _clean(raw)
        if not line:
            continue
        if line.startswith("#"):
            seen_heading = True
            beats.append({"text": line.lstrip("# ").strip(), "kind": "section"})
            speaker, hint = None, None
            continue
        if has_heading and not seen_heading:
            beats.append({"text": line, "kind": "note"})          # notes above the first heading
            continue
        dur = None
        m = DUR.search(line)
        if m:
            dur = float(m.group(1))
            line = DUR.sub("", line).strip()
        line = re.sub(r"^(\d+[.)]|[-*•])\s+", "", line)
        if not line:
            continue
        # [Label] text  -> labelled dialogue ;  [direction] alone -> direction / visual
        mb = re.match(r"^[\[(]([^\])]+)[\])]\s*(.*)$", line)
        if mb and mb.group(2).strip():
            beats.append({"text": mb.group(2).strip().strip('"“”'), "kind": "dialogue", "speaker": mb.group(1).strip(),
                          "dur": dur, "hint": hint})
            continue
        if mb:
            d = mb.group(1).strip()
            if VISUAL_WORDS.search(d):
                beats.append({"text": d, "kind": "visual", "dur": dur})
            else:
                beats.append({"text": d, "kind": "direction"})  # resolved below
            continue
        if VISUAL_PREFIX.match(line):
            beats.append({"text": VISUAL_PREFIX.sub("", line).strip(" :-"), "kind": "visual", "dur": dur})
            continue
        if SCENE_HEAD.match(line):
            beats.append({"text": line, "kind": "visual", "scene": True, "dur": dur})
            continue
        if TITLE_LINE.match(line) and len(line.split()) <= 6:
            beats.append({"text": line, "kind": "visual", "title": True, "dur": dur or 4})
            continue
        sm = SPEAKER.match(line)
        if sm and len(sm.group(1).split()) <= 3 and not sm.group(1).strip().isdigit():
            sp, rest = sm.group(1).strip(), sm.group(2).strip()
            b = {"text": rest.strip('"“”'), "kind": "dialogue", "speaker": sp, "dur": dur}
            if sp.lower() in ("vo", "v.o", "v.o.", "narrator", "voiceover", "voice over"):
                b["vo"] = True
            beats.append(b)
            continue
        if re.match(r"^[\w\u0900-\u097F .'-]{1,24}:$", line):          # "Shailesh:" on its own line
            speaker = line[:-1].strip()
            continue
        beats.append({"text": line.strip('"“”'), "kind": "dialogue", "speaker": speaker, "dur": dur})
    # a direction followed by dialogue = section header + B-roll hint for those lines; otherwise a visual beat
    out = []
    for i, b in enumerate(beats):
        if b["kind"] == "direction":
            nxt = next((x for x in beats[i + 1:] if x["kind"] not in ("note",)), None)
            if nxt and nxt["kind"] == "dialogue":
                out.append({"text": b["text"], "kind": "section", "direction": True})
                hint = b["text"]
                for x in beats[i + 1:]:
                    if x["kind"] != "dialogue":
                        break
                    x.setdefault("hint", None)
                    x["hint"] = x["hint"] or hint
                continue
            b["kind"] = "visual"
        out.append(b)
    for i, b in enumerate(out):
        b["i"] = i
    return out


# ------------------------------------------------------------------ transcript index
def load_index(out_dir, clips, cfg):
    """All transcript windows (1-4 consecutive segments) of all clips, with quality info."""
    idx = []
    for c in clips:
        tr = ai_audio.load_transcript(out_dir, c["id"])
        if not tr:
            continue
        ana = ai_audio.analyze(tr, dead_air=cfg.get("dead_air_sec", 1.2), clip_pct=tr.get("clip_pct"),
                               clip_win=tr.get("clip_win", 0.5))
        segs = [s for s in ana["segments"] if "filler" not in s["flags"]]
        sel_ranges = [(s["start"], s["end"]) for s in c["selects"]]
        for i in range(len(segs)):
            text, start = "", segs[i]["start"]
            for k in range(i, min(i + 4, len(segs))):
                if k > i and segs[k]["start"] - segs[k - 1]["end"] > 2.5:
                    break
                text = (text + " " + segs[k]["text"]).strip()
                end = segs[k]["end"]
                flags = sorted({f for j in range(i, k + 1) for f in segs[j]["flags"]})
                cov = sum(max(0, min(end, b) - max(start, a)) for a, b in sel_ranges) / max(0.1, end - start)
                words = [w for j in range(i, k + 1) for w in segs[j].get("words", []) if w.get("w")]
                idx.append({"words": words, "clip": c["id"], "path": c["path"], "name": c["name"], "group": c.get("group"),
                            "start": round(max(0, start - 0.12), 2), "end": round(end + 0.15, 2), "text": text,
                            "flags": flags, "grams": _grams(ai_audio.norm_text(text)), "nseg": k - i + 1,
                            "video_ok": min(1.0, cov), "order": (c.get("start_tc") or "", c["path"], start),
                            "shot": _shot_at(c, start, end)})
    return idx


def _shot_at(c, a, b):
    best, ov = None, 0
    for s in c["selects"]:
        o = min(b, s["end"]) - max(a, s["start"])
        if o > ov and s.get("shot"):
            best, ov = s["shot"], o
    return best


def _grams(t):
    t = f" {t} "
    return {t[i:i + 3] for i in range(len(t) - 2)}


def refine_span(line, w, sim):
    """Trim a transcript window to the words that actually say the script line (word timestamps)."""
    words = w.get("words") or []
    n = len(ai_audio.norm_text(line).split())
    if not words or n == 0 or len(words) <= n + 1:
        return w
    best = (sim * 0.98, None)
    for i in range(len(words)):
        for L in range(max(1, n - 3), n + 4):
            if i + L > len(words):
                break
            t = " ".join(x["w"] for x in words[i:i + L])
            sc = ai_audio.similarity(line, t)
            if sc > best[0]:
                best = (sc, (i, L, t))
    if best[1] is None:
        return w
    i, L, t = best[1]
    w2 = dict(w)
    w2.update(start=round(max(0, words[i]["s"] - 0.12), 2), end=round(words[i + L - 1]["e"] + 0.18, 2), text=t,
              sim_refined=best[0])
    return w2


def match_dialogue(beat, idx, k=5):
    q = ai_audio.norm_text(beat["text"])
    qg = _grams(q)
    if not qg:
        return []
    pre = []
    for w in idx:
        inter = len(qg & w["grams"])
        if inter:
            pre.append((inter / len(qg | w["grams"]) + 0.5 * inter / len(qg), w))
    pre.sort(key=lambda x: -x[0])
    cands = []
    for _, w in pre[:60]:
        sim = ai_audio.similarity(beat["text"], w["text"])
        if sim < 0.3:
            continue
        w = refine_span(beat["text"], w, sim)
        sim = w.get("sim_refined", sim)
        q_ = 0.55 + 0.45 * w["video_ok"]
        if "retake" in w["flags"]:
            q_ -= 0.15
        if "false start" in w["flags"]:
            q_ -= 0.2
        if "audio clipping" in w["flags"]:
            q_ -= 0.25
        cands.append(dict({x: w[x] for x in ("clip", "path", "name", "group", "start", "end", "text", "flags",
                                              "shot", "order")}, sim=round(sim, 3), quality=round(q_, 2),
                          score=round(0.72 * sim + 0.28 * q_, 3)))
    # same line, several takes: when scores are close prefer the later take (usually the keeper)
    cands.sort(key=lambda c: -c["score"])
    if len(cands) > 1:
        top = cands[0]["score"]
        close = [c for c in cands if top - c["score"] < 0.04 and c["sim"] > 0.55]
        if len(close) > 1:
            latest = max(close, key=lambda c: c["order"])
            cands.remove(latest)
            cands.insert(0, latest)
    # de-duplicate overlapping windows of the same clip
    out = []
    for c in cands:
        if any(o["clip"] == c["clip"] and min(o["end"], c["end"]) - max(o["start"], c["start"]) > 0.5 for o in out):
            continue
        out.append(c)
        if len(out) >= k:
            break
    return out


SYNONYMS = {
    "drone": ["Drone / aerial", "Aerial view"], "aerial": ["Drone / aerial", "Aerial view"], "upar se": ["Drone / aerial"],
    "sunset": ["Golden hour", "Sky"], "sunrise": ["Golden hour", "Sky"], "golden": ["Golden hour"], "shaam": ["Golden hour"],
    "night": ["Night / low light"], "raat": ["Night / low light"], "close": ["Close-up"], "face": ["Close-up", "Portrait"],
    "wide": ["Wide / establishing"], "establishing": ["Wide / establishing"], "crowd": ["Crowd", "Group of people"],
    "bheed": ["Crowd"], "people": ["Group of people", "Crowd"], "log": ["Group of people"], "hands": ["Hands / detail"],
    "detail": ["Hands / detail"], "food": ["Food"], "khana": ["Food"], "chai": ["Food"], "car": ["Vehicle"],
    "gaadi": ["Vehicle"], "river": ["Water"], "lake": ["Water"], "nadi": ["Water"], "sky": ["Sky"], "fort": ["Monument / fort / temple"],
    "kila": ["Monument / fort / temple"], "temple": ["Monument / fort / temple"], "mandir": ["Monument / fort / temple"],
    "palace": ["Monument / fort / temple"], "mahal": ["Monument / fort / temple"], "street": ["City / street"],
    "market": ["City / street", "Crowd"], "bazaar": ["City / street", "Crowd"], "city": ["City / street"],
    "desert": ["Landscape"], "registan": ["Landscape"], "landscape": ["Landscape"], "hills": ["Landscape"],
    "dance": ["Dance"], "stage": ["Stage / performance"], "slider": ["Slider / gimbal move"], "gimbal": ["Slider / gimbal move"],
    "kitchen": ["Kitchen"], "bedroom": ["Bedroom"], "bathroom": ["Bathroom"], "living": ["Living room"], "pool": ["Pool"],
    "garden": ["Garden"], "balcony": ["Balcony / terrace"], "facade": ["Facade", "Building exterior"],
    "exterior": ["Building exterior", "Facade"], "interior": ["Interior"], "hero": ["★ Hero shot"],
}


def broll_pool(clips, include_aroll=False):
    pool = []
    for c in clips:
        if c["type"] == "A" and not include_aroll:
            continue
        for k, s in enumerate(c["selects"]):
            pool.append({"clip": c["id"], "path": c["path"], "name": c["name"], "group": c.get("group"),
                         "start": s["start"], "end": s["end"], "score": s["score"], "hero": s.get("hero", s["score"]),
                         "tags": s.get("tags", []), "shot": s.get("shot"), "move": s.get("move"), "room": s.get("room"),
                         "thumb": s.get("thumb"), "text": s.get("text", ""), "fps": c["fps"], "key": f"{c['id']}:{k}",
                         "order": (c.get("start_tc") or "", c["path"], s["start"]), "is_hero": s.get("is_hero", False)})
    return pool


def match_visual(beat_text, pool, out_dir=None, used=None, k=6):
    used = used or set()
    q = ai_audio.norm_text(beat_text)
    words = set(q.split())
    want = set()
    for w, tags in SYNONYMS.items():
        if (" " in w and w in q) or w in words:
            want.update(tags)
    clip_sims = None
    try:
        import ai_visual
        clip_sims = ai_visual.clip_search(out_dir, beat_text, [p["thumb"] for p in pool if p.get("thumb")]) if out_dir else None
    except Exception:
        clip_sims = None
    ranked = []
    for p in pool:
        if p["key"] in used:
            continue
        tagset = set(p["tags"]) | ({p["room"]} if p.get("room") else set())
        tag_hit = len(want & tagset) / max(1, len(want)) if want else 0
        name_hit = sum(1 for w in words if len(w) > 3 and (w in ai_audio.norm_text(p["name"] + " " + (p["group"] or ""))
                                                             or w in ai_audio.norm_text(p.get("text", ""))))
        txt = min(1.0, tag_hit + 0.25 * name_hit)
        if clip_sims is not None and p.get("thumb") in clip_sims:
            txt = max(txt, min(1.0, max(0.0, (clip_sims[p["thumb"]] - 0.18) / 0.14)))
        score = 0.6 * txt + 0.4 * (p["hero"] / 100) if (want or clip_sims) else p["hero"] / 100
        if p.get("text"):
            score -= 0.15          # clips with people talking make poor cutaways
        ranked.append((score, txt, p))
    ranked.sort(key=lambda x: -x[0])
    return [dict(p, match=round(t, 2), rank=round(s, 3)) for s, t, p in ranked[:k]]


def map_script(out_dir, beats, clips, cfg):
    idx = load_index(out_dir, clips, cfg)
    pool = broll_pool(clips)
    used = set()
    res = []
    for b in beats:
        r = dict(b)
        if b["kind"] == "dialogue":
            r["takes"] = match_dialogue(b, idx)
            if not r["takes"]:
                # not found in any transcript -> treat as a visual / VO-needed beat
                r["missing"] = True
                r["broll"] = match_visual(b["text"], pool, out_dir, used)
        elif b["kind"] == "visual":
            q = b["text"] if not b.get("title") else b["text"] + " sign board logo shop exterior"
            r["broll"] = match_visual(q, pool, out_dir, used)
            if r["broll"]:
                used.add(r["broll"][0]["key"])
        res.append(r)
    return {"beats": res, "has_transcripts": bool(idx), "windows": len(idx)}


# ------------------------------------------------------------------ LLM: story from concept
def soundbites(out_dir, clips, cfg, limit=400):
    out = []
    for c in clips:
        if c["type"] != "A":
            continue
        for s in c["selects"]:
            if s.get("text"):
                out.append({"id": f"{c['id']}@{s['start']:.2f}-{s['end']:.2f}", "speaker": c.get("group"),
                            "dur": round(s["end"] - s["start"], 1), "text": s["text"][:300]})
    return out[:limit]


def story_from_concept(out_dir, clips, cfg, concept, target_min=3.0):
    import llm
    bites = soundbites(out_dir, clips, cfg)
    if not bites:
        raise RuntimeError("Transcripts nahi hain - pehle 'Transcribe' chalaiye.")
    tags = {}
    for c in clips:
        if c["type"] == "B":
            for t in c.get("tags", []):
                tags[t] = tags.get(t, 0) + 1
    system = ("You are a senior documentary editor. Build a story from interview soundbites. Soundbites may be in "
              "Hindi (Devanagari), Hinglish or English. Use ONLY the given soundbite ids. Reply in JSON.")
    user = (f"CONCEPT / BRIEF:\n{concept}\n\nTarget length: about {target_min} minutes.\n"
            f"Available B-roll tags (count): {json.dumps(tags, ensure_ascii=False)}\n\n"
            "SOUNDBITES (id | speaker | seconds | text):\n" +
            "\n".join(f"{b['id']} | {b['speaker']} | {b['dur']} | {b['text']}" for b in bites) +
            '\n\nReturn JSON: {"title": "...", "beats": [ {"kind": "dialogue", "id": "<soundbite id>", "why": "..."} '
            'or {"kind": "visual", "text": "<what to show, English>", "dur": 4} ] } in story order: hook, context, '
            "development, emotional peak, resolution. Put 1-2 visual beats between sections.")
    r = llm.chat(system, user, True)
    beats = []
    for b in r.get("beats", []):
        if b.get("kind") == "dialogue" and b.get("id"):
            beats.append({"kind": "dialogue", "ref": b["id"], "text": next((x["text"] for x in bites if x["id"] == b["id"]), ""),
                          "why": b.get("why")})
        elif b.get("text"):
            beats.append({"kind": "visual", "text": b["text"], "dur": b.get("dur")})
    for i, b in enumerate(beats):
        b["i"] = i
    return {"title": r.get("title"), "beats": beats}


def resolve_refs(mapped, clips):
    """Beats that point at an exact soundbite id (from the LLM) get that take directly."""
    by = {c["id"]: c for c in clips}
    for b in mapped["beats"]:
        ref = b.get("ref")
        if not ref:
            continue
        cid, rng = ref.split("@")
        a, e = (float(x) for x in rng.split("-"))
        c = by.get(cid)
        if c:
            b["takes"] = [{"clip": cid, "path": c["path"], "name": c["name"], "group": c.get("group"), "start": a,
                           "end": e, "text": b["text"], "flags": [], "sim": 1.0, "quality": 1.0, "score": 1.0,
                           "shot": None, "order": (c.get("start_tc") or "", c["path"], a)}]
            b.pop("missing", None)
    return mapped
