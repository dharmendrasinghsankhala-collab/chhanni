"""
All long-running jobs, executed inside worker.py (separate process).
  all        : scan + QC analyze + selects (+ thumbs, CLIP tags)
  select     : rebuild selects from cache
  transcribe : Whisper on talking clips, then rebuild selects
  deep       : deep visual + audio intelligence (shots, camera, faces, emotion, VLM, sound) -> deep/*.json
  people     : cluster faces from deep analysis into people
"""
import json
from pathlib import Path

import engine
import llm


def ai_cfg(out):
    cfg = engine.load_config(Path(out))
    st = llm.load_settings()
    for k in ("whisper_model", "language", "use_clip", "dead_air_sec", "vlm_mode", "vlm_model", "deep_audio",
              "deep_faces"):
        if st.get(k) not in (None, ""):
            cfg[k] = st[k]
    return cfg


def run(out, folders, mode, log=print, progress=None):
    out = Path(out)
    prog = progress or (lambda d: None)
    cfg = ai_cfg(out)
    if mode == "transcribe":
        import ai_audio
        import plan
        data, _ = plan.load_project(out)
        hot = None
        sp = out / "script.txt"
        if sp.exists():
            hot = " ".join(sp.read_text(encoding="utf-8").split())[:400]
        ai_audio.transcribe_project(out, data["clips"], engine.FFMPEG, cfg, log=log, progress=prog,
                                    stop=engine.STOP, hotwords=hot)
        if engine.STOP.is_set():
            return "stopped"
    if mode == "all":
        roots = [str(p) for p in engine.normalize_roots(folders)]
        if not roots:
            raise RuntimeError("Koi valid footage folder nahi mila.")
        (out / "roots.json").write_text(json.dumps(roots, ensure_ascii=False), encoding="utf-8")
        if not engine.cmd_analyze(roots, out, cfg, progress=prog):
            return "stopped"
    if mode in ("all", "select", "transcribe"):
        engine.cmd_select(out, cfg, progress=prog)
        return "finished"
    if mode == "deep":
        import deep
        ok = deep.run_project(out, cfg, log=log, progress=prog, stop=engine.STOP)
        if not ok:
            return "stopped"
        import people
        people.cluster(out, log=log)
        return "finished"
    if mode == "people":
        import people
        people.cluster(out, log=log)
        return "finished"
    raise RuntimeError(f"unknown job mode: {mode}")
