"""
Chhanni plan - shared by the app, the Resolve panel and the Premiere panel.
Pure standard library (runs inside Resolve's Python too).

  load_project(out_dir)            -> (data, review)
  effective_clips(data, review)    -> clips after the editor's review (selects switched off, type changes)
  build_sequences(clips, ...)      -> what goes on which timeline
  export_xmeml(seqs, path)         -> FCP7 XML (Premiere / Resolve / FCP7 can import)
"""
import json
import os
from urllib.parse import quote
from xml.sax.saxutils import escape

PREMIERE_LABELS = {"good": "Forest", "ok": "Yellow", "weak": "Mango", "none": "Brown"}
RESOLVE_COLORS = {"good": "Green", "ok": "Yellow", "weak": "Orange", "none": "Chocolate"}


def grade(score):
    return "good" if score >= 80 else ("ok" if score >= 65 else "weak")


def app_data_dir():
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Chhanni")
    os.makedirs(d, exist_ok=True)
    return d


def recent_projects():
    p = os.path.join(app_data_dir(), "projects.json")
    try:
        with open(p, encoding="utf-8") as f:
            lst = json.load(f)
    except Exception:
        lst = []
    return [x for x in lst if os.path.exists(os.path.join(x.get("out", ""), "selects.json"))]


def resolve_out_dir(path):
    path = str(path) if path is not None else path
    if path and path.lower().endswith(".json"):
        return os.path.dirname(path)
    return path


def load_project(out_dir):
    out_dir = resolve_out_dir(out_dir)
    with open(os.path.join(out_dir, "selects.json"), encoding="utf-8") as f:
        data = json.load(f)
    review = load_review(out_dir)
    return data, review


def load_review(out_dir):
    p = os.path.join(out_dir, "review.json")
    try:
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
    except Exception:
        r = {}
    r.setdefault("off", {})        # clip_id -> [[start, end], ...] selects switched off
    r.setdefault("clip_off", [])   # clip ids excluded completely
    r.setdefault("type", {})       # clip_id -> "A" | "B"
    r.setdefault("star", {})       # clip_id -> [[start, end], ...] favourite selects
    return r


