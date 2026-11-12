/* Chhanni Studio v4 - Models, Deep analysis, Library, People, AI Team */
"use strict";
const XP = window.EXTRA_PAGES = {};
const fmtS = s => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const kfURL = f => f ? `/kf/${enc(f)}?out=${enc(S.out)}` : "";
const busy = (el, on, txt) => { if (!el) return; el.disabled = on; if (txt) el.dataset.t = el.dataset.t || el.textContent; el.textContent = on ? (txt || "…") : (el.dataset.t || el.textContent); };
const LABEL = { EWS: "Extreme wide", WS: "Wide", MS: "Medium", MCU: "Med close-up", CU: "Close-up", ECU: "Extreme CU", INSERT: "Insert" };
const needProject = () => { if (S.out) return false; main.innerHTML = `<div class="page"><div class="empty">Pehle ek project kholiye (Projects).</div></div>`; return true; };

/* ------------------------------------------------------------ Models */
XP.models = async function pageModels() {
  main.innerHTML = `<div class="page"><div class="empty">Loading…</div></div>`;
  let m; try { m = await api("/api/models"); } catch (e) { return toast(e.message, 1); }
  const st = m.status, ins = m.install, set = m.settings || {};
  const groups = [...new Set(m.catalog.map(c => c.group))];
  const missing = m.catalog.filter(c => !st[c.id]?.ok).map(c => c.id);
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>AI models</h1><p>Sab free, open-source, aapke PC par chalne wale models. Footage kahin upload nahi hoti.</p></div>
    <div class="row">${missing.length ? `<button class="btn primary" id="mall" ${ins.running ? "disabled" : ""}>Install sab (${missing.length})</button>` : `<span class="chip ok"><i></i>All models ready</span>`}</div></div>
    <div class="card" style="margin-bottom:16px"><div class="row"><b>GPU:</b>&nbsp;<span class="${st.gpu?.cuda ? "" : "muted"}">${h(st.gpu?.name || "CUDA nahi mila (CPU par slow chalega)")}</span><span class="sp"></span>
      <span class="small muted">Qwen3-VL ke liye Ollama install hota hai (winget). Pehli baar ~10 GB download.</span></div></div>
    ${groups.map(g => `<h3 style="margin:18px 0 8px">${h(g)}</h3><div class="grid g2">${m.catalog.filter(c => c.group === g).map(c => {
      const s = st[c.id] || {}; const ok = s.ok;
      return `<div class="card mcard"><div class="row"><b><span class="sdot ${ok ? "ok" : s.partial ? "part" : ""}"></span>${h(c.name)}</b><span class="sp"></span><span class="small muted">${h(c.size)}</span></div>
        <div class="small muted" style="margin:4px 0 8px">${h(c.what)} · ${h(c.use)}</div>
        <div class="row">${ok ? "" : `<button class="btn primary sm" data-inst="${c.id}" ${ins.running ? "disabled" : ""}>Install</button>`}
        ${["qc", "motion"].includes(c.id) ? "" : `<button class="btn sm" data-test="${c.id}" ${ok ? "" : "disabled"}>Test</button>`}<span class="small muted" id="tr_${c.id}"></span></div>
        ${c.id === "vlm" ? `<div class="grid g2" style="margin-top:10px"><div class="field"><label>Model (VRAM ke hisaab se)</label><select class="input" id="vlmm">
            <option value="qwen3-vl:8b-instruct">qwen3-vl 8B — best (12 GB GPU)</option><option value="qwen3-vl:4b-instruct">qwen3-vl 4B — fast (8 GB GPU)</option><option value="qwen3-vl:2b-instruct">qwen3-vl 2B — light (6 GB GPU)</option><option value="nemotron-3.5-bolt:e4b">nemotron-3.5-bolt E4B — naya, try karein (~8 GB)</option><option value="lfm3-vl:2b">lfm3-vl 2B — naya, bahut tez (~3 GB)</option></select></div>
          <div class="field"><label>Description kab banaye</label><select class="input" id="vlmode"><option value="auto">Auto — har clip ke best shots</option><option value="all">Har shot (slow, sabse detail)</option><option value="off">Off</option></select></div></div>
          <div class="small muted">Pulled: ${h((s.pulled || []).join(", ") || "—")} ${s.ollama ? "" : s.ollama_installed ? "· Ollama band hai (Install dabane se start ho jayega)" : ""}</div>` : ""}</div>`;
    }).join("")}</div>`).join("")}
    <div class="card" style="margin-top:16px"><div class="row"><h3>Install log</h3><span class="sp"></span><span class="small muted" id="ist">${ins.running ? "installing " + h(ins.item || "") + "…" : ins.error ? "Error: " + h(ins.error) : ""}</span></div>
      <div class="console" id="mlog" style="height:220px"></div></div></div>`;
  const vm = $("#vlmm"); if (vm) { vm.value = set.vlm_model || "qwen3-vl:8b-instruct"; vm.onchange = async () => { await api("/api/ai/settings", { vlm_model: vm.value }); XP.models(); }; }
  const vo = $("#vlmode"); if (vo) { vo.value = set.vlm_mode || "auto"; vo.onchange = () => api("/api/ai/settings", { vlm_mode: vo.value }).then(() => toast("Saved")); }
  const install = async items => { try { await api("/api/models/install", { items }); toast("Install shuru - log neeche"); watch(); } catch (e) { toast(e.message, 1); } };
  $("#mall") && ($("#mall").onclick = () => install(missing));
  $$("[data-inst]").forEach(b => b.onclick = () => install([b.dataset.inst]));
  $$("[data-test]").forEach(b => b.onclick = async () => { const o = $("#tr_" + b.dataset.test); o.textContent = "testing… (pehli baar model load hota hai)"; busy(b, true, "…");
    try { const r = await api("/api/models/test", { item: b.dataset.test }); o.textContent = (r.ok ? "OK · " : "Failed · ") + r.out.split("\n").slice(-1)[0].slice(0, 140); } catch (e) { o.textContent = "Failed · " + e.message; } busy(b, false); });
  const draw = l => { const c = $("#mlog"); if (!c) return; c.innerHTML = (l || []).map(x => `<div>${h(x[1])}</div>`).join(""); c.scrollTop = 1e9; };
  draw(ins.log);
  async function watch() {
    if (S.route !== "models") return;
    try { const r = await api("/api/models"); draw(r.install.log); $("#ist").textContent = r.install.running ? "installing " + (r.install.item || "") + "…" : r.install.error ? "Error: " + r.install.error : "done";
      if (r.install.running) setTimeout(watch, 1500); else XP.models(); } catch { setTimeout(watch, 3000); }
  }
  if (ins.running) watch();
};

/* ------------------------------------------------------------ Deep analysis */
XP.deep = async function pageDeep() {
  if (needProject()) return;
  let ds, ms; try { [ds, ms] = await Promise.all([api("/api/deep/status?out=" + enc(S.out)), api("/api/models")]); } catch (e) { return toast(e.message, 1); }
  const st = ms.status, set = ms.settings || {};
  const run = S.job?.running && S.job.out === S.out && ["deep", "people"].includes(S.job.mode);
  const nf = Object.keys(ds.failed || {}).length;
  const chip = (ok, t) => `<span class="chip ${ok ? "ok" : "warn"}"><i></i>${t}</span>`;
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>Deep analysis</h1><p>Har shot ko editor / DOP ki tarah samajhna: shot size, camera, light, location, log, emotion, aawaz, aur shabdon mein description.</p></div>
    <div class="row">${run ? `<button class="btn" id="dstop">Stop</button>` : `<button class="btn primary" id="dgo">${ds.done ? "Baaki clips analyse karo" : "Start deep analysis"}</button>`}</div></div>
    <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${chip(st.shots?.ok, "Shots")}${chip(true, "Camera motion")}${chip(st.clip?.ok, "Look (CLIP)")}${chip(st.faces?.ok, "Faces / people")}${chip(st.clap?.ok, "Sound (CLAP)")}${chip(st.vlm?.ok, "Description (Qwen3-VL)")}
      <span class="sp"></span><a class="btn ghost sm" onclick="go('models')">Models…</a></div>
      <div class="grid g3" style="margin-top:12px">
        <div class="field"><label>Description (Qwen3-VL)</label><select class="input" id="dvlm"><option value="auto">Auto — best shots per clip</option><option value="all">Har shot (slow)</option><option value="off">Off (fast)</option></select></div>
        <div class="field"><label>Sound analysis</label><select class="input" id="dsnd"><option value="1">On</option><option value="0">Off</option></select></div>
        <div class="field"><label>Faces / people</label><select class="input" id="dfac"><option value="1">On</option><option value="0">Off</option></select></div></div>
      <div class="kv" style="margin-top:8px"><div><b>${ds.done}/${ds.clips}</b><span>clips done</span></div><div><b id="dpct">—</b><span>progress</span></div><div><b id="deta">—</b><span>time left</span></div><div><b>${nf}</b><span>skipped</span></div></div>
      <div class="bar" style="margin-top:10px"><div id="dbar" style="width:${ds.clips ? ds.done / ds.clips * 100 : 0}%"></div></div>
      <div class="small muted" id="dcur" style="margin-top:6px"></div>
      ${nf ? `<div class="note" style="margin-top:8px">Skipped: ${Object.values(ds.failed).slice(0, 6).map(f => h(f.name)).join(", ")}${nf > 6 ? "…" : ""} <a class="btn ghost sm" id="dretry">Retry</a></div>` : ""}
      ${!st.clip?.ok || !st.vlm?.ok ? `<div class="small muted" style="margin-top:8px">Tip: jo models install nahi hain unke bina bhi analysis chalega, bas woh hissa khali rahega. Models page se install kar sakte hain.</div>` : ""}</div>
    <div class="card" style="margin-top:16px"><div class="row"><h3>Log</h3><span class="sp"></span><span class="small muted">Koi model crash ho to sirf woh clip skip hota hai, app chalta rehta hai</span></div><div class="console" id="dlog" style="height:260px"></div></div>
    ${ds.done ? `<div class="row" style="margin-top:16px"><button class="btn primary" onclick="go('library')">Library mein search karo →</button><button class="btn" onclick="go('people')">People →</button><button class="btn" onclick="go('crew')">AI Team →</button></div>` : ""}</div>`;
  $("#dvlm").value = set.vlm_mode || "auto"; $("#dsnd").value = set.deep_audio === false ? "0" : "1"; $("#dfac").value = set.deep_faces === false ? "0" : "1";
  const saveOpts = () => api("/api/ai/settings", { vlm_mode: $("#dvlm").value, deep_audio: $("#dsnd").value === "1", deep_faces: $("#dfac").value === "1" });
  ["dvlm", "dsnd", "dfac"].forEach(i => $("#" + i).onchange = saveOpts);
  const start = async retry => { await saveOpts(); try { await api("/api/deep/start", { out: S.out, retry_failed: !!retry }); toast("Deep analysis shuru"); setTimeout(XP.deep, 600); } catch (e) { toast(e.message, 1); } };
  $("#dgo") && ($("#dgo").onclick = () => start(false));
  $("#dretry") && ($("#dretry").onclick = () => start(true));
  $("#dstop") && ($("#dstop").onclick = async () => { await api("/api/stop", {}); toast("Stop ho raha hai…"); });
  let since = 0; const lines = [];
  async function tick() {
    if (S.route !== "deep") return;
    try {
      const r = await api("/api/state?since=" + since);
      if (r.log.length) { since = r.log[r.log.length - 1].t; r.log.forEach(l => lines.push(l.m)); const c = $("#dlog"); if (c) { c.innerHTML = lines.slice(-200).map(x => `<div>${h(x)}</div>`).join(""); c.scrollTop = 1e9; } }
      const j = r.job, p = j.progress || {};
      if (j.running && ["deep", "people"].includes(j.mode)) {
        const pct = p.pct != null ? p.pct : p.total ? 100 * p.done / p.total : 0;
        $("#dpct").textContent = Math.round(pct) + "%"; $("#dbar").style.width = pct + "%"; $("#deta").textContent = fmtETA(p.eta);
        $("#dcur").textContent = (p.current || []).join(", ") + (p.sub ? " · " + p.sub : "");
      } else if (run && !j.running) { return XP.deep(); }
    } catch { }
    setTimeout(tick, 1200);
  }
  tick();
};

