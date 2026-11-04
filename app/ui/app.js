/* Chhanni desktop UI - vanilla JS, talks to the local server */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const main = $("#main");

const S = {
  route: "home", out: localStorage.getItem("chhanni.out") || null, project: null,
  presets: {}, root: "", draft: { name: "", folders: [], preset: "balanced", settings: {}, scan: null },
  job: null, logSince: 0, logLines: [], results: null, review: null,
  filter: "all", group: "", q: "", sort: "name", limit: 40, tag: "",
};

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "show" + (err ? " err" : "");
  clearTimeout(t._t); t._t = setTimeout(() => t.className = "", err ? 6000 : 3000);
}
const fmtB = b => b > 1e12 ? (b / 1e12).toFixed(2) + " TB" : b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(0) + " MB";
const fmtT = s => { s = Math.max(0, Math.round(s || 0)); const hh = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), ss = s % 60;
  return hh ? `${hh}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`; };
const fmtETA = s => s == null ? "—" : s < 60 ? "< 1 min" : s < 3600 ? Math.round(s / 60) + " min" : (s / 3600).toFixed(1) + " h";
const grade = sc => sc >= 80 ? "good" : sc >= 65 ? "ok" : "weak";
const enc = encodeURIComponent;

const ICON = {
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h6l2 2h10v11H3z"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4v16l13-8z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  tune: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
  open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 4h6v6M20 4l-9 9M19 14v6H4V5h6"/></svg>',
};

/* ------------------------------------------------------------ routing */
function go(route, out) {
  if (out !== undefined) { S.out = out; localStorage.setItem("chhanni.out", out || ""); S.results = null; }
  location.hash = "#/" + route;
}
window.addEventListener("hashchange", render);
$$("#nav a").forEach(a => a.onclick = () => go(a.dataset.r));

async function render() {
  S.route = (location.hash.slice(2) || "home").split("/")[0];
  $$("#nav a").forEach(a => {
    a.classList.toggle("on", a.dataset.r === S.route);
    if (["run", "review", "export", "assemble", "deep", "library", "people", "crew"].includes(a.dataset.r)) a.classList.toggle("dis", !S.out);
  });
  if (S.out && (!S.project || S.project.out !== S.out)) {
    try { S.project = await api("/api/project?out=" + enc(S.out)); } catch { S.project = null; }
  }
  $("#navproj").textContent = S.project ? S.project.name : "No project open";
  main.scrollTop = 0;
  const pages = Object.assign({ home: pageHome, new: pageNew, run: pageRun, review: pageReview, export: pageExport, assemble: pageAssemble, ai: pageAI }, window.EXTRA_PAGES || {});
  (pages[S.route] || pageHome)();
}

/* ------------------------------------------------------------ home */
async function pageHome() {
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>Projects</h1><p>Har project = ek footage set aur uska analysis. Footage kahin move nahi hota.</p></div>
    <button class="btn primary big" id="np">${ICON.plus}New project</button></div><div class="projects" id="plist"><div class="empty">Loading…</div></div></div>`;
  $("#np").onclick = () => { S.draft = { name: "", folders: [], preset: "balanced", settings: {}, scan: null }; go("new"); };
  let d;
  try { d = await api("/api/projects"); } catch (e) { return toast(e.message, 1); }
  S.presets = d.presets; S.root = d.root;
  const cards = d.projects.map(p => {
    const s = p.summary || {};
    return `<div class="card proj" data-out="${h(p.out)}"><button class="btn ghost x" data-forget="${h(p.out)}" title="List se hatayein">${ICON.x}</button>
      <div class="nm">${h(p.name)}</div><div class="path">${h(p.out)}</div>
      ${p.summary ? `<div class="stats"><div class="stat"><b>${s.clips}</b><span>clips</span></div><div class="stat"><b>${s.hours} h</b><span>footage</span></div>
      <div class="stat"><b>${s.selects}</b><span>selects</span></div><div class="stat"><b>${s.select_min} m</b><span>selected</span></div></div>`
      : `<span class="chip warn"><i></i>Analysis pending</span>`}
      <div class="small muted" style="margin-top:10px">${p.summary?.generated ? "Updated " + h(p.summary.generated.replace("T", " ")) : ""}</div></div>`;
  }).join("");
  $("#plist").innerHTML = `<div class="card proj newproj" id="np2">${ICON.plus}<b>New project</b><span class="small">Footage folders chuniye aur analysis shuru kijiye</span></div>` + cards;
  $("#np2").onclick = $("#np").onclick;
  $$(".proj[data-out]").forEach(el => el.onclick = (ev) => {
    if (ev.target.closest("[data-forget]")) return;
    const p = d.projects.find(x => x.out === el.dataset.out);
    go(p.summary ? "review" : "run", el.dataset.out);
  });
  $$("[data-forget]").forEach(b => b.onclick = async () => { await api("/api/forget", { out: b.dataset.forget }); pageHome(); });
}

/* ------------------------------------------------------------ new project */
const SLIDERS = [
  ["shake_threshold", "Shake tolerance", 0.3, 2.5, 0.05, "Zyada = handheld movement zyada pass hoga"],
  ["blur_rel", "Blur strictness", 0.2, 0.8, 0.01, "Zyada = sirf sabse sharp hisse"],
  ["whip_threshold", "Whip-pan limit", 2, 14, 0.5, "Bahut tez pan isse upar reject"],
  ["min_select_sec", "Min select length (s)", 1, 6, 0.5, "Isse chhota hissa nahi liya jaayega"],
  ["broll_target_sec", "B-roll select length (s)", 3, 15, 0.5, "Lambe steady shot se itne second ke windows"],
  ["max_selects_per_clip", "Max selects per clip", 1, 15, 1, "Ek clip se kitne best hisse"],
  ["workers", "Parallel files", 1, 4, 1, "RTX 3060: 2–3 best"],
];
function sliderHTML(set) {
  return SLIDERS.map(([k, lab, mn, mx, st, hint]) => `<div class="sl"><div class="lab">${lab}<b id="v_${k}">${set[k]}</b></div>
    <input type="range" min="${mn}" max="${mx}" step="${st}" value="${set[k]}" data-k="${k}"><div class="hint">${hint}</div></div>`).join("");
}
function bindSliders(root, set, onchange) {
  $$("input[type=range]", root).forEach(r => r.oninput = () => {
    set[r.dataset.k] = +r.value; $("#v_" + r.dataset.k, root).textContent = r.value; onchange && onchange();
  });
}
async function pageNew() {
  if (!Object.keys(S.presets).length) { const d = await api("/api/projects"); S.presets = d.presets; S.root = d.root; }
  const D = S.draft;
  const base = { shake_threshold: 0.9, blur_rel: 0.45, whip_threshold: 6, min_select_sec: 2, broll_target_sec: 6, max_selects_per_clip: 6, workers: 2 };
  D.settings = Object.assign({}, base, S.presets[D.preset] ? pick(S.presets[D.preset]) : {}, D.settings);
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>New project</h1><p>Footage chuniye, preset chuniye, aur Start dabaiye. Beech mein rok kar baad mein resume kar sakte hain.</p></div></div>
  <div class="grid g2">
    <div class="card"><h3>1 · Footage</h3><div class="sub">Card / camera / din ke folders, ya poora project folder. Proxy folders apne aap pehchaane jaate hain.</div>
      <div class="field"><label>Project name</label><input class="input" id="pname" placeholder="e.g. Ajay Om - Movie Shoot" value="${h(D.name)}"></div>
      <div class="folders" id="flist"></div>
      <div class="row"><button class="btn" id="addf">${ICON.folder}Add folder</button>
        <input class="input" id="pastef" placeholder="…ya path paste karke Enter" style="flex:1;min-width:220px"></div>
      <div id="scan"></div></div>
    <div class="card"><h3>2 · Footage style</h3><div class="sub">Preset baad mein Review screen se bina dobara analysis ke badal sakte hain.</div>
      <div class="presets" id="presets"></div>
      <details class="adv"><summary>Advanced settings</summary><div class="sliders" id="sl">${sliderHTML(D.settings)}</div></details>
      <div class="field" style="margin-top:16px"><label>Project save location (sirf report / database — footage nahi)</label>
        <div class="row"><input class="input" id="proot" value="${h(D.root || S.root)}" style="flex:1"><button class="btn" id="broot">${ICON.folder}</button></div></div>
    </div>
  </div>
  <div class="row" style="margin-top:20px"><span class="sp"></span><span class="muted small" id="gohint"></span>
    <button class="btn primary big" id="start">${ICON.play}Start analysis</button></div></div>`;
  const drawFolders = () => {
    $("#flist").innerHTML = D.folders.length ? D.folders.map((f, i) => `<div class="folder">${ICON.folder}<span title="${h(f)}">${h(f)}</span>
      <button class="btn ghost" data-rm="${i}">${ICON.x}</button></div>`).join("") : `<div class="muted small">Abhi koi folder nahi.</div>`;
    $$("[data-rm]").forEach(b => b.onclick = () => { D.folders.splice(+b.dataset.rm, 1); D.scan = null; drawFolders(); doScan(); });
    $("#start").disabled = !D.folders.length;
  };
  const drawScan = () => {
    const s = D.scan;
    if (!s) { $("#scan").innerHTML = ""; return; }
    if (s.loading) { $("#scan").innerHTML = `<div class="scanbox"><span class="phase"><span class="dot"></span>Scanning…</span></div>`; return; }
    $("#scan").innerHTML = `<div class="scanbox"><div class="stat"><b>${s.files}</b><span>video files</span></div><div class="stat"><b>${fmtB(s.bytes)}</b><span>size</span></div>
      <div class="stat"><b>${s.with_proxy}</b><span>with proxy</span></div><div style="flex-basis:100%"></div>
      <div><span class="small muted">Cameras / cards</span><div class="groups">${s.groups.map(([g, n]) => `<span class="chip">${h(g)} · ${n}</span>`).join("")}</div></div></div>`;
  };
  const doScan = async () => {
    if (!D.folders.length) { D.scan = null; return drawScan(); }
    D.scan = { loading: true }; drawScan();
    try { D.scan = await api("/api/scan", { folders: D.folders }); } catch (e) { D.scan = null; toast(e.message, 1); }
    drawScan();
  };
  const addFolder = p => { p = (p || "").trim().replace(/^"|"$/g, ""); if (p && !D.folders.includes(p)) { D.folders.push(p); if (!D.name) { D.name = p.split(/[\\/]/).filter(Boolean).pop(); $("#pname").value = D.name; } drawFolders(); doScan(); } };
  $("#addf").onclick = async () => { const r = await api("/api/browse", { title: "Footage folder chuniye" }); if (r.path) addFolder(r.path); };
  $("#pastef").onkeydown = e => { if (e.key === "Enter") { addFolder(e.target.value); e.target.value = ""; } };
  $("#pname").oninput = e => D.name = e.target.value;
  $("#broot").onclick = async () => { const r = await api("/api/browse", { title: "Project save location" }); if (r.path) { D.root = r.path; $("#proot").value = r.path; } };
  $("#proot").oninput = e => D.root = e.target.value;
  const drawPresets = () => {
    $("#presets").innerHTML = Object.entries(S.presets).map(([k, p]) => `<div class="preset ${k === D.preset ? "on" : ""}" data-p="${k}"><b>${h(p.label)}</b><span>${h(p.hint)}</span></div>`).join("");
    $$("[data-p]").forEach(el => el.onclick = () => {
      D.preset = el.dataset.p; Object.assign(D.settings, pick(S.presets[D.preset]));
      $("#sl").innerHTML = sliderHTML(D.settings); bindSliders($("#sl"), D.settings); drawPresets();
    });
  };
  drawPresets(); bindSliders($("#sl"), D.settings); drawFolders(); drawScan();
  if (D.folders.length && !D.scan) doScan();
  $("#start").onclick = async () => {
    try {
      const r = await api("/api/start", { name: D.name, folders: D.folders, root: $("#proot").value, settings: Object.assign({ preset: D.preset }, D.settings) });
      S.project = null; go("run", r.out); toast("Analysis shuru ho gaya");
    } catch (e) { toast(e.message, 1); }
  };
}
function pick(p) { const o = {}; ["shake_threshold", "blur_rel", "whip_threshold"].forEach(k => { if (p[k] != null) o[k] = p[k]; }); return o; }

