"""
AI Team - a film crew of role agents that think the way great filmmakers do.
  concept   : Concept maker / writer  -> angles, hooks, story structure from the footage you actually have
  director  : emotion & story arc, character journey, scene intent
  dop       : image quality, composition, light, lens/shot-size variety, camera movement motivation
  editor    : rhythm, cut points, pacing, sound/music, breathing space, continuity
  producer  : audience, platform, runtime, clarity, what to cut
Each role gets the craft principles below + a compact log of the footage / the version, and returns JSON.
Works with any provider in llm.py (local Ollama / Qwen3-VL by default, or Claude / Gemini / OpenAI-compatible).
"""
import json
from pathlib import Path

import llm

CRAFT = {
    "common": """You are part of a world-class film crew (feature films, documentaries, music videos, ad films).
Think like the best: Walter Murch, Thelma Schoonmaker, Roger Deakins, Emmanuel Lubezki, Satyajit Ray, Werner Herzog,
the BBC Natural History Unit, Netflix documentary teams, top ad-film directors.
Principles you always apply:
- Murch's Rule of Six for every cut: emotion first (51%), then story (23%), rhythm (10%), eye-trace (7%),
  2D screen direction (5%), 3D space (4%). Never sacrifice emotion for continuity.
- Every shot must earn its place: it must add information, emotion or rhythm. Cut anything that repeats.
- Show, don't tell. Prefer a strong image or a real moment over explanation.
- Hook: the first 3-10 seconds must create a question, a tension or a striking image.
- Structure: setup -> conflict / question -> escalation -> turning point -> resolution / change.
- Contrast creates meaning (Kuleshov): wide vs close, loud vs silent, stillness vs motion, hope vs loss.
- Sound is half the picture: let moments breathe, use silence before big beats, J-cuts / L-cuts for flow.
Be concrete and specific to THIS footage. Refer to segments by their [id]. Short sentences. No generic advice.""",
    "concept": """ROLE: Concept maker / writer-director.
Find the strongest story hidden in the footage: the character, the want, the obstacle, the change, the theme.
Propose angles a top documentary / ad / music-video team would pitch, each with a killer hook built from real shots.""",
    "director": """ROLE: Director.
Judge emotion and story: does each section make the audience feel something? Is there a clear arc and a turning point?
Does the opening hook? Does the ending land? Is the main character's journey clear? Where is the emotional peak?""",
    "dop": """ROLE: Director of Photography.
Judge the images: sharpness, exposure, composition, light quality and mood, shot-size variety (wide -> medium -> close),
angle variety, motivated camera movement, visual continuity (day/night, colour), the most beautiful frames.
Flag weak / soft / badly lit shots and name better alternatives from the footage log.""",
    "editor": """ROLE: Editor.
Judge rhythm and pacing: shot lengths vs music and emotion, cut points, jump cuts, repetition, breathing space,
sound bridges (J/L cuts), the music's rise and fall, where to slow down and where to accelerate.""",
    "producer": """ROLE: Producer.
Judge it for the audience and platform: is the idea clear in the first seconds, is the runtime right, is anything
confusing, boring or too long, does it fit the brand / platform (YouTube, Instagram reel, OTT, TV, festival)?
Decide priorities: what MUST change before delivery.""",
}

ROLES = ["director", "dop", "editor", "producer"]
ROLE_NAMES = {"concept": "Concept maker", "director": "Director", "dop": "DOP", "editor": "Editor", "producer": "Producer"}

REVIEW_FORMAT = """Reply ONLY with JSON:
{"score": 0-10, "verdict": one sentence,
 "strengths": [up to 3 short points],
 "problems": [{"at": "mm:ss or [segment id]", "issue": "...", "fix": "..."}],
 "instructions": [up to 5 short edit instructions the edit engine can apply, e.g.
   "best line pehle", "30 sec", "fast", "B-roll zyada", "zoom", "captions ek ek shabd", "line 4 hatao",
   "line 2 dusra take", "hook: <text>", "music", "slow", "B-roll kam"]}"""


