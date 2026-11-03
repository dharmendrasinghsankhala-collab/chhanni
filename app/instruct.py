"""
Edit instruction -> switch / option changes for regenerating a version.
Rule-based Hinglish/English (free, offline); an LLM (AI setup) handles anything the rules don't understand.

  "30 sec karo" · "fast / tez" · "slow" · "B-roll zyada | kam | hatao" · "zoom hatao | zoom 120"
  "captions ek ek shabd | hatao" · "hook: <text> | hook hatao" · "cta: <text>" · "naam: Shailesh Birani - Owner"
  "music hatao | music dheemi" · "awaaz saaf karo" · "16:9 | 1:1 | 4:5 | vertical" · "colour theek karo"
  "line 3 hatao" · "staff 3 hatao" · "line 2 ka dusra take" · "line 5 se shuru karo" · "best line pehle"
"""
import json
import re

NUM = r"(\d+(?:\.\d+)?)"
OFF = r"(mat|nahi|na|hatao|hata do|remove|off|band|no)"


def _neg(word, t):
    return bool(re.search(word + r"s?\s*" + OFF + r"\b", t) or re.search(r"\b(no|without|bina)\s*" + word, t))


def _beat_refs(text, rows):
    out = []
    lines = [r for r in rows if r.get("kind") not in ("section",)]
    for m in re.finditer(r"(?:line|beat|lain|point)\s*#?\s*(\d+)", text, re.I):
        n = int(m.group(1))
        if 1 <= n <= len(lines):
            out.append(lines[n - 1]["i"])
    for m in re.finditer(r"staff\s*(\d+)", text, re.I):
        for r in rows:
            if (r.get("speaker") or "").lower().replace(" ", "").startswith("staff" + m.group(1)):
                out.append(r["i"])
    for m in re.finditer(r"[\"“']([^\"”']{4,})[\"”']", text):
        import ai_audio
        best = max(lines, key=lambda r: ai_audio.similarity(m.group(1), r.get("text") or ""), default=None)
        if best is not None:
            out.append(best["i"])
    return list(dict.fromkeys(out))


def _text_after(key, instruction):
    m = re.search(key + r"\s*[:=-]\s*(.+?)(?:\s*[|;\n]|$)", instruction, re.I)
    return m.group(1).strip().strip('"“”') if m else None