/* ------------------------------------------------------------ run */
function pageRun() {
  const p = S.project || {};
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>${h(p.name || "Analyze")}</h1><p class="small">${h(p.out || "")}</p></div>
    <div class="row" id="runbtns"></div></div>
    <div class="card"><div class="runhead">
      <div class="ring"><svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="52" stroke="#262b36" stroke-width="10" fill="none"/>
        <circle id="arc" cx="60" cy="60" r="52" stroke="url(#rg)" stroke-width="10" fill="none" stroke-linecap="round" stroke-dasharray="326.7" stroke-dashoffset="326.7"/>
        <defs><linearGradient id="rg"><stop offset="0" stop-color="#f2a93b"/><stop offset="1" stop-color="#ffbe5c"/></linearGradient></defs></svg>
        <div class="pct"><b id="pct">0%</b><span id="phase">idle</span></div></div>
      <div class="kv"><div><b id="k_files">—</b><span>files</span></div><div><b id="k_eta">—</b><span>time left</span></div>
        <div><b id="k_el">—</b><span>elapsed</span></div><div><b id="k_err">0</b><span>errors</span></div></div></div>
      <div class="current" id="cur"></div></div>
    <div class="card" style="margin-top:16px"><div class="row"><h3>Log</h3><span class="sp"></span><span class="small muted">Window band karne par bhi run background mein chalta rahega</span></div>
      <div class="console" id="console"></div></div>
    ${p.summary ? summaryCard(p.summary) : ""}</div>`;
  S.logLines = []; S.logSince = 0;
  updateRun();
}
function summaryCard(s) {
  return `<div class="card" style="margin-top:16px"><div class="row"><h3>Last result</h3><span class="sp"></span>
    <button class="btn primary" onclick="go('review')">Review selects</button></div>
    <div class="stats" style="margin-top:10px"><div class="stat"><b>${s.clips}</b><span>clips</span></div><div class="stat"><b>${s.hours} h</b><span>footage</span></div>
    <div class="stat"><b>${s.selects}</b><span>selects</span></div><div class="stat"><b>${s.select_min} min</b><span>selected</span></div>
    <div class="stat"><b>${s.a_roll}</b><span>A-roll clips</span></div><div class="stat"><b>${s.no_usable}</b><span>no usable</span></div></div></div>`;
}
function updateRun() {
  if (S.route !== "run" || !$("#arc")) return;
  const j = S.job || {}; const mine = j.out === S.out; const p = mine ? (j.progress || {}) : {};
  const running = mine && j.running;
  let pct = 0, phase = mine ? j.phase : "idle";
  if (p.phase === "analyze" && p.bytes_total) pct = p.bytes_done / p.bytes_total * 100;
  else if (p.phase === "transcribe" && p.total) pct = p.done / p.total * 100;
  else if (p.phase === "select" || p.phase === "thumbs") pct = 100;
  if (mine && j.phase === "finished") pct = 100;
  if (!running && !(mine && ["stopped", "error"].includes(j.phase)) && S.project?.summary) { pct = 100; phase = "finished"; }
  $("#arc").style.strokeDashoffset = 326.7 * (1 - pct / 100);
  $("#pct").textContent = Math.floor(pct) + "%";
  $("#phase").textContent = { analyze: "analyzing", transcribe: "transcribing", tags: "AI tagging", select: "selecting", thumbs: "thumbnails", scan: "scanning", finished: "done", stopped: "stopped", error: "error", starting: "starting" }[phase] || phase || "idle";
  if (p.total != null) {
    $("#k_files").textContent = `${p.done}/${p.total}`;
    $("#k_eta").textContent = fmtETA(p.eta); $("#k_el").textContent = fmtT(p.elapsed); $("#k_err").textContent = p.errors;
  }
  if (p.phase === "transcribe") { $("#cur").innerHTML = (p.current || []).map(c => `<span class="chip warn"><i></i>Whisper: ${h(c)}</span>`).join(""); }
  else if (p.phase === "tags" && p.sub) { $("#cur").innerHTML = `<span class="chip"><i></i>AI tags ${p.sub.done}/${p.sub.total}</span>`; }
  else if (p.phase === "analyze") {
    $("#cur").innerHTML = (p.current || []).map(c => `<span class="chip warn"><i></i>${h(c)}</span>`).join("") +
      (p.skipped_done ? `<span class="chip ok"><i></i>${p.skipped_done} pehle se done (resume)</span>` : "");
  } else if (p.phase === "thumbs" && p.sub) { $("#cur").innerHTML = `<span class="chip"><i></i>Thumbnails ${p.sub.done}/${p.sub.total}</span>`; }
  else if (p.phase === "select" && p.sub) { $("#cur").innerHTML = `<span class="chip"><i></i>Selecting ${p.sub.done}/${p.sub.total}</span>`; }
  else if (!running) $("#cur").innerHTML = "";
  const btns = $("#runbtns");
  const want = running ? "stop" : (j.phase === "finished" && mine) ? "done" : "start";
  if (btns.dataset.m !== want) {
    btns.dataset.m = want;
    btns.innerHTML = running ? `<button class="btn danger" id="stopb">${ICON.stop}Stop (progress saved)</button>`
      : `${S.project?.summary ? `<button class="btn" onclick="go('review')">Review selects</button>` : ""}
         <button class="btn primary" id="resb">${ICON.play}${S.project?.summary ? "Re-run / add new files" : "Start / Resume"}</button>`;
    $("#stopb") && ($("#stopb").onclick = async () => { await api("/api/stop", {}); toast("Rok rahe hain… progress save hai"); });
    $("#resb") && ($("#resb").onclick = async () => { try { await api("/api/resume", { out: S.out }); toast("Shuru"); } catch (e) { toast(e.message, 1); } });
  }
}
function appendLog(lines) {
  const c = $("#console"); if (!c) return;
  for (const l of lines) {
    const cls = /ERROR|\[error\]|failed/.test(l.m) ? "err" : /finished|\[select\]|\[done\]/.test(l.m) ? "ok" : "";
    const d = document.createElement("div"); if (cls) d.className = cls; d.textContent = l.m; c.appendChild(d);
  }
  while (c.childNodes.length > 600) c.removeChild(c.firstChild);
  c.scrollTop = c.scrollHeight;
}

/* ------------------------------------------------------------ polling */
let wasRunning = false;
async function poll() {
  try {
    const st = await api("/api/state?since=" + S.logSince);
    S.job = st.job;
    if (st.log.length) { S.logSince = st.log[st.log.length - 1].t; if (S.route === "run") appendLog(st.log); }
    $("#gpuchip").className = "chip " + (st.cuda ? "ok" : "warn");
    $("#gpuchip").innerHTML = `<i></i>${st.gpu ? h(st.gpu.replace("NVIDIA GeForce ", "")) + (st.cuda ? " · CUDA" : "") : st.cuda ? "NVIDIA CUDA" : "CPU decode"}`;
    $("#ver").textContent = "Chhanni " + st.version;
    const j = st.job;
    $("#minirun").innerHTML = j.running ? `<div class="mini-run" onclick="go(['deep','people'].includes('${j.mode}')?'deep':'run','${h(j.out).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}')"><div class="row small"><span class="phase"><span class="dot"></span>${h(j.phase)}</span><span class="sp"></span>${j.progress?.pct != null ? Math.round(j.progress.pct) + "%" : j.progress?.bytes_total ? Math.floor(j.progress.bytes_done / j.progress.bytes_total * 100) + "%" : ""}</div>
      <div class="bar"><div style="width:${j.progress?.bytes_total ? j.progress.bytes_done / j.progress.bytes_total * 100 : 3}%"></div></div></div>` : "";
    if (wasRunning && !j.running) {
      S.project = null; S.results = null;
      if (j.phase === "finished") toast(["deep", "people"].includes(j.mode) ? "Deep analysis ho gaya! Library / People / AI Team dekhiye." : "Ho gaya! Review screen par selects dekhiye.");
      if (j.phase === "error") toast("Error: " + j.error, 1);
      if (["run", "review", "deep", "people"].includes(S.route)) render();
    }
    wasRunning = j.running;
    updateRun();
  } catch (e) { $("#gpuchip").innerHTML = "<i></i>Server offline"; }
  setTimeout(poll, S.route === "run" ? 900 : 2000);
}

/* ------------------------------------------------------------ review */
async function pageReview() {
  if (!S.out) return go("home");
  main.innerHTML = `<div class="page"><div class="empty">Loading results…</div></div>`;
  if (!S.results) {
    try { S.results = await api("/api/results?out=" + enc(S.out)); S.review = S.results.review; }
    catch (e) { main.innerHTML = `<div class="page"><div class="card empty">Abhi results nahi hain. <a href="#/run">Analysis chalaiye</a>.</div></div>`; return; }
  }
  S.limit = 40;
  const groups = [...new Set(S.results.clips.map(c => c.group))].sort();
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>Review selects</h1><p>Thumbnail par click = select on/off · ★ = favourite · badge par click = A-roll/B-roll badlo</p></div>
      <div class="row"><button class="btn" id="tune">${ICON.tune}Tune &amp; re-select</button><button class="btn primary" onclick="go('export')">Send to NLE →</button></div></div>
    <div class="toolbar"><div class="seg" id="flt"></div>
      <select class="input" id="grp"><option value="">All cameras / cards</option>${groups.map(g => `<option ${g === S.group ? "selected" : ""}>${h(g)}</option>`).join("")}</select>
      <select class="input" id="srt"><option value="name">Sort: name</option><option value="score">Sort: best score</option><option value="usable">Sort: most usable</option><option value="worst">Sort: least usable</option></select>
      <select class="input" id="tagf"><option value="">All tags</option>${[...new Set(S.results.clips.flatMap(c => c.tags || []))].sort().map(t => `<option ${t === S.tag ? "selected" : ""}>${h(t)}</option>`).join("")}</select>
      <input class="input" id="q" placeholder="Search clip / words…" style="width:200px" value="${h(S.q)}"><span class="sp"></span><span id="tot" class="small muted"></span></div>
    <div class="legend" style="margin-bottom:12px"><span><i style="background:var(--good)"></i>good</span><span><i style="background:var(--shake)"></i>shaky / whip</span>
      <span><i style="background:var(--blur)"></i>blur</span><span><i style="background:var(--expo)"></i>dark / blown</span><span><i style="background:var(--sel)"></i>select</span><span><i style="background:var(--star)"></i>favourite</span></div>
    <div id="clips"></div><div class="more" id="more"></div></div>
    <div class="drawer" id="drawer"></div>`;
  $("#srt").value = S.sort;
  $("#grp").onchange = e => { S.group = e.target.value; S.limit = 40; drawClips(); };
  $("#srt").onchange = e => { S.sort = e.target.value; S.limit = 40; drawClips(); };
  $("#q").oninput = e => { S.q = e.target.value; S.limit = 40; drawClips(); };
  $("#tagf").onchange = e => { S.tag = e.target.value; S.limit = 40; drawClips(); };
  $("#tune").onclick = openTune;
  drawClips();
}
function cid(c) { return c.id || c.path; }
function selKey(s) { return [s.start, s.end]; }
function inRanges(s, rs) { return (rs || []).some(([a, b]) => Math.min(s.end, b) - Math.max(s.start, a) > 0.5 * (s.end - s.start)); }
function clipState(c) {
  const R = S.review, id = cid(c);
  const excluded = R.clip_off.includes(id);
  const type = R.type[id] || c.type;
  const sels = c.selects.map(s => ({ ...s, off: excluded || inRanges(s, R.off[id]), star: inRanges(s, R.star[id]) }));
  return { id, excluded, type, sels, active: sels.filter(s => !s.off) };
}
function visibleClips() {
  let cl = S.results.clips.map(c => ({ c, st: clipState(c) }));
  const counts = { all: cl.length, A: 0, B: 0, none: 0, excluded: 0 };
  cl.forEach(({ c, st }) => { if (st.excluded) counts.excluded++; else if (!c.selects.length) counts.none++; else counts[st.type]++; });
  if (S.group) cl = cl.filter(x => x.c.group === S.group);
  if (S.tag) cl = cl.filter(x => x.c.selects.some(s => (s.tags || []).includes(S.tag)));
  if (S.q) { const q = S.q.toLowerCase(); cl = cl.filter(x => x.c.name.toLowerCase().includes(q) || x.c.path.toLowerCase().includes(q) || x.c.selects.some(s => (s.text || "").toLowerCase().includes(q))); }
  cl = cl.filter(({ c, st }) => S.filter === "all" ? true : S.filter === "excluded" ? st.excluded : S.filter === "none" ? !c.selects.length && !st.excluded : !st.excluded && c.selects.length && st.type === S.filter);
  const best = x => Math.max(0, ...x.st.active.map(s => s.score));
  if (S.sort === "score") cl.sort((a, b) => best(b) - best(a));
  if (S.sort === "usable") cl.sort((a, b) => b.c.stats.usable_pct - a.c.stats.usable_pct);
  if (S.sort === "worst") cl.sort((a, b) => a.c.stats.usable_pct - b.c.stats.usable_pct);
  return { cl, counts };
}
function drawClips() {
  const { cl, counts } = visibleClips();
  const F = [["all", "All"], ["A", "A-roll"], ["B", "B-roll"], ["none", "No usable"], ["excluded", "Excluded"]];
  $("#flt").innerHTML = F.map(([k, l]) => `<button class="${S.filter === k ? "on" : ""}" data-f="${k}">${l}<em>${counts[k]}</em></button>`).join("");
  $$("#flt button").forEach(b => b.onclick = () => { S.filter = b.dataset.f; S.limit = 40; drawClips(); });
  let nsel = 0, secs = 0;
  S.results.clips.forEach(c => clipState(c).active.forEach(s => { nsel++; secs += s.end - s.start; }));
  $("#tot").textContent = `${nsel} selects active · ${(secs / 60).toFixed(1)} min`;
  $("#clips").innerHTML = cl.slice(0, S.limit).map(x => clipHTML(x.c, x.st)).join("") || `<div class="card empty">Is filter mein koi clip nahi.</div>`;
  $("#more").innerHTML = cl.length > S.limit ? `<button class="btn" id="mb">Show more (${cl.length - S.limit})</button>` : "";
  $("#mb") && ($("#mb").onclick = () => { S.limit += 60; drawClips(); });
  if ($("#mb")) new IntersectionObserver((es, o) => { if (es[0].isIntersecting) { o.disconnect(); S.limit += 60; drawClips(); } }).observe($("#mb"));
  bindClipEvents();
}
function clipHTML(c, st) {
  const dur = c.duration || 1, tot = c.qc.reduce((a, [, n]) => a + n, 0) || 1;
  const col = ["var(--good)", "var(--shake)", "var(--blur)", "var(--expo)"];
  const q = c.qc.map(([k, n]) => `<div style="width:${n / tot * 100}%;background:${col[k]}"></div>`).join("");
  const sb = st.sels.map(s => `<div class="s ${s.off ? "off" : ""} ${s.star && !s.off ? "star" : ""}" style="left:${s.start / dur * 100}%;width:${Math.max(.4, (s.end - s.start) / dur * 100)}%"></div>`).join("");
  const cuts = (c.cuts || []).map(t => `<div class="c" style="left:${t / dur * 100}%"></div>`).join("");
  const tu = n => `/thumbs/${enc(n)}?out=${enc(S.out)}`;
  const ths = st.sels.length ? st.sels.map((s, i) => `<div class="th ${s.off ? "off" : ""} ${s.star && !s.off ? "starred" : ""}" data-c="${h(st.id)}" data-i="${i}" title="Click: on/off">
      <img loading="lazy" src="${tu(s.thumb || "")}" onerror="this.style.opacity=0"><span class="sc ${grade(s.score)}">${s.score}</span>
      <span class="st ${s.star ? "on" : ""}" data-star="1" title="Favourite">★</span>${tagChips(s)}<div class="cap"><span>${fmtT(s.start)}–${fmtT(s.end)}</span><span>${(s.end - s.start).toFixed(1)}s</span></div></div>`).join("")
    : `<div class="th poster"><img loading="lazy" src="${tu(c.poster || "")}" onerror="this.style.opacity=0"><div class="cap"><span>No usable section</span></div></div>`;
  const s = c.stats;
  const badge = st.excluded ? `<span class="badge X">EXCLUDED</span>` : `<span class="badge ${st.type}" data-type="${h(st.id)}" title="Click: A-roll / B-roll">${st.type === "A" ? "A-ROLL" : "B-ROLL"}</span>`;
  return `<div class="clip ${st.excluded ? "excluded" : ""}"><div class="hd">${badge}<span class="nm">${h(c.name)}</span>
    <span class="meta">${fmtT(c.duration)} · ${c.width}×${c.height} · ${c.fps}fps${c.proxy ? " · proxy" : ""}</span><span class="grp">${h(c.group)}</span>
    <div class="acts"><button class="btn ghost small" data-reveal="${h(c.path)}" title="Explorer mein dikhayein">${ICON.open}</button>
    <button class="btn ghost small" data-excl="${h(st.id)}">${st.excluded ? "Include" : "Exclude"}</button></div></div>
    <div class="strip"><div class="q">${q}</div>${cuts}${sb}</div>
    <div class="cstats"><span>usable <b>${s.usable_pct}%</b></span><span>shaky ${s.shaky_pct}%</span><span>blur ${s.blur_pct}%</span><span>exposure ${s.exposure_bad_pct}%</span><span>speech ${Math.round(s.speech_ratio * 100)}%</span><span>cuts ${s.scene_cuts}</span></div>
    ${s.transcript ? `<div class="cstats" style="margin-top:4px"><span>transcript (${h(s.transcript.language || "")}): <b>${s.transcript.retakes}</b> retakes, <b>${s.transcript.false_starts}</b> false starts, <b>${s.transcript.fillers}</b> fillers hataye${s.transcript.clipping ? `, <b style="color:#ff9aa0">${s.transcript.clipping} audio clipping</b>` : ""}</span></div>` : ""}
    <div class="thumbs">${ths}</div>${st.type === "A" ? st.sels.filter(x => x.text).slice(0, 6).map(x => `<div class="saytext">${fmtT(x.start)} · ${h(x.text.slice(0, 220))}${(x.flags || []).length ? ` <span class="pill bad">${h(x.flags.join(", "))}</span>` : ""}</div>`).join("") : ""}</div>`;
}
function findClip(id) { return S.results.clips.find(c => cid(c) === id); }
function toggleRange(map, id, s, on) {
  const rs = (map[id] || []).filter(([a, b]) => !(Math.min(s.end, b) - Math.max(s.start, a) > 0.5 * (s.end - s.start)));
  if (on) rs.push(selKey(s));
  if (rs.length) map[id] = rs; else delete map[id];
}
let saveT;
function saveReview() { clearTimeout(saveT); saveT = setTimeout(() => api("/api/review", { out: S.out, review: S.review }).catch(e => toast(e.message, 1)), 400); }
function bindClipEvents() {
  $$(".th[data-c]").forEach(el => el.onclick = (ev) => {
    const c = findClip(el.dataset.c), s = c.selects[+el.dataset.i], R = S.review, id = cid(c);
    if (ev.target.closest("[data-star]")) toggleRange(R.star, id, s, !inRanges(s, R.star[id]));
    else toggleRange(R.off, id, s, !inRanges(s, R.off[id]));
    saveReview(); redrawOne(el.closest(".clip"), c);
  });
  $$("[data-type]").forEach(el => el.onclick = () => {
    const c = findClip(el.dataset.type), id = cid(c), cur = S.review.type[id] || c.type, nx = cur === "A" ? "B" : "A";
    if (nx === c.type) delete S.review.type[id]; else S.review.type[id] = nx;
    saveReview(); redrawOne(el.closest(".clip"), c);
  });
  $$("[data-excl]").forEach(el => el.onclick = () => {
    const id = el.dataset.excl, L = S.review.clip_off, i = L.indexOf(id);
    if (i >= 0) L.splice(i, 1); else L.push(id);
    saveReview(); redrawOne(el.closest(".clip"), findClip(id));
  });
  $$("[data-reveal]").forEach(el => el.onclick = () => api("/api/reveal", { path: el.dataset.reveal }));
}
function redrawOne(node, c) {
  const tmp = document.createElement("div"); tmp.innerHTML = clipHTML(c, clipState(c));
  node.replaceWith(tmp.firstElementChild); bindClipEvents();
  let nsel = 0, secs = 0; S.results.clips.forEach(c => clipState(c).active.forEach(s => { nsel++; secs += s.end - s.start; }));
  $("#tot").textContent = `${nsel} selects active · ${(secs / 60).toFixed(1)} min`;
}
function openTune() {
  const set = Object.assign({}, S.results.settings);
  const d = $("#drawer");
  d.innerHTML = `<div class="row"><h3 style="margin:0">Tune &amp; re-select</h3><span class="sp"></span><button class="btn ghost" id="dx">${ICON.x}</button></div>
    <p class="small muted">Analysis dobara nahi hota — naye selects seconds mein ban jaate hain. Aapke on/off aur ★ choices bani rehti hain.</p>
    <div class="presets" style="grid-template-columns:1fr">${Object.entries(S.presets).map(([k, p]) => `<div class="preset ${set.preset === k ? "on" : ""}" data-p="${k}"><b>${h(p.label)}</b><span>${h(p.hint)}</span></div>`).join("")}</div>
    <div class="sliders" id="dsl">${sliderHTML(set)}</div>
    <div class="row" style="margin-top:18px"><span class="sp"></span><button class="btn primary" id="rs">Re-select</button></div>`;
  d.classList.add("open");
  bindSliders(d, set);
  $$("[data-p]", d).forEach(el => el.onclick = () => { set.preset = el.dataset.p; Object.assign(set, pick(S.presets[set.preset])); $("#dsl").innerHTML = sliderHTML(set); bindSliders(d, set);
    $$("[data-p]", d).forEach(x => x.classList.toggle("on", x === el)); });
  $("#dx").onclick = () => d.classList.remove("open");
  $("#rs").onclick = async () => {
    try { await api("/api/reselect", { out: S.out, settings: set }); d.classList.remove("open"); toast("Re-selecting…"); main.innerHTML = `<div class="page"><div class="card empty"><span class="phase"><span class="dot"></span>Naye selects ban rahe hain…</span></div></div>`; wasRunning = true; S.results = null; }
    catch (e) { toast(e.message, 1); }
  };
}
if (!Object.keys(S.presets).length) api("/api/projects").then(d => { S.presets = d.presets; S.root = d.root; }).catch(() => {});

