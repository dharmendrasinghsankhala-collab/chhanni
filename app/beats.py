"""
Music analysis for beat-synced cutting (numpy only).
analyze(path) -> {"tempo", "beats": [sec...], "downbeats": [sec...], "energy": [0..1 per beat], "duration"}
"""
import subprocess

import numpy as np

SR = 22050
HOP = 512


def load(path, ffmpeg, sr=SR):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, creationflags=0x08000000 if __import__("os").name == "nt" else 0)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("music file read nahi hua")
    return np.frombuffer(r.stdout, np.float32)


def onset_envelope(y, sr=SR, n_fft=2048, hop=HOP):
    n = 1 + (len(y) - n_fft) // hop
    if n < 8:
        raise RuntimeError("music bahut chhota hai")
    win = np.hanning(n_fft).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(y, shape=(n, n_fft), strides=(y.strides[0] * hop, y.strides[0]))
    env, rms = np.zeros(n, np.float32), np.zeros(n, np.float32)
    prev = None
    for i0 in range(0, n, 512):
        blk = frames[i0:i0 + 512] * win
        mag = np.log1p(10 * np.abs(np.fft.rfft(blk, axis=1)))
        rms[i0:i0 + 512] = np.sqrt((blk ** 2).mean(axis=1))
        d = np.diff(np.vstack([prev[None] if prev is not None else mag[:1], mag]), axis=0)
        env[i0:i0 + 512] = np.maximum(d, 0).mean(axis=1)
        prev = mag[-1]
    env -= np.convolve(env, np.ones(16) / 16, mode="same")       # local mean removal
    env = np.maximum(env, 0)
    env /= env.max() + 1e-9
    return env, rms


def tempo(env, sr=SR, hop=HOP, lo=70, hi=180):
    fr = sr / hop
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    bpm = 60 * fr / np.maximum(lags, 1)
    ok = (bpm >= lo) & (bpm <= hi)
    # prefer ~120 bpm a little (log-gaussian), avoids half/double confusion
    w = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1) / 120) / 0.9) ** 2)
    score = np.where(ok, ac * w, 0)
    lag = int(np.argmax(score))
    return 60 * fr / lag, lag


def track(env, lag, tightness=100):
    """Ellis 2007 dynamic-programming beat tracker."""
    n = len(env)
    score = env.astype(np.float64).copy()
    back = -np.ones(n, int)
    lo, hi = int(round(lag / 2)), int(round(lag * 2))
    for t in range(n):
        a, b = max(0, t - hi), t - lo
        if b <= a:
            continue
        prev = np.arange(a, b)
        pen = -tightness * (np.log((t - prev) / lag)) ** 2
        cand = score[prev] + pen
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] += cand[k]
            back[t] = prev[k]
    t = int(np.argmax(score[max(0, n - lag * 2):]) + max(0, n - lag * 2))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1])


def analyze(path, ffmpeg):
    y = load(path, ffmpeg)
    env, rms = onset_envelope(y)
    bpm, lag = tempo(env)
    bf = track(env, lag)
    fr = SR / HOP
    times = bf / fr + (2048 - HOP) / SR     # frame index -> onset time (window latency)
    # energy per beat (rms between beats), normalised
    e = []
    for i, f in enumerate(bf):
        g = bf[i + 1] if i + 1 < len(bf) else min(len(rms), f + lag)
        e.append(float(rms[f:max(f + 1, g)].mean()))
    e = np.asarray(e)
    if len(e):
        e = np.convolve(e, np.ones(4) / 4, mode="same")
        e = (e - np.percentile(e, 5)) / (np.percentile(e, 95) - np.percentile(e, 5) + 1e-9)
        e = np.clip(e, 0, 1)
    # downbeat phase: the beat phase (0..3) with the strongest onsets
    strength = (env[bf] / (env[bf].max() + 1e-9) + rms[bf] / (rms[bf].max() + 1e-9)) if len(bf) else np.zeros(0)
    phase = int(np.argmax([strength[p::4].mean() if len(strength[p::4]) else 0 for p in range(4)])) if len(bf) >= 8 else 0
    return {"tempo": round(float(bpm), 1), "beats": [round(float(t), 3) for t in times],
            "downbeats": [round(float(t), 3) for t in times[phase::4]], "energy": [round(float(x), 3) for x in e],
            "duration": round(len(y) / SR, 2)}
