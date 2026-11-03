# Chhanni 3 — Footage QC, Selects & Script-to-Screen

Documentary footage ki chhanni: shaky, blur, too-dark / blown-out hisse hatata hai, har clip se best sections chunta hai, aur DaVinci Resolve / Premiere Pro mein bins + draft timelines bana deta hai. Sab kuch aapke PC par chalta hai, free hai, aur footage kahin move ya copy nahi hota.

## Install (ek baar)
1. Zip ko kisi pakki jagah extract kijiye, jaise `D:\Tools\Chhanni`. Baad mein folder move kiya to setup dobara chalana hoga.
2. `setup.bat` chalaiye. Ye ye sab install karega:
   - **Chhanni app** (Desktop aur Start menu par shortcut)
   - **Resolve panel** → `Workspace › Scripts › Chhanni`
   - **Premiere panel** → `Window › Extensions › Chhanni` (Premiere ek baar restart karna hoga)

## Kaam karne ka tareeka
1. **New project** → footage folders add kijiye (poora project folder ya alag-alag cards). Scan karne par dikhega kitni files hain, kitna size hai, kitni proxies mili, aur kaun-kaun se camera/card hain.
2. **Footage style** chuniye:
   - *Tripod / Gimbal*: strict
   - *Balanced*: zyada tar documentary footage ke liye
   - *Handheld*: natural handheld movement rakhta hai, sirf jhatke hatata hai
3. **Start analysis** dabaiye. Progress, time left aur live log dikhega. **Stop** dabane par progress save rehti hai; dobara Start karne par wahin se resume hota hai. App window band karne par bhi run background mein chalta rehta hai.
4. **Review selects**: har clip ka QC strip aur thumbnails dikhte hain.
   - Thumbnail par click → select on/off
   - ★ → favourite
   - Badge par click → A-roll / B-roll badlo
   - **Exclude** → poori clip hatao
   - **Tune & re-select** → settings badlo; analysis dobara nahi hota, naye selects seconds mein ban jaate hain
5. **Send to NLE**:
   - **Resolve**: Resolve ka panel (`Workspace › Scripts › Chhanni`) → project chuniye → *Import*
   - **Premiere**: `Window › Extensions › Chhanni` → project chuniye → *Import*
   - **XML**: FCP7 XML, jo kisi bhi NLE mein import ho sakta hai

Har NLE mein ye banta hai: `A-roll / B-roll / No usable` bins, har camera/card ki timeline ya sequence, score ke hisaab se colours (green ≥ 80, yellow 65–79, orange < 65), aur source clips par select markers. High-fps (S&Q) clips apne aap slow-motion mein conform ho jaati hain (option band bhi kar sakte hain).

## Naya in v3 — AI features
- **AI setup → Install AI pack** (ek baar, ~3–4 GB): local Whisper (transcription) + CLIP (visual tags). Sab free aur offline.
- **Transcribe:** har bolne wali clip ka Hindi / English / Hinglish transcript. A-roll apne aap speech ke hisaab se katta hai — retakes (aakhri take rakhta hai), false starts, fillers aur 1.2 s se lambi chuppi hat jaati hai. Audio clipping wale takes flag hote hain.
- **Smart tags:** har select par shot size (CU / MCU / MS / Wide), camera move (Static / Slider-gimbal / Smooth pan / Drone / Handheld), Golden hour / Blue hour / Night / Rich DR, aur **★ Hero shot**. Review screen mein tag filter aur bole gaye shabdon se search.
- **Script & assemble:** script / outline / concept daaliye, mode chuniye — *Documentary, Film scene, Music video, Real estate, Hero reel* — aur 3 alag timeline versions banenge (V1 dialogue + V2 B-roll + A1 music). Har script line ka matched take, match % aur alt takes dikhte hain; jo line kahin record nahi hui wo **"VO / pickup needed"** marker ke saath aati hai.
- **Smart bins:** Resolve — Wides / Mediums / Close-ups / Drone / Interviews bins + keywords + ★ Hero / Golden hour / Smooth moves timelines. Premiere — wahi bins + subclips.
- **LLM (optional):** "Ye sirf concept hai" chunne par LLM interviews se story banata hai — Ollama (local, free), Gemini (free key) ya Claude.

Script format:
```
# Opening
[drone shot of the fort at sunset] (5s)
Raju: Yeh kila teen sau saal purana hai
VO: Hum subah jaldi nikle the
SHOT: close-up of hands making chai
```


## Naya in v3.3 — Reel / Promo mode
**Script & assemble → 📱 Reel / Promo.** Har feature ka ON/OFF switch:
- 🧹 Cleanup: bad takes, filler words, silences (gap slider), audio-clipping takes, sirf stable B-roll
- 🎬 Visual: B-roll (kam/zyada), zoom cuts (jump cut par punch-in), auto colour balance (Resolve), Smart Reframe (Studio)
- 🔤 Text: hook, word-by-word captions (1–4 shabd), name lower-third, CTA end card
- 🔊 Audio: enhance audio (FFmpeg denoise + -14 LUFS), background music (level), sound effects
- 📐 Output: target length, best line pehle, 9:16 / 16:9 / 1:1 / 4:5, 3 variations, markers, purani timeline replace

**Instruction se regenerate:** version ke neeche likhiye — "30 sec karo", "B-roll zyada", "zoom hatao", "staff 3 hatao",
"line 2 ka dusra take", "hook: …", "cta: …", "naam: …", "16:9", "awaaz saaf karo" — ya **Switches** se badal kar Regenerate.
Naya version "· v2, v3…" banta hai; **Send to Resolve** "Purani timeline replace" ON ho to pichli timeline hata kar nayi daalta hai.

Resolve: captions subtitle track par aate hain — word-highlight ke liye **Effects › Titles › Animated Subtitles** ko subtitle track ke upar daaliye.
Hook / naam / CTA Text+ titles top track par. Premiere: captions track + zoom; titles markers ke roop mein.

## Proxies
- Resolve/camera proxies (`Proxy` folder) aur Sony `M4ROOT\SUB` files analysis ke liye apne aap use hoti hain, jisse kaam bahut tez hota hai.
- Timeline hamesha original camera files se banti hai.
- Ek hi card do jagah copy ho to duplicate files skip ho jaati hain.

Architecture, requirements aur roadmap: `ARCHITECTURE.md`.

## Dikkat aaye to
- App na khule: `setup.bat` dobara chalaiye. Log yahan milega: `%APPDATA%\Chhanni\chhanni.log`
- Premiere mein panel na dikhe: Premiere band karke dobara kholiye.
- Resolve "Send now" button ke liye: `Preferences › System › General › External scripting = Local`. Ye na ho to Resolve panel use kijiye.