/* ------------------------------------------------------------ export */
function pageExport() {
  if (!S.out) return go("home");
  const o = JSON.parse(localStorage.getItem("chhanni.exp") || '{"group_by":"group","A":true,"B":true,"slowmo":true}');
  const save = () => localStorage.setItem("chhanni.exp", JSON.stringify(o));
  const inc = () => [o.A && "A", o.B && "B"].filter(Boolean);
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>Send to NLE</h1><p>Original camera files link hote hain — proxies sirf analysis ke liye thi. Aapke Review choices apply hote hain.</p></div></div>
  <div class="card" style="margin-bottom:16px"><h3>Timeline options</h3><div class="row" style="gap:28px;margin-top:8px">
    <div><div class="small muted" style="margin-bottom:6px">Timelines</div><div class="seg" id="gb">
      <button data-g="group">Per camera / card</button><button data-g="folder">Per folder</button><button data-g="none">One per type</button></div></div>
    <label class="switch"><input type="checkbox" id="iA" ${o.A ? "checked" : ""}><i></i>A-roll (interviews)</label>
    <label class="switch"><input type="checkbox" id="iB" ${o.B ? "checked" : ""}><i></i>B-roll</label>
    <label class="switch"><input type="checkbox" id="sm" ${o.slowmo ? "checked" : ""}><i></i>High-fps (S&amp;Q) clips slow-motion mein</label></div></div>
  <div class="grid g3">
    <div class="card exp"><h3><span class="logo" style="background:#1b2a3a;color:#7cc4ff">DR</span>DaVinci Resolve</h3><div class="sub">Bins (A-roll / B-roll / No usable), timelines, clip colours, select markers.</div>
      <ol class="steps"><li>Resolve mein project kholiye</li><li><span class="kbd">Workspace › Scripts › Chhanni</span> → Import</li></ol>
      <div class="row"><button class="btn primary" id="toR">Send to Resolve now</button><button class="btn" id="toSB">Smart bins → Resolve</button></div>
      <div class="small muted" style="margin-top:8px">"Send now" ke liye Resolve Preferences › System › General › External scripting = <b>Local</b>. Warna panel use kijiye.</div></div>
    <div class="card exp"><h3><span class="logo" style="background:#2a1b3a;color:#d2a8ff">Pr</span>Premiere Pro</h3><div class="sub">Bins, sequences, clip labels aur markers — Chhanni panel se ek click.</div>
      <ol class="steps"><li>Premiere mein project kholiye</li><li><span class="kbd">Window › Extensions › Chhanni</span></li><li>Project chuniye → <b>Import</b></li></ol>
      <div class="small muted">Panel na dikhe to Premiere restart kijiye (setup ne install kar diya hai).</div></div>
    <div class="card exp"><h3><span class="logo" style="background:#2b2b1b;color:#ffe08a">XML</span>XML export</h3><div class="sub">FCP7 XML — Premiere, Resolve, Final Cut Pro 7, Avid (via conversion) mein import.</div>
      <div class="row" style="margin-top:14px"><button class="btn primary" id="toX">Export XML</button></div><div id="xres" class="small muted" style="margin-top:10px"></div></div>
  </div>
  <div class="card" style="margin-top:16px;display:none" id="rlog"><h3>Resolve import</h3><div class="console" style="height:180px" id="rcon"></div></div></div>`;
  $$("#gb button").forEach(b => { b.classList.toggle("on", b.dataset.g === o.group_by); b.onclick = () => { o.group_by = b.dataset.g; save(); $$("#gb button").forEach(x => x.classList.toggle("on", x === b)); }; });
  $("#iA").onchange = e => { o.A = e.target.checked; save(); }; $("#iB").onchange = e => { o.B = e.target.checked; save(); };
  $("#sm").onchange = e => { o.slowmo = e.target.checked; save(); };
  $("#toX").onclick = async () => {
    try { const r = await api("/api/export", { out: S.out, kind: "xml", group_by: o.group_by, include: inc() });
      $("#xres").innerHTML = `${r.sequences} sequences → <a href="#" id="rv">${h(r.path)}</a>`; $("#rv").onclick = e => { e.preventDefault(); api("/api/reveal", { path: r.path }); }; toast("XML ready"); }
    catch (e) { toast(e.message, 1); }
  };
  $("#toSB").onclick = async () => {
    try { const r = await api("/api/smartbins", { out: S.out }); $("#rlog").style.display = ""; $("#rcon").textContent = r.log.join("\n"); toast("Smart bins ban gaye"); }
    catch (e) { toast(e.message, 1); }
  };
  $("#toR").onclick = async () => {
    $("#toR").disabled = true; $("#toR").textContent = "Sending…";
    try { const r = await api("/api/export", { out: S.out, kind: "resolve", group_by: o.group_by, include: inc(), slowmo: o.slowmo });
      $("#rlog").style.display = ""; $("#rcon").textContent = r.log.join("\n"); toast("Resolve mein import ho gaya"); }
    catch (e) { toast(e.message, 1); }
    $("#toR").disabled = false; $("#toR").textContent = "Send to Resolve now";
  };
}

/* ------------------------------------------------------------ boot */
render(); poll();

/* ------------------------------------------------------------ v3 helpers */
const SHORT = { CU: "CU", MCU: "MCU", MS: "MS", WS: "WS", WIDE: "WIDE" };
function tagChips(s) {
  const t = [];
  if (s.is_hero) t.push(`<span class="tg hero">★ HERO ${s.hero}</span>`);
  if (s.shot) t.push(`<span class="tg">${SHORT[s.shot] || s.shot}</span>`);
  if (s.move && !["Unknown", "Handheld"].includes(s.move)) t.push(`<span class="tg mv">${h(s.move.split(" (")[0].split(" /")[0])}</span>`);
  (s.tags || []).filter(x => ["Golden hour", "Blue hour", "Night / low light", "Rich dynamic range"].includes(x)).forEach(x => t.push(`<span class="tg gold">${h(x.split(" /")[0])}</span>`));
  (s.tags || []).filter(x => !/Close|Medium|Wide|Static|Slider|Smooth|Handheld|Drone|Golden|Blue|Night|dynamic|Hero|Vivid/.test(x)).slice(0, 2).forEach(x => t.push(`<span class="tg">${h(x)}</span>`));
  return t.length ? `<div class="tags">${t.join("")}</div>` : "";
}

/* ------------------------------------------------------------ AI setup */
async function pageAI() {
  main.innerHTML = `<div class="page"><div class="empty">Loading…</div></div>`;
  let st; try { st = await api("/api/ai/status" + (S.out ? "?out=" + enc(S.out) : "")); } catch (e) { return toast(e.message, 1); }
  let up = {}; try { up = await api("/api/update/status"); } catch {}
  const s = st.settings || {}, uc = up.config || {};
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>Settings</h1><p>Updates, AI models aur LLM. Sab kuch local aur free chal sakta hai.</p></div></div>
  <div class="card" style="margin-bottom:16px"><div class="row"><div><h3>⬆️ Updates</h3><div class="sub" style="margin:0">Installed: <b>Chhanni ${h(up.local || "?")}</b>${up.remote ? ` · GitHub par: <b>${h(up.remote)}</b>` : ""}${up.checked ? ` · checked ${h(up.checked)}` : ""}</div></div><span class="sp"></span>
    ${up.available ? `<button class="btn primary" id="upgo">Update to ${h(up.remote)}</button>` : ""}<button class="btn" id="upchk">Check now</button><button class="btn ghost" id="uprb" title="Pichla version wapas">Rollback</button></div>
    ${up.available && up.notes ? `<div class="small" style="margin-top:8px;color:#ffd166">Naya: ${h(up.notes)}</div>` : ""}
    <div class="grid g3" style="margin-top:12px"><div class="field"><label>GitHub repo (owner/name)</label><input class="input" id="uprepo" placeholder="aapka-username/chhanni" value="${h(uc.repo || "")}"></div>
      <div class="field"><label>Token (sirf private repo ke liye) ${uc.has_token ? '<span class="muted">(saved)</span>' : ""}</label><input class="input" id="uptok" type="password" placeholder="${uc.has_token ? "unchanged" : "public repo ho to khali chhodiye"}"></div>
      <div class="field"><label>&nbsp;</label><label class="switch"><input type="checkbox" id="upauto" ${uc.auto !== false ? "checked" : ""}><i></i>App khulte hi update check karo</label></div></div>
    <div class="console" id="uplog" style="height:auto;max-height:160px;display:none"></div></div>
  <div class="status" style="margin-bottom:16px">
    <div class="st-card"><b>${st.whisper ? "✅" : "⬜"} Whisper (transcription)</b><span class="small muted">faster-whisper · local GPU · Hindi / English / Hinglish</span></div>
    <div class="st-card"><b>${st.clip ? "✅" : "⬜"} CLIP (visual AI tags)</b><span class="small muted">drone, rooms, scenes, aesthetic score · local GPU</span></div>
    <div class="st-card"><b>✅ Built-in QC &amp; tags</b><span class="small muted">shake, blur, exposure, shot size, movement, golden hour, hero score</span></div></div>
  ${!st.whisper || !st.clip ? `<div class="card" style="margin-bottom:16px"><div class="row"><div><h3>AI pack install kijiye</h3><div class="sub" style="margin:0">~3–4 GB download (PyTorch CUDA, faster-whisper, CLIP). Ek baar. Models pehli baar use par download honge.</div></div><span class="sp"></span><button class="btn primary" id="inst">Install AI pack</button></div></div>` : ""}
  <div class="grid g2">
    <div class="card"><h3>Transcription</h3><div class="sub">Model aur bhasha. large-v3 Hindi ke liye sabse accurate; turbo 3–4× tez.</div>
      <div class="field"><label>Whisper model</label><select class="input" id="wm"><option value="large-v3">large-v3 (best accuracy)</option><option value="large-v3-turbo">large-v3-turbo (fast)</option><option value="medium">medium (low VRAM)</option></select></div>
      <div class="field"><label>Language</label><select class="input" id="lg"><option value="auto">Auto detect</option><option value="hi">Hindi</option><option value="en">English</option><option value="ur">Urdu</option><option value="pa">Punjabi</option><option value="gu">Gujarati</option><option value="mr">Marathi</option><option value="bn">Bengali</option><option value="ta">Tamil</option><option value="te">Telugu</option></select></div>
      <div class="field"><label>CLIP visual tags</label><select class="input" id="uc"><option value="auto">Auto (install hai to use karo)</option><option value="false">Off</option></select></div>
      ${S.out ? `<div class="row"><button class="btn primary" id="tr">Transcribe "${h(S.project?.name || "")}"</button><span class="small muted">${st.transcripts || 0} clips transcribed</span></div>` : `<div class="small muted">Transcribe ke liye pehle ek project kholiye.</div>`}</div>
    <div class="card"><h3>LLM (optional) — story from concept</h3><div class="sub">Sirf "concept se story" ke liye. Script mapping bina LLM ke bhi chalti hai.</div>
      <div class="field"><label>Provider</label><select class="input" id="pv"><option value="none">None (rule-based, free)</option><option value="ollama">Ollama (local, free)</option><option value="gemini">Google Gemini (free tier key)</option><option value="claude">Anthropic Claude (API key)</option><option value="openai">OpenAI-compatible (LM Studio / Groq / OpenRouter)</option></select></div>
      <div class="field"><label>Model</label><input class="input" id="md" placeholder=""></div>
      <div class="field"><label>API key ${s.has_key ? `<span class="muted">(saved: ${h(s.api_key)})</span>` : ""}</label><input class="input" id="ky" type="password" placeholder="${s.has_key ? "unchanged" : "paste key (Ollama ke liye zaroorat nahi)"}"></div>
      <div class="field"><label>Base URL (Ollama / OpenAI-compatible)</label><input class="input" id="bu" placeholder="http://127.0.0.1:11434" value="${h(s.base_url || "")}"></div>
      <div class="row"><button class="btn primary" id="sv">Save</button><button class="btn" id="tl">Test</button><span class="small muted" id="tlr"></span></div></div></div></div>`;
  $("#wm").value = s.whisper_model || "large-v3"; $("#lg").value = s.language || "auto"; $("#uc").value = String(s.use_clip ?? "auto");
  $("#pv").value = s.llm_provider || "none"; $("#md").value = s.llm_model || ""; $("#md").placeholder = st.defaults[$("#pv").value] || "";
  $("#pv").onchange = () => $("#md").placeholder = st.defaults[$("#pv").value] || "";
  const save = async () => { await api("/api/ai/settings", { whisper_model: $("#wm").value, language: $("#lg").value, use_clip: $("#uc").value === "false" ? false : "auto",
    llm_provider: $("#pv").value, llm_model: $("#md").value, api_key: $("#ky").value || null, base_url: $("#bu").value }); toast("Saved"); };
  $("#sv").onclick = save; ["wm", "lg", "uc"].forEach(i => $("#" + i).onchange = save);
  $("#tl").onclick = async () => { await save(); $("#tlr").textContent = "testing…"; try { await api("/api/ai/test_llm", {}); $("#tlr").textContent = "✅ working"; } catch (e) { $("#tlr").textContent = "❌ " + e.message; } };
  const saveUp = async () => api("/api/update/config", { repo: $("#uprepo").value.trim(), token: $("#uptok").value || null, auto: $("#upauto").checked });
  $("#uprepo").onchange = saveUp; $("#upauto").onchange = saveUp; $("#uptok").onchange = saveUp;
  $("#upchk").onclick = async () => { try { await saveUp(); const r = await api("/api/update/check", {}); toast(r.available ? `Naya version ${r.remote} available` : r.configured ? "Aap latest version par hain" : "Pehle GitHub repo daaliye"); pageAI(); } catch (e) { toast(e.message, 1); } };
  $("#upgo") && ($("#upgo").onclick = () => doUpdate());
  $("#uprb").onclick = async () => { if (!confirm("Pichla version wapas laayein?")) return; try { await api("/api/update/rollback", {}); toast("Rollback ho gaya - engine restart ho raha hai"); setTimeout(() => location.reload(), 4000); } catch (e) { toast(e.message, 1); } };
  $("#inst") && ($("#inst").onclick = async () => { await api("/api/ai/install", {}); toast("Installer window khul gayi - khatam hone ke baad Chhanni restart kijiye"); });
  $("#tr") && ($("#tr").onclick = async () => { await save(); try { await api("/api/transcribe", { out: S.out }); go("run"); toast("Transcription shuru"); } catch (e) { toast(e.message, 1); } });
}

