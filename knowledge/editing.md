# Editing

## Core rules
- Cut on action or on a look; motivated cuts feel invisible.
- Enter late, leave early — trim heads/tails of every clip.
- Vary shot size between consecutive cuts (wide → medium → close); two near-identical sizes = jump cut.
- 30° rule for same-subject cuts; respect the 180° line unless crossing is shown.
- J-cuts and L-cuts smooth interviews and dialogue — lead with sound.
- Room tone/ambience under every cut; silence gaps read as mistakes.
- Hold hero shots a beat longer; shorten repetitive B-roll.
- Pace: average shot length should track the music/energy, not be constant.

## DaVinci Resolve (scripting changes that matter to Chhanni)
- **Resolve 21.1 (Sep 2026): external / Workflow-Integration scripting and UIManager became Studio-only; the free
  edition keeps only console access.** If the user updates the free version, the Chhanni panel and "Send now" may stop
  working — stay on 21.0.x free, or use Studio.
  ([Digital Production](https://digitalproduction.com/2026/09/08/resolve-21-1-adds-mcp-and-finally-gets-presets/),
  [Puget Systems](https://www.pugetsystems.com/blog/2026/09/10/how-davinci-resolve-free-v21-1-scripting-changes-affect-puget-bench/))
- 21.1 also: built-in MCP server for AI assistants (Studio), ~20 new API calls (render presets, transcription,
  multicam), Python 2 removed.
- 21.1.1 (Oct 6): async APIs for Quick Export, Transcription, Scene Cut Detection, Smart Reframe, Subtitles.
  ([Digital Production](https://digitalproduction.com/2026/10/06/davinci-resolve-21-1-1/))
- 21.1.2 (Oct 27): Studio API can move/trim/extend clips, remove gaps & silence, copy attributes; Photo-page album scripting.
  ([Digital Production](https://digitalproduction.com/2026/10/27/davinci-resolve-21-1-2-ai-ograf-scripting/))
- 21.2 (Nov 2026): more scripting API improvements (details unpublished), updated Fusion scripting docs,
  "restore last edit points", extracted audio keeps source timecode.
  ([Newsshooter](https://www.newsshooter.com/2026/11/04/blackmagic-design-davinci-resolve-21-2/))
- 21.0.4: `Timeline.GetSelectedClips()`, `MediaPoolItem.GetTimeline()`.

## Premiere Pro
- Premiere 27 (Nov 10 2026): projects saved in 27 do NOT open in 26; Sony RAW/F65 RAW import removed; UXP 9.4 with
  breaking changes (new `TickTime.timeToTimecode/timecodeToTime`, ProjectItemType enum).
  ([UXP changelog](https://developer.adobe.com/premiere-pro/uxp/changelog), [CineD](https://www.cined.com/adobe-premiere-27-0-released-color-mode-ai-assistant-panel-and-generative-audio-tools/))
- Adobe local MCP integrations for Premiere/After Effects/Photoshop expected early 2027.
  ([No Film School](https://nofilmschool.com/adobe-local-mcp-integrations))

## House style — learned from the editor (Nov 2026, Arangetram FAST v3)
- Multicam stage/dance: wide (Cam1) runs continuously on V1, close camera (Cam2) sits on V2 with a colour label
  (Orange), extra punch-ins on V3 (Pink). Audio from the master camera only.
- B-roll / close-camera shots on V2: **2–4 s each** for fast cuts; nothing on V2 longer than ~4 s in a fast cut.
- **Match on action** whenever two angles cover the same moment: cut in the middle of a clear movement
  (arm sweep, turn, jump, stamp, head turn), not when the dancer is still. The movement starts in one angle and
  finishes in the other. Pick the cut frame where motion is peaking in both angles, then snap to the nearest beat
  (±4 frames) — action first, beat second.
- Avoid cutting during a hold/pose unless it lands exactly on a strong beat (araimandi / mandi hold on sam).
- The editor's own manual cuts and adjustments are the reference: re-cut only what they asked for, keep the rest.