/* ------------------------------------------------------------ Library */
const FACET_NAMES = { size: "Shot size", camera: "Camera", angle: "Angle", light: "Light", place: "In / Out", time: "Time", scene: "Location", subject: "Subject", emotion: "Emotion", sound: "Sound", music_mood: "Music mood", person: "People", use: "Use" };
XP.library = async function pageLibrary() {
  if (needProject()) return;
  let f; try { f = await api("/api/library/facets?out=" + enc(S.out)); } catch (e) { return toast(e.message, 1); }
  if (!f.segments) { main.innerHTML = `<div class="page"><div class="empty">Library khali hai. Pehle <a onclick="go('deep')">Deep analysis</a> chalaiye.</div></div>`; return; }
  S.lib = S.lib || { q: "", filters: {}, picks: [] };
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>Library</h1><p>${f.segments} shots · ${f.clips} clips — jo chahiye likhiye: "drone shot of fort", "close-up crying", "haath dholak bajate", "pani ke baare mein bolta hai"</p></div>
    <div class="row"><span class="small muted" id="npick"></span><button class="btn primary" id="sout" disabled>String-out version banao</button></div></div>
    <div class="card"><div class="row"><input class="input" id="lq" style="flex:1" placeholder="Search footage…" value="${h(S.lib.q)}"><button class="btn primary" id="lgo">Search</button></div>
      <div class="facets" id="lfac">${Object.entries(FACET_NAMES).filter(([k]) => (f.facets[k] || []).length).map(([k, n]) =>
        `<select class="input sm" data-f="${k}"><option value="">${n}</option>${f.facets[k].map(([v, c]) => `<option value="${h(v)}">${h(LABEL[v] || v)} (${c})</option>`).join("")}</select>`).join("")}
        <select class="input sm" data-f="min_beauty"><option value="">Beauty</option><option value="60">60+</option><option value="70">70+</option><option value="80">80+</option></select></div></div>
    <div class="libgrid" id="lres"></div></div>`;
  $$("#lfac select").forEach(s => { s.value = S.lib.filters[s.dataset.f] || ""; s.onchange = () => { S.lib.filters[s.dataset.f] = s.value; doSearch(); }; });
  $("#lgo").onclick = () => { S.lib.q = $("#lq").value; doSearch(); };
  $("#lq").onkeydown = e => { if (e.key === "Enter") $("#lgo").click(); };
  const updPick = () => { $("#npick").textContent = S.lib.picks.length ? S.lib.picks.length + " selected" : ""; $("#sout").disabled = !S.lib.picks.length; };
  $("#sout").onclick = async () => {
    const name = prompt("Version ka naam", S.lib.q ? "Search: " + S.lib.q : "Library selects"); if (name === null) return;
    try { await api("/api/library/stringout", { out: S.out, picks: S.lib.picks, name }); S.lib.picks = []; toast("Version ban gaya - Script & assemble mein dekhiye / Resolve bhejiye"); go("assemble"); } catch (e) { toast(e.message, 1); }
  };
  async function doSearch() {
    $("#lres").innerHTML = `<div class="empty">Searching…</div>`;
    let r; try { r = await api("/api/library/search", { out: S.out, q: S.lib.q, filters: S.lib.filters, limit: 120 }); } catch (e) { return toast(e.message, 1); }
    S.lib.res = r.results;
    $("#lres").innerHTML = r.results.length ? r.results.map((x, i) => {
      const on = S.lib.picks.some(p => p.clip === x.clip && p.seg === x.seg);
      return `<div class="lcard ${on ? "on" : ""}" data-i="${i}"><div class="lth" style="background-image:url('${kfURL(x.kf)}')"><span class="ltc">${fmtS(x.start)}–${fmtS(x.end)}</span>${x.beauty ? `<span class="lbe">${x.beauty}</span>` : ""}</div>
        <div class="lmeta"><div class="small ellip" title="${h(x.name)}">${h(x.name)}</div>${x.desc ? `<div class="small muted clamp2">${h(x.desc)}</div>` : ""}
        <div class="tags">${[LABEL[x.size] || x.size, x.camera !== "static" ? x.camera : "", x.emotion, x.scene, x.sound, ...(x.person || [])].filter(Boolean).slice(0, 6).map(t => `<span class="tg">${h(t)}</span>`).join("")}</div>
        ${x.said ? `<div class="small clamp2 quote">“${h(x.said)}”</div>` : ""}</div></div>`;
    }).join("") : `<div class="empty">Kuch nahi mila. Doosre shabd ya kam filters try kijiye.</div>`;
    $$("#lres .lcard").forEach(el => el.onclick = () => {
      const x = S.lib.res[+el.dataset.i]; const k = S.lib.picks.findIndex(p => p.clip === x.clip && p.seg === x.seg);
      if (k >= 0) S.lib.picks.splice(k, 1); else S.lib.picks.push({ clip: x.clip, seg: x.seg }); el.classList.toggle("on", k < 0); updPick();
    });
    updPick();
  }
  doSearch();
};

/* ------------------------------------------------------------ People */
XP.people = async function pagePeople() {
  if (needProject()) return;
  let d; try { d = await api("/api/people?out=" + enc(S.out)); } catch (e) { return toast(e.message, 1); }
  const face = p => { const b = p.sample?.box || [0, 0, 1, 1]; const m = 0.35; const x = Math.max(0, b[0] - b[2] * m), y = Math.max(0, b[1] - b[3] * m), w = Math.min(1 - x, b[2] * (1 + 2 * m)), hh = Math.min(1 - y, b[3] * (1 + 2 * m));
    return `background-image:url('${kfURL(p.sample?.kf)}');background-size:${100 / w}% ${100 / hh}%;background-position:${w < 1 ? x / (1 - w) * 100 : 0}% ${hh < 1 ? y / (1 - hh) * 100 : 0}%`; };
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>People</h1><p>Footage mein baar-baar aane wale chehre, apne aap group kiye gaye. Naam dijiye — phir Library mein naam se search aur AI Team unhe character ki tarah samjhegi.</p></div>
    <div class="row"><button class="btn" id="prc">Dobara group karo</button></div></div>
    ${d.people.length ? `<div class="pgrid">${d.people.map(p => `<div class="card pcard"><div class="pface" style="${face(p)}"></div>
      <input class="input" data-p="${p.id}" placeholder="Naam (e.g. Ismail Khan)" value="${h(p.name || "")}"><div class="small muted">${p.count} shots · ${p.clips.length} clips</div>
      <a class="small" data-find="${h(p.name || p.id)}">Library mein dekho →</a></div>`).join("")}</div>`
    : `<div class="empty">Abhi koi chehra group nahi hua. Deep analysis (Faces on, SFace model installed) ke baad yahan dikhenge.</div>`}</div>`;
  $$("[data-p]").forEach(i => i.onchange = async () => { await api("/api/people/rename", { out: S.out, id: i.dataset.p, name: i.value }); toast("Saved"); });
  $$("[data-find]").forEach(a => a.onclick = () => { S.lib = { q: "", filters: { person: a.dataset.find }, picks: [] }; go("library"); });
  $("#prc").onclick = async () => { try { await api("/api/people/recluster", { out: S.out }); toast("Grouping…"); setTimeout(XP.people, 4000); } catch (e) { toast(e.message, 1); } };
};