/* ------------------------------------------------------------ Script & assemble */
const MODES = [
  ["reel", "📱", "Reel / Promo", "Talking-head reel: tight cuts, zoom, captions, hook, CTA"],
  ["documentary", "🎙️", "Documentary", "Script / outline order, best takes + B-roll coverage"],
  ["film", "🎬", "Film scene", "Dialogue lines → best take, angle variety"],
  ["music_video", "🎵", "Music video", "Cuts on beats, energy-driven"],
  ["real_estate", "🏠", "Real estate", "Aerial → exterior → rooms, smooth moves"],
  ["hero_reel", "⭐", "Hero reel", "Best shots, beat-snapped"],
];
async function pageAssemble() {
  if (!S.out) return go("home");
  const A = S.asm || (S.asm = { mode: "reel", music: "", concept: false, vid: null });
  const RO = ropts();
  let script = ""; try { script = (await api("/api/script?out=" + enc(S.out))).text; } catch {}
  let vs = []; try { vs = (await api("/api/versions?out=" + enc(S.out))).versions; } catch {}
  main.innerHTML = `<div class="page"><div class="ph"><div><h1>Script &amp; assemble</h1><p>Script daaliye → features ON/OFF kijiye → versions banaiye → instruction se sudhaariye → NLE mein bhejiye.</p></div></div>
  <div class="card" style="margin-bottom:16px"><h3>1 · Mode</h3><div class="modes" id="modes" style="margin-top:10px;grid-template-columns:repeat(6,1fr)">${MODES.map(([k, ic, t, d]) => `<div class="mode ${A.mode === k ? "on" : ""}" data-m="${k}"><span class="ic">${ic}</span><b>${t}</b><span>${d}</span></div>`).join("")}</div></div>
  <div class="grid g2"><div class="card"><div class="row"><h3>2 · Script / outline</h3><span class="sp"></span><button class="btn ghost small" id="ld">Load .txt</button></div>
      <div class="sub">Formats: <span class="kbd">NAME: line</span> <span class="kbd">*[Staff 1]* line</span> <span class="kbd">[BASEMENT]</span> <span class="kbd">SHOT: …</span> <span class="kbd"># Section</span> <span class="kbd">(5s)</span>. Hindi, Hinglish, English. Script khali chhodein to reel saari achhi lines se banegi.</div>
      <textarea class="input" id="sc" placeholder="# Opening&#10;[drone shot of the fort at sunset]&#10;Raju: Yeh kila teen sau saal purana hai">${h(script)}</textarea>
      <label class="switch" style="margin-top:10px"><input type="checkbox" id="cc" ${A.concept ? "checked" : ""}><i></i>Ye sirf concept / brief hai — LLM interviews se story banaye</label></div>
    <div class="card" id="asmside"></div></div>
  <div class="card" style="margin-top:16px" id="featcard"></div>
  <div class="card" style="margin-top:16px"><h3>5 · Versions</h3><div class="vtabs" id="vtabs" style="margin-top:10px"></div><div id="vview"></div></div></div>`;
  const drawSide = () => {
    const reel = A.mode === "reel";
    $("#asmside").innerHTML = reel ? `<h3>3 · Generate</h3><div class="sub">Neeche ke switches se reel ka style tay hota hai. Baad mein version ke neeche instruction likh kar badal sakte hain.</div>
        <div class="small muted" style="margin:10px 0">Current: ${RO.aspect} · ${RO.target_on ? RO.target_sec + "s" : "auto length"} · B-roll ${RO.broll_on ? RO.broll : "off"} · zoom ${RO.zoom ? "on" : "off"} · captions ${RO.captions ? "on" : "off"}</div>
        <div class="row" style="margin-top:18px"><span class="sp"></span><button class="btn primary big" id="gen">Generate reel</button></div><div class="small muted" id="genmsg" style="margin-top:8px"></div>`
      : `<h3>3 · Music &amp; options</h3><div class="sub">Music video / real estate / hero reel ke liye music track.</div>
        <div class="row"><input class="input" id="mu" placeholder="Music file (wav / mp3)" value="${h(A.music)}" style="flex:1"><button class="btn" id="bm">Browse</button></div>
        <div class="field" style="margin-top:14px"><label>Real estate shot length (s)</label><input class="input" id="sl" type="number" value="${A.shot_sec || 4}" step="0.5" min="2" max="8" style="width:120px"></div>
        <div class="row" style="margin-top:18px"><span class="sp"></span><button class="btn primary big" id="gen">Generate versions</button></div><div class="small muted" id="genmsg" style="margin-top:8px"></div>`;
    $("#featcard").style.display = reel ? "" : "none";
    if (reel) { $("#featcard").innerHTML = `<h3>4 · Features (ON / OFF)</h3>${featuresHTML(RO, "f_")}`; bindFeatures($("#featcard"), RO, o => { saveRopts(o); }); }
    $("#mu") && ($("#mu").oninput = e => A.music = e.target.value);
    $("#bm") && ($("#bm").onclick = async () => { const r = await api("/api/browse_file", { title: "Music file", kind: "audio" }); if (r.path) { A.music = r.path; $("#mu").value = r.path; } });
    $("#gen").onclick = generate;
  };
  $$("#modes .mode").forEach(el => el.onclick = () => { A.mode = el.dataset.m; $$("#modes .mode").forEach(x => x.classList.toggle("on", x === el)); drawSide(); });
  $("#cc").onchange = e => A.concept = e.target.checked;
  $("#ld").onclick = async () => { const r = await api("/api/browse_file", { title: "Script (.txt)", kind: "text" }); if (r.path?.text) $("#sc").value = r.path.text; };
  $("#sc").onchange = () => api("/api/script", { out: S.out, text: $("#sc").value }).catch(() => {});
  const drawTabs = () => {
    const sel = vs.find(v => v.id === A.vid);
    const latest = vs[0]?.batch;
    const shown = A.allv ? vs : vs.filter(v => v.batch === latest || v.id === A.vid || (sel && v.root === sel.root));
    $("#vtabs").innerHTML = (vs.length ? shown.slice(0, 60).map(v => `<div class="vtab ${v.id === A.vid ? "on" : ""}" data-v="${v.id}">${h(v.name)}${v.instruction ? `<span class="vtag" title="${h(v.instruction)}">✎</span>` : ""}<small>${h(v.mode)} · ${fmtT(v.duration)} · ${v.n_items || ""} clips · ${h(v.created || "")}</small></div>`).join("") : `<span class="muted small">Abhi koi version nahi.</span>`)
      + (vs.length > shown.length || A.allv ? `<button class="btn ghost small" id="allv">${A.allv ? "Sirf latest" : `Purane versions (${vs.length - shown.length})`}</button>` : "")
      + (vs.length > 1 ? `<button class="btn ghost small" id="delold">Purane versions delete karo</button>` : "");
    $("#allv") && ($("#allv").onclick = () => { A.allv = !A.allv; drawTabs(); });
    $("#delold") && ($("#delold").onclick = async () => {
      const cur = vs.find(v => v.id === A.vid);
      const keep = new Set(vs.filter(v => v.batch === latest || v.id === A.vid || (cur && v.root && v.root === cur.root)).map(v => v.id));
      const ids = vs.filter(v => !keep.has(v.id)).map(v => v.id);
      if (!ids.length) return toast("Delete karne ko kuch nahi");
      await api("/api/delete_version", { out: S.out, ids }); vs = vs.filter(v => keep.has(v.id)); drawTabs(); toast(`${ids.length} purane versions delete`);
    });
    $$("#vtabs .vtab").forEach(el => el.onclick = () => { A.vid = el.dataset.v; drawTabs(); showVersion(A.vid, refresh); });
  };
  const refresh = async (newId) => { vs = (await api("/api/versions?out=" + enc(S.out))).versions; if (newId !== undefined) A.vid = newId; drawTabs(); if (A.vid) showVersion(A.vid, refresh); else $("#vview").innerHTML = ""; };
  async function generate() {
    $("#gen").disabled = true; $("#genmsg").textContent = A.concept ? "LLM story bana raha hai… (1–2 min)" : "Takes match + cuts bana rahe hain…";
    try {
      const reel = A.mode === "reel";
      const body = { out: S.out, mode: A.mode, script: $("#sc").value, concept: A.concept,
        music: reel ? (RO.music_on && RO.music ? RO.music : null) : (A.music || null),
        opts: reel ? toEngine(RO) : { shot_sec: +($("#sl")?.value || 4) } };
      const r = await api("/api/assemble", body);
      await refresh(r.versions[0]?.id);
      $("#genmsg").textContent = `${r.versions.length} version${r.versions.length > 1 ? "s" : ""} bane.`;
    } catch (e) { toast(e.message, 1); $("#genmsg").textContent = ""; }
    $("#gen").disabled = false;
  }
  drawSide(); drawTabs(); if (A.vid) showVersion(A.vid, refresh);
}
async function showVersion(id, refresh) {
  let v;
  try { v = await api(`/api/version?out=${enc(S.out)}&id=${id}`); } catch (e) { $("#vview").innerHTML = ""; return; }
  const RO = ropts();
  const dur = Math.max(1, v.duration), tracks = [...new Set(v.items.map(i => i.track))].sort((a, b) => (a[0] === "A") - (b[0] === "A") || b.localeCompare(a));
  const px = Math.max(900, dur * 14);
  const titleRow = (v.titles || []).length ? `<div class="trk" data-n="T" style="width:${px}px">${v.titles.map(t => `<div class="ci V2" style="left:${t.rec / dur * px}px;width:${Math.max(3, t.dur / dur * px - 1)}px;background:#8a6a1f">${h(t.kind)}: ${h(t.text)}</div>`).join("")}</div>` : "";
  const capRow = (v.captions || []).length ? `<div class="trk" data-n="CC" style="width:${px}px">${v.captions.map(c => `<div class="ci" style="left:${c.start / dur * px}px;width:${Math.max(2, (c.end - c.start) / dur * px - 1)}px;background:#3a3f4a">${h(c.text)}</div>`).join("")}</div>` : "";
  const tl = `<div class="tl"><div style="width:${px + 40}px">${titleRow}${capRow}${tracks.map(t => `<div class="trk" data-n="${t}" style="width:${px}px">${v.items.filter(i => i.track === t).map(i =>
    `<div class="ci ${t} ${i.label === "dialogue" ? "dlg" : ""}" title="${h(i.name)} ${fmtT(i.start)}–${fmtT(i.end)} ${h(i.text || "")}${i.zoom ? " · zoom " + Math.round(i.zoom * 100) + "%" : ""}" style="left:${i.rec / dur * px}px;width:${Math.max(3, (i.end - i.start) / dur * px - 1)}px">${i.zoom ? "🔍 " : ""}${h(i.text ? i.text.slice(0, 40) : i.name)}</div>`).join("")}</div>`).join("")}</div></div>`;
  let n = 0;
  const rows = (v.beats || []).map(b => {
    if (b.kind === "section") return `<tr><td colspan="4"><b>${h(b.text)}</b></td></tr>`;
    n++;
    const t = b.take; const pc = t ? Math.round(t.sim * 100) : null;
    return `<tr style="${b.removed ? "opacity:.45" : ""}"><td>${n}</td><td>${b.kind === "dialogue" ? "🗣️" : "🎞️"} ${b.speaker ? `<b>${h(b.speaker)}:</b> ` : ""}${h(b.text)}${b.removed ? ` <span class="pill bad">hataya</span>` : ""}</td>
      <td>${t ? `<b>${h(t.name)}</b> ${fmtT(t.start)}–${fmtT(t.end)}<div class="small muted">${h((t.text || "").slice(0, 160))}</div>` : b.missing ? `<span class="pill bad">match nahi — VO / pickup</span>${(b.alts || [])[0] ? `<div class="small muted">closest: ${h(b.alts[0].name)} (${Math.round(b.alts[0].sim * 100)}%) · ${h((b.alts[0].text || "").slice(0, 80))}</div>` : ""}` : (b.broll || []).slice(0, 2).map(p => `${h(p.name)} <span class="small muted">${h(p.move || "")}</span>`).join("<br>")}</td>
      <td>${pc != null ? `<span class="pill ${pc >= 70 ? "ok" : pc >= 50 ? "mid" : "bad"}">${pc}%</span>${(b.alts || []).length > 1 ? `<div class="small muted">${b.alts.length - 1} alt takes</div>` : ""}` : ""}</td></tr>`;
  }).join("");
  $("#vview").innerHTML = `<div class="row" style="margin-bottom:6px"><b>${h(v.name)}</b><span class="muted small">${fmtT(v.duration)} · ${v.items.length} items${v.aspect ? " · " + v.aspect : ""}${(v.captions || []).length ? ` · ${v.captions.length} captions` : ""}</span>${v.instruction ? `<span class="small muted">✎ "${h(v.instruction)}"</span>` : ""}<span class="sp"></span>
    <label class="switch" style="margin:0 10px 0 0"><input type="checkbox" id="vrep" ${RO.replace ? "checked" : ""}><i></i>Purani timeline replace</label>
    <label class="switch" style="margin:0 10px 0 0"><input type="checkbox" id="vsm" checked><i></i>HFR slow-mo</label>
    <button class="btn" id="vx">Export XML</button><button class="btn primary" id="vr">Send to Resolve</button><button class="btn ghost" id="vdel" title="Version delete">🗑</button></div>
    ${v.mode === "reel" || v.source ? `<div class="instr"><input class="input" id="vin" placeholder='Instruction: "30 sec karo, B-roll zyada" · "staff 3 hatao" · "line 2 ka dusra take" · "hook: Dulhe raja yahan bante hain" · "zoom hatao"'>
      <button class="btn" id="vsw">${ICON.tune}Switches</button><button class="btn primary" id="vgo">Regenerate</button></div><div id="vswp"></div>` : ""}
    ${(v.notes || []).map(x => `<div class="small" style="color:#ffd166;margin-bottom:6px">⚠ ${h(x)}</div>`).join("")}${tl}
    <div class="small muted" style="margin:8px 0">Premiere: <span class="kbd">Window › Extensions › Chhanni</span> → Versions → Import. Captions SRT: project ke <span class="kbd">exports</span> folder mein. Resolve mein word-highlight ke liye subtitle track par <b>Effects › Titles › Animated Subtitles</b> daaliye.</div>
    ${rows ? `<table class="beats"><tr><th>#</th><th>Script</th><th>Match</th><th>Score</th></tr>${rows}</table>` : ""}`;
  $("#vrep").onchange = e => { const o = ropts(); o.replace = e.target.checked; saveRopts(o); };
  $("#vx").onclick = async () => { try { const r = await api("/api/export_version", { out: S.out, id, kind: "xml" }); toast("XML ready"); api("/api/reveal", { path: r.path }); } catch (e) { toast(e.message, 1); } };
  $("#vr").onclick = async () => {
    $("#vr").disabled = true; $("#vr").textContent = "Sending…";
    try { const r = await api("/api/export_version", { out: S.out, id, kind: "resolve", slowmo: $("#vsm").checked, replace: $("#vrep").checked }); toast(`Resolve: "${r.result.timeline}" ban gayi`); }
    catch (e) { toast(e.message, 1); }
    $("#vr").disabled = false; $("#vr").textContent = "Send to Resolve";
  };
  $("#vdel").onclick = async () => { await api("/api/delete_version", { out: S.out, id }); toast("Version delete"); refresh && refresh(null); };
  const regen = async (changes) => {
    const ins = $("#vin")?.value || "";
    if (!ins.trim() && !changes) return toast("Instruction likhiye ya Switches badaliye", 1);
    $("#vgo").disabled = true; $("#vgo").textContent = "Bana rahe hain…";
    try {
      const r = await api("/api/regenerate", { out: S.out, id, instruction: ins, changes });
      toast(`${r.version.name} bana${r.changes?.length ? " — " + r.changes.join(", ") : ""}`);
      refresh && refresh(r.version.id);
    } catch (e) { toast(e.message, 1); }
    $("#vgo").disabled = false; $("#vgo").textContent = "Regenerate";
  };
  $("#vgo") && ($("#vgo").onclick = () => regen(null));
  $("#vin") && ($("#vin").onkeydown = e => { if (e.key === "Enter") regen(null); });
  $("#vsw") && ($("#vsw").onclick = () => {
    const box = $("#vswp");
    if (box.innerHTML) { box.innerHTML = ""; return; }
    const o = fromEngine(v.opts || toEngine(ropts()));
    box.innerHTML = `<div class="card" style="margin:8px 0;background:#12151b">${featuresHTML(o, "vs_")}<div class="row" style="margin-top:10px"><span class="sp"></span><button class="btn primary" id="vswgo">In switches ke saath regenerate</button></div></div>`;
    bindFeatures(box, o);
    $("#vswgo").onclick = () => { const e = toEngine(o); delete e.variations; regen(e); };
  });
}