def _fmt(t):
    t = max(0, int(round(t or 0)))
    return f"{t // 60:02d}:{t % 60:02d}"


# ------------------------------------------------------------------ footage knowledge
def footage_log(out_dir, max_lines=140):
    """Compact, LLM-friendly log of the analysed footage (best segments first)."""
    import library
    c = library._index(out_dir)
    rows = c["rows"]
    if not rows:
        return "", {}
    ranked = sorted(rows, key=lambda r: -((r["hook"] or 0) * 6 + r["beauty"] + (25 if r["said"] else 0)))[:max_lines]
    ranked.sort(key=lambda r: (r["clip"], r["start"]))
    ids, lines = {}, []
    for n, r in enumerate(ranked):
        sid = f"S{n + 1}"
        ids[sid] = (r["clip"], r["seg"])
        bits = [f"[{sid}]", r["name"] or r["clip"], f"{_fmt(r['start'])}-{_fmt(r['end'])}", r["size"] or "",
                r["camera"] or "", r["light"] or "", r["scene"] or ""]
        if r["person"]:
            bits.append("who:" + ",".join(r["person"]))
        if r["emotion"]:
            bits.append("emo:" + r["emotion"])
        if r["sound"]:
            bits.append("snd:" + r["sound"])
        bits.append(f"beauty:{r['beauty']}")
        if r["hook"]:
            bits.append(f"hook:{r['hook']}")
        line = " ".join(b for b in bits if b)
        if r["desc"]:
            line += " | " + r["desc"][:140]
        if r["said"]:
            line += ' | says: "' + r["said"][:160] + '"'
        lines.append(line)
    f = __import__("library").facets(out_dir)["facets"]
    head = "FOOTAGE OVERVIEW: " + "; ".join(f"{k}: " + ", ".join(f"{v}({n})" for v, n in f[k][:6])
                                          for k in ("scene", "subject", "size", "camera", "emotion", "person", "sound")
                                          if f.get(k))
    return head + "\n\nBEST SEGMENTS:\n" + "\n".join(lines), ids


def version_log(out_dir, version, max_items=120):
    """Timeline of a version enriched with what deep analysis knows about each used shot."""
    import deep
    deepmap = {j["clip_id"]: j for j, _ in deep.load_all(out_dir)}
    lines = [f"VERSION '{version.get('name')}' mode={version.get('mode')} duration={_fmt(version.get('duration'))}"]
    items = sorted([it for it in version.get("items", []) if it.get("media") != "a"], key=lambda x: (x["rec"], x["track"]))
    for it in items[:max_items]:
        j = deepmap.get(it.get("clip") or "")
        info = ""
        if j:
            mid = (it["start"] + it["end"]) / 2
            seg = next((s for s in j["segments"] if s["start"] <= mid < s["end"]), None)
            if seg:
                v = seg.get("vlm") or {}
                info = " ".join(str(x) for x in [seg.get("size"), (seg.get("camera") or {}).get("label"),
                                                  seg.get("light"), seg.get("emotion"), f"beauty:{seg.get('beauty')}",
                                                  "| " + v["description"][:110] if v.get("description") else ""] if x)
        dur = it["end"] - it["start"]
        lines.append(f"{_fmt(it['rec'])} {it['track']} {dur:.1f}s {it.get('name')}"
                     f"{' text:' + repr(it['text'][:90]) if it.get('text') else ''} {info}")
    music = [it for it in version.get("items", []) if it.get("media") == "a"]
    if music:
        lines.append("MUSIC: " + ", ".join(m.get("name", "") for m in music))
    return "\n".join(lines)


def _brief(b):
    b = b or {}
    return ("BRIEF: genre={g}; platform={p}; target length={d}; audience={a}; notes={n}"
            .format(g=b.get("genre") or "documentary", p=b.get("platform") or "YouTube",
                    d=b.get("duration") or "flexible", a=b.get("audience") or "general",
                    n=b.get("notes") or "-"))


