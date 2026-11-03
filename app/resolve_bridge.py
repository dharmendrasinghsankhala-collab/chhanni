"""
Chhanni -> DaVinci Resolve. Used by the Resolve panel (inside Resolve) and by the app ("Send to Resolve").
Standard library + Resolve scripting API only.
"""
import datetime
import json
import time
import os

try:
    from . import plan  # package import (app)
except ImportError:      # plain import (Resolve panel adds the app folder to sys.path)
    import plan

CHUNK = 100


def norm(p):
    return os.path.normcase(os.path.normpath(p or ""))


def _unique_sub(mp, parent, name):
    names = {f.GetName() for f in (parent.GetSubFolderList() or [])}
    n, k = name, 2
    while n in names:
        n, k = f"{name} ({k})", k + 1
    return mp.AddSubFolder(parent, n)


def _timeline_names(project):
    names = set()
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            names.add(tl.GetName())
    return names


def _clip_fps(item, fallback):
    try:
        v = float(item.GetClipProperty("FPS") or 0)
        return v if v > 0 else fallback
    except Exception:
        return fallback


def _create_tl(mp, name, log=print):
    """CreateEmptyTimeline can fail while Resolve shows a dialog (e.g. frame-rate prompt) - retry a few times."""
    for i in range(6):
        tl = mp.CreateEmptyTimeline(name)
        if tl:
            return tl
        time.sleep(1.0)
    log(f"[warn] timeline '{name}' nahi bani - Resolve mein koi dialog khula ho to band karke dobara chalaiye")
    return None


def get_resolve():
    """External scripting (Resolve Preferences > System > General > External scripting using = Local)."""
    import sys
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    api = os.path.join(pd, "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting")
    lib = os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Blackmagic Design", "DaVinci Resolve",
                       "fusionscript.dll")
    os.environ.setdefault("RESOLVE_SCRIPT_API", api)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib)
    mods = os.path.join(api, "Modules")
    if os.path.isdir(mods) and mods not in sys.path:
        sys.path.append(mods)
    import DaVinciResolveScript  # noqa: F401  (replaces itself with fusionscript in sys.modules)
    dvr = sys.modules.get("DaVinciResolveScript")
    if not hasattr(dvr, "scriptapp"):
        raise RuntimeError("Resolve scripting library load nahi hui. Workspace > Scripts > Chhanni panel use kijiye.")
    r = dvr.scriptapp("Resolve")
    if not r:
        raise RuntimeError("Resolve se connect nahi hua: Resolve kholiye aur Preferences > System > General > "
                           "'External scripting using' = Local kijiye. Ya Workspace > Scripts > Chhanni panel use kijiye.")
    return r