def rules(instruction, version):
    t = " " + instruction.lower().strip() + " "
    cur = version.get("opts") or {}
    rows = version.get("beats") or []
    o, notes = {}, []
    m = re.search(NUM + r"\s*(?:sec|second|seconds|s)\b", t) or re.search(NUM + r"\s*(?:min|minute)", t)
    if m:
        o["target_sec"] = float(m.group(1)) * (60 if "min" in m.group(0) else 1)
        notes.append(f"target {o['target_sec']:.0f}s")
    if re.search(r"\b(fast|tez|tight|punchy|speed)\b", t):
        o.update(pause=0.25, remove_silences=True)
        notes.append("tight cuts")
    if re.search(r"\b(slow|aaram|dheere|relaxed)\b", t) and "music" not in t:
        o["pause"] = 0.6
        notes.append("relaxed pacing")
    if re.search(r"silence|chuppi|gap", t):
        o["remove_silences"] = not _neg(r"(silence|chuppi|gap)", t)
    if re.search(r"filler|umm|\buh\b", t):
        o["drop_fillers"] = not _neg(r"(filler)", t) or bool(re.search(r"filler\w*\s*(hatao|remove)", t))
    if re.search(r"b-?roll", t):
        o["broll"] = "none" if _neg(r"b-?roll", t) else ("more" if re.search(r"zyada|jyada|more|bahut", t) else "some")
        notes.append(f"B-roll {o['broll']}")
    if "zoom" in t:
        o["zoom"] = not _neg("zoom", t)
        m = re.search(r"zoom\s*" + NUM, t)
        if m and o["zoom"]:
            z = float(m.group(1))
            o["zoom_level"] = z / 100 if z > 3 else z
        notes.append("zoom " + ("on" if o["zoom"] else "off"))
    if re.search(r"caption|subtitle", t):
        o["captions"] = not _neg(r"(caption|subtitle)", t)
        if o["captions"]:
            if re.search(r"ek ek|word by word|word-by-word|har shabd|single word", t):
                o["caption_words"] = 1
            m = re.search(r"(\d)\s*(?:words?|shabd)", t)
            if m:
                o["caption_words"] = int(m.group(1))
        notes.append("captions " + ("on" if o["captions"] else "off"))
    x = _text_after("hook", instruction)
    if x is not None:
        o.update(hook=True, hook_text=x)
    elif "hook" in t and _neg("hook", t):
        o["hook"] = False
    x = _text_after(r"(?:cta|end card|outro)", instruction)
    if x is not None:
        o.update(cta=True, cta_text=x)
    elif re.search(r"(cta|end card)", t):
        o["cta"] = not _neg(r"(cta|end card)", t)
    x = _text_after(r"(?:naam|name|lower ?third)", instruction)
    if x is not None:
        o.update(lower_third=True, lower_third_text=x)
    elif re.search(r"lower ?third", t):
        o["lower_third"] = not _neg(r"lower ?third", t)
    if re.search(r"music|gaana|song", t):
        if _neg(r"(music|gaana|song)", t):
            o["music"] = ""
        elif re.search(r"(dheemi|dheema|low|kam)", t):
            o["music_db"] = -26.0
        elif re.search(r"(tez|loud|zyada)", t):
            o["music_db"] = -14.0
        m = re.search(r"music\s*(-\d+)", t)
        if m:
            o["music_db"] = float(m.group(1))
    if re.search(r"(audio|awaaz|awaz|voice)\s*(saaf|clean|enhance|better|theek)|enhance", t):
        o["enhance_audio"] = not _neg("enhance", t)
    if re.search(r"(colou?r|rang)", t):
        o["color_balance"] = not _neg(r"(colou?r|rang)", t)
    if re.search(r"(sfx|sound effect|whoosh)", t):
        if _neg(r"(sfx|sound effect|whoosh)", t):
            o["sfx"] = ""
    for asp, pat in (("16:9", r"16:9|horizontal|landscape|youtube"), ("1:1", r"1:1|square"), ("4:5", r"4:5|portrait post"),
                     ("9:16", r"9:16|vertical|reel|shorts")):
        if re.search(pat, t):
            o["aspect"] = asp
            break
    if re.search(r"reframe", t):
        o["smart_reframe"] = not _neg("reframe", t)
    if re.search(r"best line pehle|auto hook|strong(est)? (line|opening)", t):
        o["auto_hook"] = True
    refs = _beat_refs(instruction, rows)
    if refs and re.search(r"(hatao|hata do|remove|delete|nikalo|drop)", t):
        o["remove_beats"] = sorted(set(cur.get("remove_beats", [])) | set(refs))
        notes.append(f"{len(refs)} line hatai")
    elif refs and re.search(r"(dusra|doosra|alternate|alt|another|next|badlo|change)", t):
        alt = dict(cur.get("alt_take", {}))
        for i in refs:
            alt[str(i)] = int(alt.get(str(i), 0)) + 1
        o["alt_take"] = alt
        notes.append("alternate take")
    elif refs and re.search(r"(shuru|start|first|pehle|opening)", t):
        o["first_beat"] = refs[0]
        notes.append("nayi opening")
    return o, notes


def with_llm(instruction, version):
    import llm
    import reel
    lines = [{"line": n + 1, "i": r["i"], "kind": r.get("kind"), "speaker": r.get("speaker"),
              "text": (r.get("text") or "")[:120], "removed": r.get("removed", False)}
             for n, r in enumerate([r for r in version.get("beats") or [] if r.get("kind") != "section"])]
    cur = {k: v for k, v in (version.get("opts") or {}).items() if k in reel.DEFAULTS}
    system = ("You are an assistant video editor. Convert the editor's instruction (Hindi/Hinglish/English) into changes "
              "of the generator's options. Use only existing keys. Reply with JSON only.")
    user = (f"Version: {version.get('name')} ({version.get('duration')}s)\nOptions now: {json.dumps(cur, ensure_ascii=False)}\n"
            f"Lines: {json.dumps(lines, ensure_ascii=False)}\n\nInstruction: {instruction}\n\n"
            'Return {"changes": {...only changed keys...}, "note": "short Hinglish summary"}. remove_beats / first_beat use '
            'the "i" of lines; alt_take is {"i": take_number}.')
    r = llm.chat(system, user, True)
    ch = {k: v for k, v in (r.get("changes") or {}).items() if k in reel.DEFAULTS}
    return ch, [r.get("note") or "LLM"]


def apply(instruction, version):
    o, notes = rules(instruction, version)
    used_llm = False
    if not o:
        try:
            import llm
            if llm.load_settings().get("llm_provider", "none") != "none":
                o, notes = with_llm(instruction, version)
                used_llm = True
        except Exception as e:
            notes = [f"LLM error: {e}"]
    if not o:
        raise RuntimeError("Instruction samajh nahi aaya. Jaise: '30 sec karo', 'B-roll zyada', 'zoom hatao', 'line 3 hatao', "
                           "'line 2 ka dusra take', 'hook: ...', 'cta: ...'. Ya AI setup mein LLM chuniye.")
    return o, notes, used_llm