def save_review(out_dir, review):
    p = os.path.join(out_dir, "review.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=1)
    os.replace(tmp, p)


def _overlaps(sel, ranges):
    for a, b in ranges or []:
        ov = min(sel["end"], b) - max(sel["start"], a)
        if ov > 0.5 * (sel["end"] - sel["start"]):
            return True
    return False


def effective_clips(data, review, include_missing=False):
    out = []
    off_clips = set(review.get("clip_off", []))
    for c in data.get("clips", []):
        cid = c.get("id") or c["path"]
        c2 = dict(c)
        c2["type"] = review.get("type", {}).get(cid, c["type"])
        if cid in off_clips:
            c2["selects"] = []
            c2["excluded"] = True
        else:
            offs = review.get("off", {}).get(cid, [])
            stars = review.get("star", {}).get(cid, [])
            sels = []
            for s in c.get("selects", []):
                if _overlaps(s, offs):
                    continue
                s2 = dict(s)
                s2["star"] = _overlaps(s, stars)
                sels.append(s2)
            c2["selects"] = sels
        if not include_missing and not os.path.exists(c["path"]):
            c2["missing"] = True
        out.append(c2)
    return out


def build_sequences(clips, group_by="group", include=("A", "B"), prefix="Chhanni"):
    """group_by: 'group' (camera/card/day folder), 'folder' (exact folder) or 'none' (one timeline per type)."""
    groups = {}
    for c in clips:
        if not c["selects"] or c.get("missing") or c["type"] not in include:
            continue
        if group_by == "none":
            g = "All"
        elif group_by == "folder":
            g = os.path.basename(c["folder"]) or c["folder"]
        else:
            g = c.get("group") or os.path.basename(c["folder"])
        groups.setdefault((c["type"], g), []).append(c)
    seqs = []
    for (typ, g), cl in sorted(groups.items(), key=lambda kv: (kv[0][0] != "A", kv[0][1])):
        cl.sort(key=lambda c: (c.get("start_tc") or "", c["path"]))
        items = []
        for c in cl:
            for s in c["selects"]:
                items.append({"path": c["path"], "name": c.get("name") or os.path.basename(c["path"]),
                              "clip_id": c.get("id"), "start": s["start"], "end": s["end"],
                              "score": s["score"], "star": s.get("star", False), "fps": c["fps"],
                              "width": c.get("width"), "height": c.get("height"),
                              "duration": c.get("duration"), "has_audio": c.get("has_audio", True),
                              "audio_channels": c.get("audio_channels", 2)})
        seqs.append({"name": f"{prefix} {'A-roll' if typ == 'A' else 'B-roll'} - {g}", "type": typ,
                     "group": g, "items": items})
    return seqs


def clip_markers(clips):
    """Per source clip: the select ranges, for bin/source markers."""
    out = []
    for c in clips:
        if c.get("missing"):
            continue
        out.append({"path": c["path"], "type": c["type"], "group": c.get("group"),
                    "speech": c["type"] == "A" or any(s.get("text") for s in c["selects"]),
                    "best": max([s["score"] for s in c["selects"]], default=0),
                    "selects": [{"start": s["start"], "end": s["end"], "score": s["score"],
                                 "star": s.get("star", False), "shake": s.get("shake"),
                                 "sharp": s.get("sharp_rel"), "shot": s.get("shot"), "move": s.get("move"),
                                 "hero": s.get("is_hero", False), "tags": s.get("tags", [])} for s in c["selects"]]})
    return out


def plan_for_host(out_dir, group_by="group", include=("A", "B")):
    data, review = load_project(out_dir)
    clips = effective_clips(data, review)
    seqs = build_sequences(clips, group_by=group_by, include=include)
    return {"project": os.path.basename(resolve_out_dir(out_dir)), "out": resolve_out_dir(out_dir),
            "sequences": seqs, "clips": clip_markers(clips),
            "labels": PREMIERE_LABELS, "colors": RESOLVE_COLORS}


# ---------------------------------------------------------------- FCP7 XML (xmeml)
def _rate(fps):
    fps = float(fps or 25)
    ntsc = abs(fps - round(fps)) > 0.01
    tb = int(round(fps))
    return tb, ntsc


def _rate_xml(fps):
    tb, ntsc = _rate(fps)
    return f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def _pathurl(p):
    p = os.path.abspath(p).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://localhost" + quote(p, safe="/:")


def export_xmeml(seqs, path, project_name="Chhanni"):
    files = {}
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE xmeml>", '<xmeml version="4">',
           f"<project><name>{escape(project_name)}</name><children>"]
    cid = 0
    for si, seq in enumerate(seqs):
        if not seq["items"]:
            continue
        fps_votes = {}
        for it in seq["items"]:
            fps_votes[round(float(it["fps"]), 3)] = fps_votes.get(round(float(it["fps"]), 3), 0) + 1
        seq_fps = max(fps_votes, key=fps_votes.get)
        first = seq["items"][0]
        w, h = first.get("width") or 1920, first.get("height") or 1080
        vclips, aclips = [], []
        t = 0
        for it in seq["items"]:
            cid += 1
            fid = files.get(it["path"])
            new_file = fid is None
            if new_file:
                fid = f"file-{len(files)+1}"
                files[it["path"]] = fid
            fps = float(it["fps"])
            src_in = int(round(it["start"] * fps))
            src_out = max(src_in + 1, int(round(it["end"] * fps)))
            length = max(1, int(round((src_out - src_in) / fps * seq_fps)))
            src_dur = int(round((it.get("duration") or it["end"]) * fps))
            label = PREMIERE_LABELS[grade(it["score"])]
            if new_file:
                ch = int(it.get("audio_channels") or 2)
                audio = (f"<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>"
                         f"</samplecharacteristics><channelcount>{ch}</channelcount></audio>") if it.get("has_audio", True) else ""
                file_xml = (f'<file id="{fid}"><name>{escape(it["name"])}</name><pathurl>{escape(_pathurl(it["path"]))}</pathurl>'
                            f"{_rate_xml(fps)}<duration>{src_dur}</duration><media><video><samplecharacteristics>"
                            f"<width>{it.get('width') or w}</width><height>{it.get('height') or h}</height>"
                            f"</samplecharacteristics></video>{audio}</media></file>")
            else:
                file_xml = f'<file id="{fid}"/>'
            marker = (f"<marker><name>{'★ ' if it.get('star') else ''}Chhanni {it['score']}</name><comment>score {it['score']}</comment>"
                      f"<in>{src_in}</in><out>-1</out></marker>")
            links = (f"<link><linkclipref>v{cid}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>{len(vclips)+1}</clipindex></link>"
                     + (f"<link><linkclipref>a{cid}</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>{len(aclips)+1}</clipindex></link>"
                        if it.get("has_audio", True) else ""))
            vclips.append(f'<clipitem id="v{cid}"><name>{escape(it["name"])}</name><enabled>TRUE</enabled>'
                          f"<duration>{src_dur}</duration>{_rate_xml(fps)}<start>{t}</start><end>{t+length}</end>"
                          f"<in>{src_in}</in><out>{src_out}</out>{file_xml}"
                          f"<labels><label2>{label}</label2></labels>{marker}{links}</clipitem>")
            if it.get("has_audio", True):
                aclips.append(f'<clipitem id="a{cid}"><name>{escape(it["name"])}</name><enabled>TRUE</enabled>'
                              f"<duration>{src_dur}</duration>{_rate_xml(fps)}<start>{t}</start><end>{t+length}</end>"
                              f'<in>{src_in}</in><out>{src_out}</out><file id="{fid}"/>'
                              f"<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>"
                              f"<labels><label2>{label}</label2></labels>{links}</clipitem>")
            t += length
        out.append(f'<sequence id="seq-{si+1}"><name>{escape(seq["name"])}</name><duration>{t}</duration>'
                   f"{_rate_xml(seq_fps)}<timecode>{_rate_xml(seq_fps)}<string>00:00:00:00</string><frame>0</frame>"
                   f"<displayformat>NDF</displayformat></timecode><media><video><format><samplecharacteristics>"
                   f"{_rate_xml(seq_fps)}<width>{w}</width><height>{h}</height><pixelaspectratio>square</pixelaspectratio>"
                   f"</samplecharacteristics></format><track>{''.join(vclips)}</track></video>"
                   f"<audio><track>{''.join(aclips)}</track></audio></media></sequence>")
    out.append("</children></project></xmeml>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path


# ---------------------------------------------------------------- multi-track versions -> FCP7 XML
def export_version_xml(version, path, seq_fps=None):
    """version items: path,name,start,end,rec,fps,track V1/V2/A1,media av/v/a"""
    items = version["items"]
    vids = [it for it in items if it["media"] in ("av", "v")]
    if not seq_fps:
        votes = {}
        for it in vids:
            if it.get("fps") and float(it["fps"]) <= 61:
                votes[round(float(it["fps"]), 3)] = votes.get(round(float(it["fps"]), 3), 0) + 1
        seq_fps = max(votes, key=votes.get) if votes else 25.0
    first = vids[0] if vids else {}
    w, h = first.get("width") or 1920, first.get("height") or 1080
    files, vtracks, atracks = {}, {}, {}
    total = 0
    for n, it in enumerate(items):
        fps = float(it.get("fps") or seq_fps)
        fid = files.get(it["path"])
        new = fid is None
        if new:
            fid = files[it["path"]] = f"file-{len(files)+1}"
        src_in = int(round(it["start"] * fps))
        src_out = max(src_in + 1, int(round(it["end"] * fps)))
        rec0 = int(round(it["rec"] * seq_fps))
        rec1 = rec0 + max(1, int(round((it["end"] - it["start"]) * seq_fps)))
        total = max(total, rec1)
        dur = int(round(max(it["end"], it.get("duration") or 0) * fps)) + 1
        has_v = it["media"] in ("av", "v")
        has_a = it["media"] in ("av", "a")
        media = ""
        if has_v:
            media += "<video><samplecharacteristics><width>1920</width><height>1080</height></samplecharacteristics></video>"
        media += "<audio><channelcount>2</channelcount></audio>"
        fx = (f'<file id="{fid}"><name>{escape(it["name"])}</name><pathurl>{escape(_pathurl(it["path"]))}</pathurl>'
              f"{_rate_xml(fps)}<duration>{dur}</duration><media>{media}</media></file>") if new else f'<file id="{fid}"/>'
        label = PREMIERE_LABELS[grade(it.get("score") or 0)] if it.get("score") else "Iris"
        common = (f"<duration>{dur}</duration>{_rate_xml(fps)}<start>{rec0}</start><end>{rec1}</end>"
                  f"<in>{src_in}</in><out>{src_out}</out>")
        if has_v:
            vt = int(it["track"][1:]) if it["track"].startswith("V") else 1
            vtracks.setdefault(vt, []).append(
                f'<clipitem id="v{n}"><name>{escape(it["name"])}</name><enabled>TRUE</enabled>{common}{fx}'
                f"<labels><label2>{label}</label2></labels></clipitem>")
            fx = f'<file id="{fid}"/>'
        if has_a:
            at = int(it["track"][1:]) if it["track"].startswith("A") else vt
            atracks.setdefault(at, []).append(
                f'<clipitem id="a{n}"><name>{escape(it["name"])}</name><enabled>TRUE</enabled>{common}{fx}'
                f"<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
    vt_xml = "".join(f"<track>{''.join(vtracks.get(i, []))}</track>" for i in range(1, max(vtracks or {1: 0}) + 1))
    at_xml = "".join(f"<track>{''.join(atracks.get(i, []))}</track>" for i in range(1, max(atracks or {1: 0}) + 1))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq-1">'
           f"<name>{escape(version['name'])}</name><duration>{total}</duration>{_rate_xml(seq_fps)}"
           f"<timecode>{_rate_xml(seq_fps)}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
           f"<media><video><format><samplecharacteristics>{_rate_xml(seq_fps)}<width>{w}</width><height>{h}</height>"
           f"<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>{vt_xml}</video>"
           f"<audio>{at_xml}</audio></media></sequence></xmeml>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    return path


def list_versions(out_dir):
    from pathlib import Path
    vd = Path(resolve_out_dir(out_dir)) / "versions"
    out = []
    for p in sorted(vd.glob("*.json"), key=lambda p: -p.stat().st_mtime) if vd.exists() else []:
        try:
            v = json.load(open(p, encoding="utf-8"))
            d = {k: v.get(k) for k in ("id", "name", "mode", "duration", "created", "batch", "notes", "music", "root",
                                       "parent", "gen", "instruction", "aspect")}
            d["n_items"] = len(v.get("items", []))
            out.append(d)
        except Exception:
            pass
    return out


def load_version(out_dir, vid):
    with open(os.path.join(resolve_out_dir(out_dir), "versions", f"{vid}.json"), encoding="utf-8") as f:
        return json.load(f)
