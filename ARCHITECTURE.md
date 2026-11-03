# Chhanni 3 — Concept, Requirements & Architecture

AI assistant editor for real-shoot content: documentaries, films, music videos and real-estate walk-throughs. It turns raw footage plus a script, outline or concept into reviewed selects, smart bins and several alternative rough-cut timelines in DaVinci Resolve and Premiere Pro. Everything runs local-first on the editor's PC, using free models. Cloud LLMs are optional.

---

## 1. Ideal workflow

```
Footage (TBs, any card structure)              Script / outline / concept        Music (optional)
        │                                               │                              │
        ▼                                               │                              │
 [1 Ingest & QC]  scan → proxies → GPU decode → shake / blur / exposure / cuts          │
        ▼                                               │                              │
 [2 Understand]   Whisper transcript · retakes · false starts · dead air · clipping     │
                  shot size · camera move · light · hero score · CLIP tags              │
        ▼                                               ▼                              ▼
 [3 Review]  editor toggles selects, ★ favourites, A/B type      [4 Assemble]  script → beats → takes / B-roll
        └───────────────────────────────┬───────────────────────────────┘    music → beats → cut grid
                                        ▼
                     [5 Deliver]  Resolve panel · Premiere panel · FCP7 XML
                     bins · smart bins · per-camera string-outs · N timeline versions
```

1. **New project:** add folders. The scan shows file count, size, proxy coverage and cameras/cards.
2. **Analyze** (overnight, resumable): technical QC and multiple best windows per clip.
3. **Transcribe** (AI pack): every clip with speech. A-roll is rebuilt around speech, with retakes, false starts, fillers and dead air removed.
4. **Review:** filter by tag or camera, search spoken words, switch selects on/off, star favourites.
5. **Script & assemble:** pick a mode, paste the script or concept, add music. Three alternative versions are generated, each with a match table and timeline preview.
6. **Send:** one click into Resolve or Premiere. Original camera media stays linked, and high-frame-rate clips can be conformed to slow motion.

## 2. Requirements → status

| Requirement | Chhanni 3 | How |
|---|---|---|
| Script / idea input | ✅ | Script editor with screenplay, outline, VO, `[visual]` and `SHOT:` formats; `.txt` load; "concept only" switch |
| Transcribe and map dialogue to the script | ✅ | faster-whisper (local GPU); Hindi/Hinglish/English fuzzy matching (Devanagari→Roman transliteration plus word and character n-grams); later take preferred when scores tie |
| Multiple timeline versions | ✅ | Documentary (best / alternate / tight), Film scene (best / angle variety / alternate), Music video (energy / 2-beat / 4-beat), Real estate (shoot order / room tour / 60 s highlights), Hero reel |
| False starts, retakes, dead air | ✅ | Transcript analysis; dead air above 1.2 s is cut; fillers flagged |
| Shaky, blur, bad light | ✅ | Acceleration-based shake, shot-relative sharpness, exposure |
| Audio clipping | ✅ | Full-scale sample detection at the original sample rate; the take score is penalised |
| Hero shots | ✅ | Hero score = technical + light + movement + composition (+ CLIP aesthetic). Top ~12 % of B-roll |
| Cinematic visuals | ✅ | Movement class (static / slider-gimbal / smooth pan / tilt / handheld / drone), golden and blue hour, rich dynamic range |
| Actor expressions / peaks | ◐ | Faces and shot size per select; emotion-peak detection is on the roadmap (needs a facial-expression model) |
| Smart bins | ✅ | Wides / Mediums / Close-ups / Drone / Interviews / No usable; keywords and descriptions in Resolve; subclips in Premiere for ★ Hero, Golden hour, Smooth moves, Soundbites |
| Clean GUI | ✅ | Desktop app (Projects · Analyze · Review · Script & assemble · Send · AI setup) |
| Resolve integration | ✅ | Workspace › Scripts › Chhanni panel, plus "Send now" via external scripting |
| Premiere integration | ✅ | CEP panel (Window › Extensions › Chhanni): bins, sequences, labels, markers, versions, subclips |
| Round trip | ◐ | One-way NLE push plus FCP7 XML. Reading editor changes back from the NLE is on the roadmap |