def import_project(resolve, out_dir, group_by="group", include=("A", "B"), slowmo_hfr=True,
                   markers=True, log=print):
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("Resolve mein pehle ek project kholiye.")
    data, review = plan.load_project(out_dir)
    clips = plan.effective_clips(data, review)
    seqs = plan.build_sequences(clips, group_by=group_by, include=include)
    try:
        tl_fps = float(project.GetSetting("timelineFrameRate") or 0)
    except Exception:
        tl_fps = 0
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
    proj_name = os.path.basename(plan.resolve_out_dir(out_dir).rstrip("\\/")) or "Chhanni"
    main = _unique_sub(mp, root, f"Chhanni - {proj_name} - {stamp}")
    bins = {}

    def bin_for(key):
        if key not in bins:
            bins[key] = mp.AddSubFolder(main, key)
        return bins[key]

    usable = [c for c in clips if not c.get("missing")]
    missing = len(clips) - len(usable)
    by_bin = {}
    for c in usable:
        if c["type"] not in include and c["selects"]:
            continue
        key = "No usable" if not c["selects"] else ("A-roll" if c["type"] == "A" else "B-roll")
        by_bin.setdefault(key, []).append(c)

    items = {}
    for key in ("A-roll", "B-roll", "No usable"):
        group = by_bin.get(key, [])
        if not group:
            continue
        mp.SetCurrentFolder(bin_for(key))
        for i in range(0, len(group), CHUNK):
            got = mp.ImportMedia([c["path"] for c in group[i:i + CHUNK]]) or []
            for it in got:
                items[norm(it.GetClipProperty("File Path"))] = it
        log(f"[import] {key}: {len(group)} clips")

    src_fps = {}
    slowed = 0
    for c in usable:
        it = items.get(norm(c["path"]))
        if not it:
            continue
        fps = _clip_fps(it, c["fps"])
        src_fps[norm(c["path"])] = fps
        talking = c["type"] == "A" or any(s.get("text") for s in c["selects"]) or c.get("stats", {}).get("speech_ratio", 0) > 0.3
        if slowmo_hfr and tl_fps and fps > tl_fps * 1.9 and not talking:
            # S&Q / high frame rate -> conform to timeline fps = real slow motion
            if it.SetClipProperty("FPS", str(int(tl_fps) if float(tl_fps).is_integer() else tl_fps)):
                slowed += 1
        if not c["selects"]:
            it.SetClipColor(plan.RESOLVE_COLORS["none"])
            continue
        best = max(s["score"] for s in c["selects"])
        it.SetClipColor(plan.RESOLVE_COLORS[plan.grade(best)])
        if markers:
            for k, s in enumerate(c["selects"]):
                f0 = int(round(s["start"] * fps))
                dur = max(1, int(round((s["end"] - s["start"]) * fps)))
                it.AddMarker(f0, "Cream" if s.get("star") else "Blue",
                             f"{'★ ' if s.get('star') else ''}Select {k+1} · {s['score']}",
                             f"Chhanni · shake {s.get('shake')} · sharp {s.get('sharp_rel')}", dur)
    if slowed:
        log(f"[slow-mo] {slowed} high-fps clips conformed to {tl_fps} fps")

    names = _timeline_names(project)
    made = []
    for seq in seqs:
        name, k = seq["name"], 2
        while name in names:
            name, k = f"{seq['name']} ({k})", k + 1
        names.add(name)
        mp.SetCurrentFolder(main)
        tl = _create_tl(mp, name, log)
        if not tl:
            continue
        project.SetCurrentTimeline(tl)
        infos, meta = [], []
        for x in seq["items"]:
            it = items.get(norm(x["path"]))
            if not it:
                continue
            fps = src_fps.get(norm(x["path"]), x["fps"])
            s0 = int(round(x["start"] * fps))
            s1 = max(s0 + 1, int(round(x["end"] * fps)) - 1)
            infos.append({"mediaPoolItem": it, "startFrame": s0, "endFrame": s1})
            meta.append(x)
        n_ok = 0
        for i in range(0, len(infos), CHUNK):
            res = mp.AppendToTimeline(infos[i:i + CHUNK]) or []
            vids = [r for r in res if r and r.GetTrackTypeAndIndex()[0] == "video"]
            for j, r in enumerate(vids):
                x = meta[i + j] if i + j < len(meta) else None
                if x:
                    r.SetClipColor(plan.RESOLVE_COLORS[plan.grade(x["score"])])
                    if x.get("star"):
                        r.AddMarker(0, "Cream", "★ Favourite", "Chhanni", 1)
                n_ok += 1
        made.append({"name": name, "selects": n_ok})
        log(f"[timeline] {name}: {n_ok} selects")
    summary = {"bin": main.GetName(), "timelines": made, "clips_imported": len(items),
               "missing_files": missing, "slowmo_conformed": slowed}
    log("[done] " + str(summary))
    return summary