/* ------------------------------------------------------------ v3.3 reel feature switches */
const FEATURES = [
  ["🧹 Cleanup", [
    ["remove_bad_takes", "Bad takes & false starts hatao", "bool"],
    ["drop_fillers", "Filler words hatao (umm, uh, अं)", "bool"],
    ["remove_silences", "Silences hatao", "bool", ["pause", "Gap (sec)", "range", 0.15, 0.8, 0.05]],
    ["drop_clipped", "Audio clipping wale takes hatao", "bool"],
    ["stable_broll", "Sirf stable & sharp B-roll", "bool"]]],
  ["🎬 Visual", [
    ["broll_on", "B-roll add karo", "bool", ["broll", "Amount", "select", [["some", "Kam"], ["more", "Zyada"]]]],
    ["zoom", "Zoom cuts (jump cut par punch-in)", "bool", ["zoom_level", "Zoom", "range", 1.05, 1.3, 0.01]],
    ["color_balance", "Auto colour balance (Resolve, beta)", "bool"],
    ["smart_reframe", "Smart Reframe (Resolve Studio)", "bool"]]],
  ["🔤 Text", [
    ["hook", "Text hook (shuru mein)", "bool", ["hook_text", "Hook text (khali = pehli line)", "text"]],
    ["captions", "Word-by-word captions", "bool", ["caption_words", "Shabd per caption", "range", 1, 4, 1]],
    ["lower_third", "Name lower-third", "bool", ["lower_third_text", "Jaise: Shailesh Birani – Owner", "text"]],
    ["cta", "CTA end card", "bool", ["cta_text", "Address / phone / offer", "text"]]]],
  ["🔊 Audio", [
    ["enhance_audio", "Enhance audio (denoise, -14 LUFS)", "bool"],
    ["music_on", "Background music", "bool", ["music", "Music file", "file"], ["music_db", "Level dB", "range", -32, -8, 1]],
    ["sfx_on", "Sound effects (B-roll/zoom par whoosh)", "bool", ["sfx", "SFX file", "file"]]]],
  ["📐 Structure & output", [
    ["target_on", "Target length", "bool", ["target_sec", "Seconds", "select", [[15, "15s"], [30, "30s"], [45, "45s"], [60, "60s"], [90, "90s"]]]],
    ["auto_hook", "Best line pehle (auto hook)", "bool"],
    ["aspect_x", "Format", "fixed", ["aspect", "", "select", [["9:16", "9:16 Reel"], ["16:9", "16:9 YouTube"], ["1:1", "1:1"], ["4:5", "4:5"]]]],
    ["variations", "3 variations banao", "bool"],
    ["markers", "Script line markers", "bool"],
    ["replace", "NLE mein purani timeline replace karo", "bool"]]],
];
const RDEF = { remove_bad_takes: true, drop_fillers: true, remove_silences: true, pause: 0.35, drop_clipped: true, stable_broll: true,
  broll_on: true, broll: "some", zoom: true, zoom_level: 1.12, color_balance: false, smart_reframe: false,
  hook: true, hook_text: "", captions: true, caption_words: 3, lower_third: false, lower_third_text: "", cta: false, cta_text: "",
  enhance_audio: false, music_on: false, music: "", music_db: -20, sfx_on: false, sfx: "",
  target_on: false, target_sec: 30, auto_hook: false, aspect: "9:16", variations: true, markers: true, replace: true };