## 3. Architecture

```
┌─────────────────────────── Editor's PC (Windows, RTX GPU) ───────────────────────────┐
│  Chhanni app (Edge app-window UI)      Resolve panel (UIManager)   Premiere panel (CEP)│
│            │  HTTP 127.0.0.1:47321              │  imports plan / bridge   │  fetch + ExtendScript
│            ▼                                    ▼                          ▼          │
│  server.py ── jobs (analyze · transcribe · select) ── plan.py (review-aware plans, XML)│
│     │                                                                                  │
│     ├─ engine.py      FFmpeg/NVDEC decode @320px 10fps · QC · selects · thumbnails     │
│     ├─ ai_audio.py    faster-whisper · clipping · retakes · dead air · transliteration │
│     ├─ ai_visual.py   faces→shot size · motion→camera move · colour→light · CLIP       │
│     ├─ script.py      parse beats · transcript index · dialogue & visual matching      │
│     ├─ beats.py       onset envelope · tempo · DP beat tracker · energy                │
│     ├─ assemble.py    5 modes → multi-track versions (V1/V2/A1/A2)                     │
│     ├─ llm.py         none | Ollama | Gemini | Claude | OpenAI-compatible (optional)   │
│     └─ resolve_bridge.py  bins · timelines · versions · smart bins (Resolve API)       │
│  Project folder: chhanni.db · cache/*.npz · thumbs · transcripts · versions · review.json│
└───────────────────────────────────────────────────────────────────────────────────────┘
```

- **Footage is never moved or re-encoded.** Analysis uses proxies when they exist (Resolve `Proxy`, Sony `M4ROOT/SUB`). Timelines always link the original files.
- **Resumable and incremental:** a SQLite record for each file, cached per-frame metrics, and cached transcripts and CLIP embeddings. Changing thresholds or the review takes seconds and needs no new analysis.
- **One plan for every NLE:** review choices apply equally in Resolve, Premiere and XML.

## 4. Models: preferred vs free alternatives

| Job | Commercial option | Free option used by Chhanni | Notes |
|---|---|---|---|
| Speech-to-text | OpenAI Whisper API, Gemini audio | **faster-whisper large-v3** (local, RTX 3060 int8, ~3 GB VRAM); `large-v3-turbo` for speed | large-v3 is more accurate than turbo for Hindi |
| Script ↔ transcript mapping | Claude / Gemini | **Rule-based fuzzy matcher** (no model, offline) | Handles Devanagari vs Roman Hinglish |
| Story from concept | Claude Sonnet/Haiku 5.5 API, Gemini | **Ollama** local (qwen3:8b, gemma3:12b, llama3.1:8b) or **Gemini free-tier key** | Optional; set under AI setup |
| Visual understanding | Gemini video understanding | **CLIP ViT-B/32 (open_clip)** local plus OpenCV faces and built-in motion/colour analysis | Gemini could later be added as an optional "describe shot" provider |
| Beat detection | — | **Built-in numpy beat tracker** | No librosa needed |

## 5. Technical feasibility on the target PC (i5-12th gen, RTX 3060 12 GB)

- **QC analysis:** limited by decode speed. With proxies, on the order of tens of times faster than real time. 4K 10-bit 4:2:2 H.265 without proxies falls back to CPU decode and is slower, so proxies are strongly recommended.
- **Whisper large-v3 int8:** roughly 2× real time or better on a 3060. Only clips with speech are transcribed.
- **CLIP B/32:** thousands of thumbnails per minute on the GPU.
- **Assembly and matching:** seconds.

## 6. Known limits / roadmap

1. Expression / emotion peaks (face-expression model on A-roll frames).
2. Speaker diarization (who is speaking) for multi-person interviews.
3. Lip-sync for music-video performance takes (audio-to-music alignment).
4. Reading edits back from the NLE (re-sync after the editor changes the timeline).
5. Optional Gemini "describe this shot" for richer search.
