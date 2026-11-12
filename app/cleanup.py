"""
Storage & cleanup: delete old Chhanni data (versions, scripts, caches ...) per project.
Only deletes files/folders that Chhanni itself creates inside the project folder (whitelist).
Footage is never touched - it lives elsewhere and is only linked.
"""
import json
import os
import shutil
from pathlib import Path

# id, label, hint, entries inside the project folder
CATEGORIES = [
    ("versions", "Timeline versions", "Assemble / AI Team se bani saari versions", ["versions"]),
    ("script", "Script", "Script & assemble mein likhi script", ["script.txt"]),
    ("crew", "AI Team notes", "Concept, review, sawaal-jawab", ["crew"]),
    ("exports", "XML exports", "FCP7 XML files", ["exports"]),
    ("enhanced", "Enhanced audio", "Reel ke liye saaf kiya gaya audio", ["enhanced"]),
    ("transcripts", "Transcripts", "Whisper transcript - dobara banane mein samay lagta hai", ["transcripts"]),
    ("deep", "Deep analysis", "Tags, VLM descriptions, people / faces - dobara chalana padega", ["deep", "clip_emb.npz"]),
    ("cache", "Analysis cache + thumbnails", "Sabse bada. Hatane ke baad settings badalne par poora Analyze dobara", ["cache", "thumbs"]),
]
CORE_FILES = ["project.json", "selects.json", "review.json", "chhanni.db", "chhanni.db-journal", "chhanni.db-wal",
              "chhanni.db-shm", "roots.json", "config.json", "report.html", ".stop"]


def _inside(base, p):
    """True if p (not following p itself if it is a link) is strictly inside base."""
    b = os.path.normcase(os.path.realpath(base))
    q = os.path.normcase(os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(p))), os.path.basename(p)))
    try:
        return q != b and os.path.commonpath([b, q]) == b
    except ValueError:
        return False


def _size(p):
    p = Path(p)
    if p.is_symlink():
        return 0
    if p.is_file():
        return p.stat().st_size
    tot = 0
    for root, dirs, files in os.walk(p):
        for f in files:
            try:
                tot += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return tot


def _count(p):
    p = Path(p)
    if p.is_file():
        return 1
    if p.is_dir():
        return sum(len(files) for _, _, files in os.walk(p))
    return 0


def _remove(base, p):
    p = Path(p)
    if not p.exists() and not p.is_symlink():
        return 0
    if not _inside(base, p):
        raise RuntimeError(f"Project folder ke bahar: {p}")
    n = _size(p)
    if p.is_symlink() or p.is_file():
        p.unlink()
    else:
        shutil.rmtree(p)
    return n


def usage(out):
    out = Path(out)
    cats = []
    for cid, label, hint, entries in CATEGORIES:
        size = sum(_size(out / e) for e in entries if (out / e).exists())
        count = sum(_count(out / e) for e in entries)
        cats.append({"id": cid, "label": label, "hint": hint, "size": size, "count": count})
    core = sum(_size(out / f) for f in CORE_FILES if (out / f).exists())
    return {"out": str(out), "exists": out.exists(), "categories": cats, "core": core,
            "total": core + sum(c["size"] for c in cats)}


def clean(out, ids):
    out = Path(out)
    known = {c[0]: c[3] for c in CATEGORIES}
    freed, done = 0, []
    for cid in ids:
        if cid not in known:
            raise RuntimeError(f"unknown category {cid}")
        for e in known[cid]:
            freed += _remove(out, out / e)
        done.append(cid)
    return {"ok": True, "freed": freed, "cleaned": done}


def forget(out, app_data_dir):
    pf = Path(app_data_dir) / "projects.json"
    try:
        lst = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return
    lst = [p for p in lst if os.path.normcase(os.path.abspath(p.get("out", ""))) != os.path.normcase(os.path.abspath(out))]
    pf.write_text(json.dumps(lst, indent=1, ensure_ascii=False), encoding="utf-8")
    lp = Path(app_data_dir) / "last_selects.txt"
    try:
        if os.path.normcase(os.path.dirname(lp.read_text(encoding="utf-8").strip())) == os.path.normcase(str(out)):
            lp.unlink()
    except Exception:
        pass


def delete_project(out, app_data_dir):
    """Delete all Chhanni data of a project. The folder itself is removed only if nothing else is left in it."""
    out = Path(out)
    r = clean(out, [c[0] for c in CATEGORIES]) if out.exists() else {"freed": 0}
    freed = r["freed"]
    for f in CORE_FILES:
        freed += _remove(out, out / f)
    removed_dir = False
    try:
        if out.exists() and not any(out.iterdir()):
            out.rmdir()
            removed_dir = True
    except OSError:
        pass
    forget(out, app_data_dir)
    left = [] if removed_dir or not out.exists() else sorted(x.name for x in out.iterdir())[:10]
    return {"ok": True, "freed": freed, "folder_removed": removed_dir, "left": left}