function ropts() { try { return Object.assign({}, RDEF, JSON.parse(localStorage.getItem("chhanni.reel") || "{}")); } catch { return Object.assign({}, RDEF); } }
function saveRopts(o) { localStorage.setItem("chhanni.reel", JSON.stringify(o)); }
function toEngine(o) {   // UI switches -> reel.py options
  return { remove_bad_takes: o.remove_bad_takes, drop_fillers: o.drop_fillers, remove_silences: o.remove_silences, pause: +o.pause,
    drop_clipped: o.drop_clipped, stable_broll: o.stable_broll, broll: o.broll_on ? o.broll : "none", zoom: o.zoom, zoom_level: +o.zoom_level,
    color_balance: o.color_balance, smart_reframe: o.smart_reframe, hook: o.hook, hook_text: o.hook_text, captions: o.captions,
    caption_words: +o.caption_words, lower_third: o.lower_third, lower_third_text: o.lower_third_text, cta: o.cta, cta_text: o.cta_text,
    enhance_audio: o.enhance_audio, music: o.music_on ? o.music : "", music_db: +o.music_db, sfx: o.sfx_on ? o.sfx : "",
    target_sec: o.target_on ? +o.target_sec : 0, auto_hook: o.auto_hook, aspect: o.aspect, variations: o.variations, markers: o.markers };
}
function fromEngine(e) {   // version.opts -> UI switches
  const o = ropts();
  Object.assign(o, { remove_bad_takes: e.remove_bad_takes, drop_fillers: e.drop_fillers, remove_silences: e.remove_silences, pause: e.pause,
    drop_clipped: e.drop_clipped, stable_broll: e.stable_broll, broll_on: e.broll !== "none", broll: e.broll === "none" ? o.broll : e.broll,
    zoom: e.zoom, zoom_level: e.zoom_level, color_balance: e.color_balance, smart_reframe: e.smart_reframe, hook: e.hook, hook_text: e.hook_text,
    captions: e.captions, caption_words: e.caption_words, lower_third: e.lower_third, lower_third_text: e.lower_third_text, cta: e.cta,
    cta_text: e.cta_text, enhance_audio: e.enhance_audio, music_on: !!e.music, music: e.music || o.music, music_db: e.music_db,
    sfx_on: !!e.sfx, sfx: e.sfx || o.sfx, target_on: !!e.target_sec, target_sec: e.target_sec || o.target_sec, auto_hook: e.auto_hook,
    aspect: e.aspect, markers: e.markers });
  for (const k in o) if (o[k] === undefined) o[k] = RDEF[k];
  return o;
}
function featuresHTML(o, prefix) {
  const sub = (d) => {
    const [k, lab, typ, a, b, st] = d, id = prefix + k;
    if (typ === "range") return `<label class="small muted fsub">${lab} <b id="${id}_v">${o[k]}</b><input type="range" id="${id}" data-k="${k}" min="${a}" max="${b}" step="${st}" value="${o[k]}"></label>`;
    if (typ === "select") return `<select class="input fsub" id="${id}" data-k="${k}">${a.map(([v, l]) => `<option value="${v}" ${String(o[k]) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    if (typ === "text") return `<input class="input fsub" id="${id}" data-k="${k}" placeholder="${h(lab)}" value="${h(o[k] || "")}">`;
    if (typ === "file") return `<div class="row fsub"><input class="input" id="${id}" data-k="${k}" placeholder="${h(lab)}" value="${h(o[k] || "")}" style="flex:1"><button class="btn small" data-browse="${id}">Browse</button></div>`;
    return "";
  };
  return `<div class="fgrid">${FEATURES.map(([g, list]) => `<div class="fgroup"><div class="fgh">${g}</div>${list.map(f => {
    const [k, lab, typ, ...subs] = f;
    const head = typ === "fixed" ? `<div class="fname">${lab}</div>` : `<label class="switch"><input type="checkbox" id="${prefix + k}" data-k="${k}" ${o[k] ? "checked" : ""}><i></i>${lab}</label>`;
    return `<div class="feat ${typ !== "fixed" && !o[k] ? "off" : ""}">${head}${subs.map(sub).join("")}</div>`;
  }).join("")}</div>`).join("")}</div>`;
}
function bindFeatures(root, o, onchange) {
  $$("[data-k]", root).forEach(el => {
    const k = el.dataset.k;
    const upd = () => {
      o[k] = el.type === "checkbox" ? el.checked : el.type === "range" ? +el.value : el.value;
      if (el.type === "range") { const v = $("#" + el.id + "_v", root); if (v) v.textContent = el.value; }
      if (el.type === "checkbox") el.closest(".feat").classList.toggle("off", !el.checked);
      onchange && onchange(o);
    };
    el.oninput = upd; el.onchange = upd;
  });
  $$("[data-browse]", root).forEach(b => b.onclick = async () => {
    const r = await api("/api/browse_file", { title: "File chuniye", kind: "audio" });
    if (r.path) { const inp = $("#" + b.dataset.browse, root); inp.value = r.path; inp.dispatchEvent(new Event("input")); }
  });
}

/* ------------------------------------------------------------ updates */
async function doUpdate() {
  toast("Update download ho raha hai…");
  try {
    const r = await api("/api/update/install", {});
    toast(`Chhanni ${r.result.version} install ho gaya - engine restart ho raha hai…`);
    setTimeout(() => location.reload(), 5000);
  } catch (e) { toast(e.message, 1); }
}
async function autoUpdateCheck() {
  try {
    const st = await api("/api/update/status");
    const c = st.config || {};
    if (!c.repo || c.auto === false) return;
    const old = st.checked ? (Date.now() - new Date(st.checked.replace(" ", "T")).getTime()) / 3600000 : 99;
    const r = old > 6 ? await api("/api/update/check", {}) : st;
    if (r.available) {
      const b = document.createElement("div");
      b.className = "mini-run"; b.style.borderColor = "var(--accent)";
      b.innerHTML = `<div class="small"><b>⬆️ Chhanni ${h(r.remote)}</b> available</div><button class="btn primary small" style="margin-top:6px;width:100%">Update</button>`;
      b.querySelector("button").onclick = doUpdate;
      $("#minirun").after(b);
    }
  } catch {}
}
setTimeout(autoUpdateCheck, 2500);