/* ------------------------------------------------------------ AI Team */
const ROLE_ICON = { director: "", dop: "", editor: "", producer: "", concept: "" };
const ROLE_NAME = { director: "Director", dop: "DOP", editor: "Editor", producer: "Producer", concept: "Concept maker" };
function briefGet() { try { return JSON.parse(localStorage.getItem("chhanni.brief." + S.out) || "{}"); } catch { return {}; } }
XP.crew = async function pageCrew() {
  if (needProject()) return;
  let ms, vers; try { [ms, vers] = await Promise.all([api("/api/models"), api("/api/versions?out=" + enc(S.out))]); } catch (e) { return toast(e.message, 1); }
  const s = ms.settings || {}; const b = briefGet(); const tab = S.crewTab || "concept";
  const llmOk = s.llm_provider && s.llm_provider !== "none";
  const vlist = (vers.versions || vers || []);
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>AI Team</h1><p>Concept maker, Director, DOP, Editor aur Producer — duniya ke best filmmakers ke usoolon (Murch Rule of Six, hook, structure, contrast) se sochne wali team.</p></div>
    <div class="row"><span class="chip ${llmOk ? "ok" : "warn"}"><i></i>${llmOk ? h(s.llm_provider + " · " + (s.llm_model || "")) : "Dimaag set nahi"}</span><a class="btn ghost sm" onclick="go('ai')">Change</a></div></div>
    ${llmOk ? "" : `<div class="card" style="margin-bottom:16px;border-color:#4a3c1e">AI Team ko ek LLM chahiye. Free/local: <b>Models → Qwen3-VL install</b> (khud set ho jayega). Ya Settings mein Claude / Gemini key daaliye (zyada smart).</div>`}
    <div class="card"><h3>Brief</h3><div class="grid g3">
      <div class="field"><label>Genre</label><select class="input" id="bg"><option>documentary</option><option>feature film</option><option>short film</option><option>music video</option><option>ad film</option><option>brand film</option><option>reel / short</option><option>wedding film</option><option>corporate</option></select></div>
      <div class="field"><label>Platform</label><select class="input" id="bp"><option>YouTube</option><option>Instagram reel</option><option>OTT / Netflix-style</option><option>TV</option><option>Film festival</option><option>Cinema</option><option>Client presentation</option></select></div>
      <div class="field"><label>Length</label><input class="input" id="bd" placeholder="e.g. 3 min, 30 sec, 52 min" value="${h(b.duration || "")}"></div></div>
      <div class="grid g2"><div class="field"><label>Audience</label><input class="input" id="ba" placeholder="e.g. young Indian audience, global festival jury" value="${h(b.audience || "")}"></div>
      <div class="field"><label>Notes (kahani, brand, mood, kya zaroori hai)</label><input class="input" id="bn" placeholder="e.g. Langa musicians ki virasat, garv aur dard dono" value="${h(b.notes || "")}"></div></div></div>
    <div class="vtabs" style="margin:16px 0 10px"><a class="vtab ${tab === "concept" ? "on" : ""}" data-t="concept">Concepts &amp; hooks</a><a class="vtab ${tab === "review" ? "on" : ""}" data-t="review">Version review</a><a class="vtab ${tab === "ask" ? "on" : ""}" data-t="ask">Crew se poochho</a></div>
    <div id="ctab"></div></div>`;
  $("#bg").value = b.genre || "documentary"; $("#bp").value = b.platform || "YouTube";
  const brief = () => { const o = { genre: $("#bg").value, platform: $("#bp").value, duration: $("#bd").value, audience: $("#ba").value, notes: $("#bn").value }; localStorage.setItem("chhanni.brief." + S.out, JSON.stringify(o)); return o; };
  ["bg", "bp", "bd", "ba", "bn"].forEach(i => $("#" + i).onchange = brief);
  $$(".vtab[data-t]").forEach(a => a.onclick = () => { S.crewTab = a.dataset.t; XP.crew(); });
  const T = $("#ctab");
  if (tab === "concept") {
    let saved = null; try { saved = (await api("/api/crew/saved?out=" + enc(S.out) + "&name=concepts")).data; } catch { }
    const draw = r => (r?.concepts || []).map((c, i) => `<div class="card" style="margin-bottom:12px"><div class="row"><h3>${i + 1}. ${h(c.title || "")}</h3></div>
      <div style="margin:4px 0 8px"><b>${h(c.logline || "")}</b></div>${c.theme ? `<div class="small muted">Theme: ${h(c.theme)}</div>` : ""}
      ${c.hook ? `<div class="hookbox"><b>Hook:</b> ${h(c.hook.idea || "")}<div class="kfrow">${(c.hook.refs || []).map(refThumb).join("")}</div></div>` : ""}
      ${(c.structure || []).map(st => `<div class="beat"><b>${h(st.part || "")}</b> — ${h(st.beat || "")}<div class="kfrow">${(st.refs || []).map(refThumb).join("")}</div></div>`).join("")}
      <div class="small" style="margin-top:8px">Music: ${h(c.music_mood || "")} · <span class="muted">${h(c.why_it_works || "")}</span></div></div>`).join("");
    T.innerHTML = `<div class="row" style="margin-bottom:12px"><button class="btn primary" id="cgo" ${llmOk ? "" : "disabled"}>${saved ? "Naye concepts banao" : "Footage se concepts banao"}</button><span class="small muted">Deep analysis ka data use hota hai · local model par 1–3 min</span></div><div id="cout">${saved ? draw(saved) : ""}</div>`;
    $("#cgo").onclick = async () => { busy($("#cgo"), true, "Team soch rahi hai…"); try { const r = await api("/api/crew/concept", { out: S.out, brief: brief() }); $("#cout").innerHTML = draw(r); } catch (e) { toast(e.message, 1); } busy($("#cgo"), false); };
  } else if (tab === "review") {
    T.innerHTML = `<div class="row" style="margin-bottom:12px"><select class="input" id="rv" style="min-width:320px">${vlist.map(v => `<option value="${h(v.id)}">${h(v.name)} · ${fmtS(v.duration)}</option>`).join("")}</select>
      <button class="btn primary" id="rgo" ${llmOk && vlist.length ? "" : "disabled"}>Team se review karao</button><span class="small muted">${vlist.length ? "Director, DOP, Editor, Producer — har ek alag nazar se" : "Pehle Script & assemble mein version banaiye"}</span></div><div id="rout"></div>`;
    const drawR = r => `<div class="card" style="margin-bottom:12px"><div class="row"><h3>Team score: ${r.score ?? "—"}/10</h3><span class="sp"></span>
        ${r.plan?.length ? `<button class="btn primary" id="rapply">Plan apply karke naya version banao</button>` : ""}</div>
        ${r.plan?.length ? `<div class="small" style="margin-top:6px">Final plan: ${r.plan.map(p => `<span class="tg">${h(p)}</span>`).join(" ")}</div>` : ""}</div>
      <div class="grid g2">${Object.entries(r.roles || {}).map(([role, x]) => `<div class="card"><div class="row"><h3>${ROLE_NAME[role] || role}</h3><span class="sp"></span><b>${x.score ?? ""}${x.score != null ? "/10" : ""}</b></div>
        ${x.error ? `<div class="small" style="color:#ff8a8a">${h(x.error)}</div>` : `<div class="small" style="margin:4px 0 8px"><i>${h(x.verdict || "")}</i></div>
        ${(x.strengths || []).map(t => `<div class="small"><span class="sdot ok"></span>${h(t)}</div>`).join("")}
        ${(x.problems || []).map(p => `<div class="small" style="margin-top:6px"><span class="sdot part"></span><b>${h(p.at || "")}</b> ${h(p.issue || "")}<br><span class="muted">→ ${h(p.fix || "")}</span></div>`).join("")}`}</div>`).join("")}</div>`;
    const load = async () => { const id = $("#rv").value; if (!id) return; try { const r = (await api("/api/crew/saved?out=" + enc(S.out) + "&name=review_" + id)).data; $("#rout").innerHTML = r ? drawR(r) : ""; bindApply(r); } catch { } };
    const bindApply = r => { $("#rapply") && ($("#rapply").onclick = async () => { try { await api("/api/regenerate", { out: S.out, id: $("#rv").value, instruction: r.plan.join(", ") }); toast("Naya version ban gaya"); go("assemble"); } catch (e) { toast(e.message, 1); } }); };
    $("#rv").onchange = load; load();
    $("#rgo").onclick = async () => { busy($("#rgo"), true, "Review chal raha hai…"); try { const r = await api("/api/crew/review", { out: S.out, version: $("#rv").value, brief: brief() }); $("#rout").innerHTML = drawR(r); bindApply(r); } catch (e) { toast(e.message, 1); } busy($("#rgo"), false); };
  } else {
    T.innerHTML = `<div class="card"><div class="row"><select class="input" id="ar" style="width:180px">${Object.keys(ROLE_NAME).map(k => `<option value="${k}">${ROLE_NAME[k]}</option>`).join("")}</select>
      <select class="input" id="av" style="width:260px"><option value="">(koi version nahi — sirf footage)</option>${vlist.map(v => `<option value="${h(v.id)}">${h(v.name)}</option>`).join("")}</select></div>
      <div class="row" style="margin-top:10px"><input class="input" id="aq" style="flex:1" placeholder="e.g. Is documentary ka sabse strong opening kya hoga? / Kaunse shots sabse cinematic hain?"><button class="btn primary" id="ago" ${llmOk ? "" : "disabled"}>Poochho</button></div>
      <div id="aout" style="margin-top:12px;white-space:pre-wrap"></div></div>`;
    $("#aq").onkeydown = e => { if (e.key === "Enter") $("#ago").click(); };
    $("#ago").onclick = async () => { const q = $("#aq").value.trim(); if (!q) return; busy($("#ago"), true, "…"); $("#aout").textContent = "Soch raha hai…";
      try { const r = await api("/api/crew/ask", { out: S.out, role: $("#ar").value, q, brief: brief(), version: $("#av").value || null }); $("#aout").textContent = r.answer; } catch (e) { $("#aout").textContent = "Error: " + e.message; } busy($("#ago"), false); };
  }
};
function refThumb(r) { return Array.isArray(r) ? `<div class="kft" title="${h(r[0])}" style="background-image:url('${kfURL(`${r[0]}_${String(r[1]).padStart(4, "0")}.jpg`)}')"></div>` : ""; }
