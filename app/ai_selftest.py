"""Downloads the AI models once and checks the GPU path (run by setup_ai.bat)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

ok = {}
try:
    import torch
    ok["torch"] = torch.__version__
    ok["cuda"] = torch.cuda.is_available()
    if ok["cuda"]:
        ok["gpu"] = torch.cuda.get_device_name(0)
    print(f"   torch {torch.__version__}  CUDA: {ok['cuda']}  {ok.get('gpu', '')}")
except Exception as e:
    print("   torch:", e)

try:
    import llm
    size = llm.load_settings().get("whisper_model", "large-v3")
    import ai_audio
    t = time.time()
    m = ai_audio.load_model(size)
    print(f"   Whisper {size} ready on {ai_audio._MODEL['device']} ({time.time()-t:.0f}s)")
    ok["whisper"] = ai_audio._MODEL["device"]
except Exception as e:
    print("   Whisper:", e)

try:
    import ai_visual
    t = time.time()
    if ai_visual._load_clip():
        e = ai_visual.clip_text(["a drone shot"])
        print(f"   CLIP ready ({time.time()-t:.0f}s), embedding dim {e.shape[1]}")
        ok["clip"] = True
except Exception as e:
    print("   CLIP:", e)

p = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Chhanni", "ai_status.json")
with open(p, "w") as f:
    json.dump(ok, f, indent=1)
