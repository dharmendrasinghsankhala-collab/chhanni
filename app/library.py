"""
Footage Library: search everything the deep analysis found, in plain words + filters.
  "close-up crying woman at night", "drone shot of the fort", "hands playing dholak", "he talks about water"
Ranking = CLIP text<->keyframe similarity + keyword hits in VLM descriptions, tags, people names and transcript.
Results can become a string-out version (-> Resolve / Premiere like any other version).
"""
import json
import re
import threading
from pathlib import Path

import numpy as np

_CACHE = {}
_LOCK = threading.Lock()
FACETS = ["size", "camera", "angle", "light", "place", "time", "scene", "subject", "emotion", "sound", "music_mood",
          "person", "use"]


def _cam(s):
    lab = (s.get("camera") or {}).get("label") or "static"
    return lab.split(" + ")[0]


def _index(out_dir):
    import deep
    import people
    out_dir = Path(out_dir)
    dd = out_dir / "deep"
    key = str(out_dir)
    stamp = max([p.stat().st_mtime for p in dd.glob("*.json")], default=0) if dd.exists() else 0
    with _LOCK:
        c = _CACHE.get(key)
        if c and c["stamp"] == stamp:
            return c
    ppl = people.load(out_dir)
    pname = {p["id"]: (p.get("name") or p["id"]) for p in ppl.get("people", [])}
    rows, embs = [], []
    try:
        import ai_audio
    except Exception:
        ai_audio = None
    for j, z in deep.load_all(out_dir):
        tr = None
        if ai_audio:
            try:
                tr = ai_audio.load_transcript(out_dir, j["clip_id"])
            except Exception:
                tr = None
        tsegs = (tr or {}).get("segments", [])
        for si, s in enumerate(j["segments"]):
            v = s.get("vlm") or {}
            persons = sorted({pname.get(ppl["faces"].get(f"{j['clip_id']}/{si}/{fi}"), "")
                              for fi in range(len(s.get("faces") or []))} - {""})
            said = " ".join(t.get("text", "") for t in tsegs if t.get("start", 0) < s["end"] and t.get("end", 0) > s["start"])
            text = " ".join(str(x) for x in [v.get("description"), v.get("action"), " ".join(map(str, v.get("subjects") or [])),
                                              " ".join(map(str, v.get("objects") or [])), v.get("setting"), v.get("mood"),
                                              v.get("on_screen_text"), s.get("scene"), s.get("subject"), s.get("emotion"),
                                              " ".join(s.get("sound") or []), s.get("music_mood"), " ".join(persons),
                                              _cam(s), s.get("size")] if x)
            rows.append({"clip": j["clip_id"], "name": j.get("name"), "path": j.get("path"), "seg": si,
                         "start": s["start"], "end": s["end"], "kf": s.get("kf"), "fps": j.get("fps"),
                         "size": s.get("size"), "camera": _cam(s), "speed": (s.get("camera") or {}).get("speed"),
                         "angle": s.get("angle"), "light": s.get("light"), "place": s.get("place"), "time": s.get("time"),
                         "scene": s.get("scene"), "subject": s.get("subject"), "emotion": s.get("emotion"),
                         "sound": (s.get("sound") or [None])[0], "music_mood": s.get("music_mood"),
                         "person": persons, "use": v.get("editorial_use"), "hook": v.get("hook_potential"),
                         "beauty": s.get("beauty") or 0, "desc": v.get("description") or "", "said": said[:300],
                         "text": (text + " " + said).lower()})
            embs.append(z["clip"][si] if si < len(z["clip"]) else np.zeros(512, np.float16))
    E = np.stack(embs).astype(np.float32) if embs else np.zeros((0, 512), np.float32)
    c = {"stamp": stamp, "rows": rows, "E": E}
    with _LOCK:
        _CACHE[key] = c
    return c


def facets(out_dir):
    c = _index(out_dir)
    res = {}
    for f in FACETS:
        cnt = {}
        for r in c["rows"]:
            vals = r[f] if isinstance(r[f], list) else [r[f]]
            for v in vals:
                if v not in (None, ""):
                    cnt[str(v)] = cnt.get(str(v), 0) + 1
        res[f] = sorted(cnt.items(), key=lambda x: -x[1])[:30]
    return {"segments": len(c["rows"]), "clips": len({r["clip"] for r in c["rows"]}), "facets": res}


def search(out_dir, query="", filters=None, limit=80):
    c = _index(out_dir)
    rows, E = c["rows"], c["E"]
    if not rows:
        return []
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
    mask = np.ones(len(rows), bool)
    for k, v in filters.items():
        if k == "min_beauty":
            mask &= np.array([(r["beauty"] or 0) >= float(v) for r in rows])
        elif k in FACETS:
            vs = set(v if isinstance(v, list) else [v])
            mask &= np.array([bool(vs & set(r[k] if isinstance(r[k], list) else [r[k]])) for r in rows])
    score = np.array([r["beauty"] / 400.0 for r in rows], np.float32)
    q = (query or "").strip()
    if q:
        sim = None
        try:
            import ai_visual
            if len(E) and ai_visual.clip_available():
                qe = ai_visual.clip_text([q])
                if qe is not None:
                    sim = E @ qe[0]
        except Exception:
            sim = None
        if sim is not None:
            valid = np.linalg.norm(E, axis=1) > 0.1
            mu, sd = sim[valid].mean() if valid.any() else 0, sim[valid].std() + 1e-6 if valid.any() else 1
            score += np.where(valid, (sim - mu) / sd, -1.0)
        words = [w for w in re.findall(r"[\w']+", q.lower()) if len(w) > 2]
        if words:
            kw = np.array([sum(1 for w in words if w in r["text"]) / len(words) for r in rows], np.float32)
            score += 1.6 * kw
    idx = [i for i in np.argsort(-score) if mask[i]][:limit]
    out = []
    for i in idx:
        r = dict(rows[i])
        r.pop("text", None)
        r["score"] = round(float(score[i]), 3)
        out.append(r)
    return out


def stringout(out_dir, picks, name="Library selects"):
    """picks: [{clip, seg}] in order -> saved version (V1 string-out)."""
    import assemble
    c = _index(out_dir)
    by = {(r["clip"], r["seg"]): r for r in c["rows"]}
    items, rec = [], 0.0
    for p in picks:
        r = by.get((p["clip"], int(p["seg"])))
        if not r:
            continue
        L = r["end"] - r["start"]
        items.append({"path": r["path"], "name": r["name"], "clip": r["clip"], "start": round(r["start"], 3),
                      "end": round(r["end"], 3), "rec": round(rec, 3), "fps": r["fps"], "track": "V1", "media": "av",
                      "score": r["beauty"], "beat": None, "text": r["desc"][:120] or None,
                      "label": f"{r['size'] or ''} {r['camera'] or ''}".strip(), "thumb": None})
        rec += L
    if not items:
        raise RuntimeError("Koi shot select nahi hua.")
    v = assemble._version(name, "stringout", items, notes=[f"{len(items)} shots from Library search"])
    vd = Path(out_dir) / "versions"
    vd.mkdir(exist_ok=True)
    (vd / f"{v['id']}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    return v
