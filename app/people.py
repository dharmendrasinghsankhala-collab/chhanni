"""
People: groups every detected face (SFace identity embeddings from deep.py) into persons.
Names you give in the app are kept across re-clustering (matched by face centroid).
<project>/deep/people.json = {"people": [{id, name, count, centroid_idx, sample:{kf, box}, clips:[...]}],
                              "faces": {"<clip_id>/<seg>/<face>": person_id}}
"""
import json
from pathlib import Path

import numpy as np

import engine

SAME = 0.40      # cosine similarity for "same person" (SFace recommends 0.363)


def log(*a):
    engine.log(*a)


def cluster(out_dir, log=log):
    import deep
    out_dir = Path(out_dir)
    pf = out_dir / "deep" / "people.json"
    old = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    old_people = old.get("people", [])
    old_cent = {p["id"]: np.array(p["centroid"], np.float32) for p in old_people if p.get("centroid")}
    names = {p["id"]: p.get("name") for p in old_people if p.get("name")}
    refs, embs = [], []
    for j, z in deep.load_all(out_dir):
        F = z["face"]
        for si, s in enumerate(j["segments"]):
            for fi, f in enumerate(s.get("faces") or []):
                if f.get("emb") is not None and f["emb"] < len(F):
                    refs.append((j["clip_id"], si, fi, s.get("kf"), f["box"], s.get("beauty") or 0, f.get("score", 0)))
                    embs.append(F[f["emb"]])
    if not embs:
        pf.parent.mkdir(exist_ok=True)
        pf.write_text(json.dumps({"people": old_people, "faces": {}}, indent=1), encoding="utf-8")
        log("[people] no faces found")
        return
    X = np.stack(embs).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    # big faces first -> better seeds
    order = sorted(range(len(refs)), key=lambda i: -(refs[i][4][3] * (0.5 + refs[i][6])))
    cents, members = [], []
    for i in order:
        x = X[i]
        if cents:
            sims = np.array([c @ x for c in cents])
            k = int(sims.argmax())
            if sims[k] >= SAME:
                members[k].append(i)
                c = X[members[k]].mean(0)
                cents[k] = c / (np.linalg.norm(c) + 1e-9)
                continue
        cents.append(x.copy())
        members.append([i])
    # keep recurring people only (>=3 sightings), keep ids/names stable
    people, faces, used_old = [], {}, set()
    for k in sorted(range(len(cents)), key=lambda k: -len(members[k])):
        if len(members[k]) < 3:
            continue
        pid = None
        best = 0.0
        for oid, oc in old_cent.items():
            if oid in used_old:
                continue
            s = float(oc @ cents[k])
            if s > best and s >= SAME:
                pid, best = oid, s
        if pid is None:
            pid = f"p{len(people) + 1:03d}"
            while pid in old_cent or any(p["id"] == pid for p in people):
                pid = "p%03d" % (int(pid[1:]) + 1)
        used_old.add(pid)
        ms = members[k]
        best_i = max(ms, key=lambda i: refs[i][4][3] * 2 + refs[i][5] / 100)
        r = refs[best_i]
        clips = sorted({refs[i][0] for i in ms})
        people.append({"id": pid, "name": names.get(pid, ""), "count": len(ms), "clips": clips,
                       "sample": {"clip": r[0], "seg": r[1], "kf": r[3], "box": r[4]},
                       "centroid": [round(float(v), 5) for v in cents[k]]})
        for i in ms:
            faces[f"{refs[i][0]}/{refs[i][1]}/{refs[i][2]}"] = pid
    pf.write_text(json.dumps({"people": people, "faces": faces}, indent=1), encoding="utf-8")
    log(f"[people] {len(embs)} faces -> {len(people)} recurring people")


def load(out_dir):
    pf = Path(out_dir) / "deep" / "people.json"
    return json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {"people": [], "faces": {}}


def rename(out_dir, pid, name):
    d = load(out_dir)
    for p in d["people"]:
        if p["id"] == pid:
            p["name"] = name.strip()
    (Path(out_dir) / "deep" / "people.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
    return d