# ------------------------------------------------------------------ agents
def concept(out_dir, brief=None):
    log_txt, ids = footage_log(out_dir)
    if not log_txt:
        raise RuntimeError("Pehle Deep analysis chalaiye - AI Team ko footage ki samajh wahi se milti hai.")
    sys_ = CRAFT["common"] + "\n\n" + CRAFT["concept"]
    user = (_brief(brief) + "\n\n" + log_txt + """

Reply ONLY with JSON: {"concepts": [ {"title": "...", "logline": one sentence, "theme": "...",
 "hook": {"idea": "...", "segments": ["S..", ...]},
 "structure": [{"part": "Opening|Act 1|...", "beat": "...", "segments": ["S..", ...]}],
 "music_mood": "...", "why_it_works": "..."} ] }  - give 3 different concepts, use only segment ids from the log.""")
    r = llm.chat(sys_, user, want_json=True, timeout=600)
    for cpt in r.get("concepts", []):
        for k in ("hook",):
            if isinstance(cpt.get(k), dict):
                cpt[k]["refs"] = [ids[s] for s in cpt[k].get("segments", []) if s in ids]
        for st in cpt.get("structure", []) or []:
            st["refs"] = [ids[s] for s in st.get("segments", []) if s in ids]
    _save(out_dir, "concepts", r)
    return r


def review(out_dir, version_id, brief=None, roles=None):
    import plan
    v = plan.load_version(out_dir, version_id)
    vlog = version_log(out_dir, v)
    flog, _ = footage_log(out_dir, max_lines=60)
    res = {"version": version_id, "roles": {}}
    for role in roles or ROLES:
        sys_ = CRAFT["common"] + "\n\n" + CRAFT[role]
        user = (_brief(brief) + "\n\n" + vlog + ("\n\nOTHER AVAILABLE FOOTAGE (alternatives):\n" + flog if flog and role in ("dop", "director") else "")
                + "\n\n" + REVIEW_FORMAT)
        try:
            res["roles"][role] = llm.chat(sys_, user, want_json=True, timeout=600)
        except Exception as e:
            res["roles"][role] = {"error": str(e)[:300]}
    # producer-style final call: merge instructions, most-agreed first
    votes = {}
    for role, r in res["roles"].items():
        for ins in (r.get("instructions") or []) if isinstance(r, dict) else []:
            k = str(ins).strip()
            if k:
                votes.setdefault(k.lower(), [k, 0])[1] += 2 if role == "producer" else 1
    res["plan"] = [k for k, _ in sorted(votes.values(), key=lambda x: -x[1])][:6]
    sc = [r.get("score") for r in res["roles"].values() if isinstance(r, dict) and isinstance(r.get("score"), (int, float))]
    res["score"] = round(sum(sc) / len(sc), 1) if sc else None
    _save(out_dir, f"review_{version_id}", res)
    return res


def ask(out_dir, role, question, brief=None, version_id=None):
    """Free chat with one crew member about this project."""
    sys_ = CRAFT["common"] + "\n\n" + CRAFT.get(role, CRAFT["director"])
    ctx = footage_log(out_dir, max_lines=80)[0]
    if version_id:
        import plan
        ctx = version_log(out_dir, plan.load_version(out_dir, version_id)) + "\n\n" + ctx
    user = _brief(brief) + "\n\n" + ctx + "\n\nQUESTION: " + question + \
        "\nAnswer in the user's language (Hinglish if they wrote Hinglish), concrete, max 12 lines."
    return llm.chat(sys_, user, want_json=False, timeout=600)


def _save(out_dir, name, obj):
    d = Path(out_dir) / "crew"
    d.mkdir(exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def load_saved(out_dir, name):
    p = Path(out_dir) / "crew" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