# ---------------------------------------------------------------- v3: multi-track versions
def _tc(frame, fps):
    n = int(round(fps))
    f = int(frame)
    sec = f // n
    return "%02d:%02d:%02d:%02d" % (sec // 3600, (sec // 60) % 60, sec % 60, f % n)


TITLE_STYLE = {"hook": {"Center": {1: 0.5, 2: 0.78}, "Size": 0.085},
               "lower_third": {"Center": {1: 0.5, 2: 0.22}, "Size": 0.055},
               "cta": {"Center": {1: 0.5, 2: 0.5}, "Size": 0.075}}


def _timeline_map(out_dir):
    p = os.path.join(plan.resolve_out_dir(out_dir), "resolve_timelines.json")
    try:
        with open(p, encoding="utf-8") as f:
            return p, json.load(f)
    except Exception:
        return p, {}


def _clip_light(out_dir):
    try:
        data, _ = plan.load_project(out_dir)
    except Exception:
        return {}
    out = {}
    for c in data.get("clips", []):
        ls = [s["light"] for s in c.get("selects", []) if s.get("light")]
        if ls:
            out[norm(c["path"])] = (sum(x["luma"] for x in ls) / len(ls), sum(x["warm"] for x in ls) / len(ls))
    return out


def _cdl(luma, warm):
    g = max(0.85, min(1.2, (115.0 / max(20.0, luma)) ** 0.6))
    rf = bf = 1.0
    if warm > 25:
        rf, bf = 0.96, 1.04
    elif warm < -12:
        rf, bf = 1.04, 0.96
    return {"NodeIndex": "1", "Slope": f"{g*rf:.3f} {g:.3f} {g*bf:.3f}", "Offset": "0 0 0", "Power": "1 1 1",
            "Saturation": "1.0"}


def import_version(resolve, out_dir, version, slowmo_hfr=True, log=print, replace=True):
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("Resolve mein pehle ek project kholiye.")
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    try:
        tl_fps = float(project.GetSetting("timelineFrameRate") or 25)
    except Exception:
        tl_fps = 25.0
    proj_name = os.path.basename(plan.resolve_out_dir(out_dir).rstrip("\\/")) or "Chhanni"
    holder = None
    for f in root.GetSubFolderList() or []:
        if f.GetName() == f"Chhanni - {proj_name} - Versions":
            holder = f
    holder = holder or mp.AddSubFolder(root, f"Chhanni - {proj_name} - Versions")
    mp.SetCurrentFolder(holder)
    items = {}
    for it in holder.GetClipList() or []:
        items[norm(it.GetClipProperty("File Path"))] = it
    paths = sorted({x["path"] for x in version["items"]} - set(items))
    for i in range(0, len(paths), CHUNK):
        for it in mp.ImportMedia(paths[i:i + CHUNK]) or []:
            items[norm(it.GetClipProperty("File Path"))] = it
    src_fps = {}
    talking = {norm(x["path"]) for x in version["items"] if x["media"] in ("av", "a") or x.get("label") == "dialogue"}
    for p, it in items.items():
        f = _clip_fps(it, 0)
        src_fps[p] = f
        if slowmo_hfr and f and f > tl_fps * 1.9 and p not in talking and it.GetClipProperty("Type") != "Audio":
            it.SetClipProperty("FPS", str(int(tl_fps) if float(tl_fps).is_integer() else tl_fps))
    # ---- replace: remove the previous timeline(s) of this version family in this Resolve project
    map_path, tmap = _timeline_map(out_dir)
    pkey = project.GetName()
    fam = version.get("root") or version["id"]
    old_names = set(tmap.get(pkey, {}).get(fam, []))
    names = _timeline_names(project)
    if replace:
        old_names.add(version["name"])
        dead = []
        for i in range(1, int(project.GetTimelineCount() or 0) + 1):
            t = project.GetTimelineByIndex(i)
            if t and t.GetName() in old_names:
                dead.append(t)
        if dead:
            mp.DeleteTimelines(dead)
            log(f"[replace] {len(dead)} purani timeline hatai: {', '.join(sorted(old_names & names))}")
        names = _timeline_names(project)
    name, k = version["name"], 2
    while name in names:
        name, k = f"{version['name']} ({k})", k + 1
    tl = _create_tl(mp, name, log)
    if not tl:
        raise RuntimeError("timeline create failed")
    project.SetCurrentTimeline(tl)
    res = version.get("resolution")
    if res:
        tl.SetSetting("useCustomSettings", "1")
        tl.SetSetting("timelineResolutionWidth", str(res[0]))
        tl.SetSetting("timelineResolutionHeight", str(res[1]))
        tl.SetSetting("timelineInputResMismatchBehavior", "scaleToCrop")
    need_v = max([int(x["track"][1:]) for x in version["items"] if x["track"].startswith("V")] or [1])
    title_track = need_v + 1 if version.get("titles") else None
    need_a = max([int(x["track"][1:]) for x in version["items"] if x["track"].startswith("A")] or [1])
    has_av = any(x["media"] == "av" for x in version["items"])
    music_track = max(need_a, 2 if has_av and any(x.get("label") == "music" for x in version["items"]) else 1)
    while tl.GetTrackCount("video") < (title_track or need_v):
        tl.AddTrack("video")
    while tl.GetTrackCount("audio") < music_track:
        tl.AddTrack("audio", "stereo")
    start = tl.GetStartFrame()
    # ---- 1. captions first (Resolve appends subtitles at the end of the timeline, so the timeline must be empty)
    if version.get("captions"):
        try:
            import reel
            ed = os.path.join(plan.resolve_out_dir(out_dir), "exports")
            os.makedirs(ed, exist_ok=True)
            srt_path = os.path.join(ed, f"{version['id']} captions.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(reel.srt(version["captions"]))
            sub = (mp.ImportMedia([srt_path]) or [None])[0]
            if sub:
                if tl.GetTrackCount("subtitle") < 1:
                    tl.AddTrack("subtitle")
                mp.AppendToTimeline([{"mediaPoolItem": sub, "trackIndex": 1}])
                log(f"[captions] {len(version['captions'])} captions subtitle track par")
        except Exception as e:
            log(f"[captions] skipped: {e}")
    # ---- 2. titles (inserted on the empty V1 at the playhead, then moved to the title track)
    for t in version.get("titles") or []:
        try:
            f0 = start + int(round(t["rec"] * tl_fps))
            tl.SetCurrentTimecode(_tc(f0, tl_fps))
            ti = tl.InsertFusionTitleIntoTimeline("Text+")
            if not ti:
                continue
            ti.SetPosition({"trackIndex": title_track, "startFrame": f0})
            want = max(1, int(round(t["dur"] * tl_fps)))
            if ti.GetDuration() > want:
                ti.SetRightOffset(ti.GetDuration() - want)
            comp = ti.GetFusionCompByIndex(1)
            tool = comp.FindTool("Template") if comp else None
            if tool:
                tool.SetInput("StyledText", t["text"])
                for k_, v_ in TITLE_STYLE.get(t["kind"], {}).items():
                    try:
                        tool.SetInput(k_, v_)
                    except Exception:
                        pass
            log(f"[title] {t['kind']}: {t['text'][:40]}")
        except Exception as e:
            log(f"[title] skipped: {e}")
    # ---- 3. clips
    light = _clip_light(out_dir) if version.get("color_balance") else {}
    n_ok = 0
    for x in sorted(version["items"], key=lambda x: (x["track"], x["rec"])):
        it = items.get(norm(x["path"]))
        if not it:
            log(f"[warn] missing {x['name']}")
            continue
        fps = src_fps.get(norm(x["path"])) or float(x.get("fps") or tl_fps)
        info = {"mediaPoolItem": it, "recordFrame": start + int(round(x["rec"] * tl_fps))}
        if x["media"] == "a":
            sr_fps = fps or tl_fps
            a_idx = int(x["track"][1:]) if x["track"].startswith("A") else 1
            if x.get("label") == "music" and a_idx == 1 and has_av:
                a_idx = 2
            while tl.GetTrackCount("audio") < a_idx:
                tl.AddTrack("audio", "stereo")
            info.update(startFrame=int(round(x["start"] * sr_fps)), endFrame=int(round(x["end"] * sr_fps)) - 1,
                        mediaType=2, trackIndex=a_idx)
        else:
            info.update(startFrame=int(round(x["start"] * fps)), endFrame=max(int(round(x["start"] * fps)) + 1, int(round(x["end"] * fps)) - 1),
                        trackIndex=int(x["track"][1:]) if x["track"].startswith("V") else 1)
            if x["media"] == "v":
                info["mediaType"] = 1
        res_items = mp.AppendToTimeline([info]) or []
        for r in res_items:
            if not r:
                continue
            kind = r.GetTrackTypeAndIndex()[0]
            if kind == "video":
                if x.get("score"):
                    r.SetClipColor(plan.RESOLVE_COLORS[plan.grade(x["score"])])
                if x.get("zoom"):
                    r.SetProperty("ZoomX", float(x["zoom"]))
                    r.SetProperty("ZoomY", float(x["zoom"]))
                if light.get(norm(x["path"])):
                    try:
                        r.SetCDL(_cdl(*light[norm(x["path"])]))
                    except Exception:
                        pass
                if version.get("smart_reframe"):
                    try:
                        r.SmartReframe()
                    except Exception:
                        pass
            elif kind == "audio" and x.get("volume") is not None:
                r.SetProperty("AudioVolume", float(x["volume"]))
        n_ok += 1 if res_items else 0
    # ---- 4. markers
    if version.get("markers", True):
        for row in version.get("beats", []):
            if row.get("rec") is not None and row.get("text") and row.get("kind") != "section":
                try:
                    tl.AddMarker(int(round(row["rec"] * tl_fps)), "Red" if row.get("missing") else "Sand",
                                 (row["text"] or "")[:60], ("MISSING - VO / pickup needed. " if row.get("missing") else "") + row["text"][:300], 1)
                except Exception:
                    pass
    tmap.setdefault(pkey, {}).setdefault(fam, [])
    if name not in tmap[pkey][fam]:
        tmap[pkey][fam].append(name)
    if replace:
        tmap[pkey][fam] = [name]
    try:
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(tmap, f, indent=1, ensure_ascii=False)
    except Exception:
        pass
    log(f"[version] {name}: {n_ok}/{len(version['items'])} items")
    return {"timeline": name, "items": n_ok}


def smart_bins(resolve, out_dir, log=print):
    """Keyword metadata on every clip + string-out timelines that act as smart collections."""
    project = resolve.GetProjectManager().GetCurrentProject()
    data, review = plan.load_project(out_dir)
    clips = [c for c in plan.effective_clips(data, review) if not c.get("missing")]
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    proj_name = os.path.basename(plan.resolve_out_dir(out_dir).rstrip("\\/")) or "Chhanni"
    main = _unique_sub(mp, root, f"Chhanni - {proj_name} - Smart bins")
    SHOT_BIN = {"CU": "Close-ups", "MCU": "Close-ups", "MS": "Mediums", "WS": "Wides", "WIDE": "Wides"}
    bins = {}

    def b(name):
        if name not in bins:
            bins[name] = mp.AddSubFolder(main, name)
        return bins[name]
    items = {}
    for c in clips:
        if c["type"] == "A":
            key = "Interviews & dialogue"
        elif not c["selects"]:
            key = "No usable"
        elif any(s.get("move") == "Drone / aerial" for s in c["selects"]):
            key = "Drone & aerial"
        else:
            shots = [s.get("shot") for s in c["selects"] if s.get("shot")]
            key = "B-roll - " + (SHOT_BIN.get(max(set(shots), key=shots.count), "Wides") if shots else "Wides")
        mp.SetCurrentFolder(b(key))
        got = mp.ImportMedia([c["path"]]) or []
        if got:
            it = got[0]
            items[c["id"]] = it
            kw = sorted({t for s in c["selects"] for t in s.get("tags", [])} | {c.get("group") or ""} - {""})
            desc = " | ".join((s.get("text") or "")[:120] for s in c["selects"] if s.get("text"))[:900]
            it.SetMetadata({"Keywords": ", ".join(kw), "Description": desc, "Comments": f"Chhanni: {key}",
                            "Shot": ",".join(sorted({s.get('shot') or '' for s in c['selects']} - {''}))})
            best = max([s["score"] for s in c["selects"]], default=0)
            it.SetClipColor(plan.RESOLVE_COLORS[plan.grade(best)] if c["selects"] else plan.RESOLVE_COLORS["none"])
    collections = {"★ Hero shots": lambda s: s.get("is_hero"), "Golden hour": lambda s: "Golden hour" in s.get("tags", []),
                   "Smooth moves (slider-gimbal-drone)": lambda s: s.get("move") in ("Slider / gimbal move", "Drone / aerial", "Smooth pan", "Smooth tilt / crane"),
                   "Close-ups": lambda s: s.get("shot") in ("CU", "MCU")}
    made = []
    names = _timeline_names(project)
    for title, pred in collections.items():
        infos = []
        for c in clips:
            it = items.get(c["id"])
            if not it or c["type"] == "A":
                continue
            fps = _clip_fps(it, c["fps"])
            for s in c["selects"]:
                if pred(s):
                    infos.append({"mediaPoolItem": it, "startFrame": int(s["start"] * fps), "endFrame": int(s["end"] * fps) - 1})
        if not infos:
            continue
        name = f"{title} - {proj_name}"
        if name in names:
            name += time.strftime(" %H%M")
        mp.SetCurrentFolder(main)
        tl = _create_tl(mp, name, log)
        if tl:
            project.SetCurrentTimeline(tl)
            mp.AppendToTimeline(infos)
            made.append((name, len(infos)))
    log(f"[smart bins] {len(items)} clips in {len(bins)} bins, collections: {made}")
    return {"bins": list(bins), "collections": made, "clips": len(items)}
