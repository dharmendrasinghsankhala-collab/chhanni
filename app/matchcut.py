"""Chhanni - re-cut close-camera (V2) shots to a target length with match-on-action cut points.

usage:  python matchcut.py <job.json> <out.json>

job.json = {
  "fps": 29.97, "min": 2.0, "max": 4.0, "gap": [1.2, 2.6],
  "beats": [timeline seconds ...]            # optional, record-timeline seconds of strong beats
  "shots": [ {"id": 0, "rec0": f, "rec1": f,  # record frames (timeline) of the V2 clip
              "b": {"path": p, "in": f},     # close camera source + source frame at rec0
              "a": [{"path": p, "rec0": f, "rec1": f, "in": f}, ...]   # V1 wide pieces under it
             }, ... ]
}
out.json = {"shots": [{"id": 0, "pieces": [[rec0, rec1], ...]}]}   # V2 pieces to keep (record frames)

House style (knowledge/editing.md): V2 shots 2-4 s, wide in between, cut while the dancer is moving in BOTH
angles (match on action), then snap to a beat within +-4 frames. Never cut on a still hold unless it is on a beat.
"""
import json
import subprocess
import sys

import numpy as np

NOWIN = 0x08000000 if sys.platform == "win32" else 0
W, H = 160, 90


def motion(path, src0, n, fps):
    """per-frame motion energy for n frames starting at source frame src0 (low-res frame differencing)."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{src0 / fps:.3f}", "-i", path, "-frames:v", str(n + 1), "-an",
           "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=NOWIN).stdout
    fr = np.frombuffer(raw[:len(raw) // (W * H) * W * H], np.uint8).reshape(-1, H, W).astype(np.float32)
    if len(fr) < 2:
        return np.zeros(n)
    d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    # remove camera pans: global shift shows up as uniform change; keep the local (dancer) part
    d = d - np.convolve(d, np.ones(31) / 31, "same") * 0.5
    d = np.clip(d, 0, None)
    out = np.zeros(n)
    out[:min(n, len(d))] = d[:n]
    return out


def norm(x):
    s = np.percentile(x, 95) if len(x) else 0
    return x / s if s > 0 else x


def main(job_p, out_p):
    J = json.load(open(job_p, encoding="utf-8"))
    fps = float(J.get("fps", 30000 / 1001))
    mn, mx = int(J.get("min", 2.0) * fps), int(J.get("max", 4.0) * fps)
    g0, g1 = [int(x * fps) for x in J.get("gap", [1.2, 2.6])]
    beats = np.array(sorted(J.get("beats", [])))
    res = []
    for s in J["shots"]:
        r0, r1 = int(s["rec0"]), int(s["rec1"])
        n = r1 - r0
        if n <= mx + 6:                      # already short: only nudge the out point onto action
            res.append({"id": s["id"], "pieces": [[r0, r1]]})
            continue
        mb = norm(motion(s["b"]["path"], s["b"]["in"], n, fps))
        ma = np.zeros(n)
        for a in s.get("a", []):
            lo, hi = max(r0, a["rec0"]), min(r1, a["rec1"])
            if hi > lo:
                ma[lo - r0:hi - r0] = motion(a["path"], a["in"] + (lo - a["rec0"]), hi - lo, fps)
        ma = norm(ma)
        act = np.sqrt(np.clip(mb, 0, 3) * np.clip(ma, 0, 3))     # moving in both angles = match-on-action
        act = np.convolve(act, np.ones(3) / 3, "same")
        beat_bonus = np.zeros(n)
        for b in beats:
            k = int(round(b * fps)) - r0
            if 0 <= k < n:
                beat_bonus[max(0, k - 4):k + 5] = 0.35
        score = act + beat_bonus

        def best(lo, hi):
            lo, hi = max(lo, 0), min(hi, n - 1)
            if hi <= lo:
                return hi
            return lo + int(np.argmax(score[lo:hi + 1]))
        pieces, t = [], 0
        while t < n - mn:
            e = best(t + mn, t + mx)
            pieces.append([r0 + t, r0 + e])
            nxt = best(e + g0, e + g1)            # wide shot in between, cut back in on action too
            if nxt >= n - mn:
                break
            t = nxt
        res.append({"id": s["id"], "pieces": pieces})
    json.dump({"shots": res}, open(out_p, "w"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
