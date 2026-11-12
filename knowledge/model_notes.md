# Model notes (RTX 3060 12 GB)

Advisory only. The app never switches models automatically — see models_catalog.json; the user installs from the Models page.

## 2026-11-12 weekly check
- **Ollama v0.40.15 (Nov 3)**: concurrent requests for vision GGUF models, long-context scaling for Qwen3/Qwen3.5.
  ([releases](https://github.com/ollama/ollama/releases))
- **LFM3-VL 2B (Liquid AI)** — `ollama pull lfm3-vl:2b`, ~1.5–3.8 GB, 125K context. Windows support from Ollama v0.40.13
  (Oct 28). Candidate as a fast first-pass captioner; not as smart as Qwen3-VL 8B. ([ollama](https://ollama.com/library/lfm3-vl))
- **Nemotron 3.5 Bolt E4B (NVIDIA, from Gemma 4 E4B)** — `ollama pull nemotron-3.5-bolt:e4b`, ~7.9 GB, vision + thinking.
  Alternative VLM; untested on our footage. ([ollama](https://ollama.com/library/nemotron-3.5-bolt))
- **Bonsai 2 (Prism ML)** — ternary-weight vision models; 27B fits in ~8.3 GB (`bonsai-2:27b`). Quality not independently
  verified — watch, don't recommend yet. ([ollama](https://ollama.com/library/bonsai-2))
- **WeVisOne-VL-2B (Tencent, Apache-2.0)** — Qwen3-VL based detection/grounding/OCR/referring segmentation/alpha matting.
  Interesting for rotoscope/matting; transformers only, not on Ollama. ([HF](https://huggingface.co/tencent/WeVisOne-VL-2b))
- **Nemotron 3.5 Transcribe 30B-A4B** — Hindi + Hinglish code-switching + speaker labels, but ~17 GB at 4-bit → does NOT fit.
- **Nemotron 3.5 ASR streaming 0.6B** (June 2026) — fast, Hindi FLEURS WER ~7–8% (NVIDIA's figure); needs transformers ≥5.13
  or NeMo. Possible fast alternative to Whisper; Whisper large-v3 stays default.
- Nothing new this week for shot detection, faces, audio tagging, beat tracking, aesthetic scoring, upscaling.
- **Resolve 21.1+ free edition lost external scripting** — matters for the Chhanni Resolve panel (see editing.md).

## Standing notes
- Qwen3-VL 8B (`qwen3-vl:8b`, ~6 GB) remains the best all-round VLM for 12 GB; keep Whisper + VLM from loading together
  if VRAM is tight.
- Whisper large-v3 int8_float16 ≈ 5 GB VRAM; handles Hindi/English mix well.
- Q-ReAlign-Pro-9B (June 2026) gives 0–1 quality/aesthetic scores for images+video but ~18 GB BF16 → needs quantising; skip for now.

## 2026-11-12 (user request) — added to the app
- Models page: **Beat This!** item (Audio). When installed, music-video beat cutting uses it; else the built-in tracker.
- VLM dropdown: `nemotron-3.5-bolt:e4b` and `lfm3-vl:2b` (pick → Install). Only Qwen3-VL also becomes the AI Team LLM.
  Photo cull (dance) stays on Qwen3-VL 8B.
