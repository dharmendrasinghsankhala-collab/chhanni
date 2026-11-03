/* Chhanni - Premiere Pro panel (CEP) */
const API = "http://127.0.0.1:47321";
const $ = s => document.querySelector(s);
const fs = typeof require === "function" ? require("fs") : null;
const cp = typeof require === "function" ? require("child_process") : null;
const path = typeof require === "function" ? require("path") : null;
const LABEL_IDX = { Violet: 0, Iris: 1, Caribbean: 2, Lavender: 3, Cerulean: 4, Forest: 5, Rose: 6, Mango: 7, Purple: 8, Blue: 9, Teal: 10, Magenta: 11, Tan: 12, Green: 13, Brown: 14, Yellow: 15 };
let projects = [];

function es(code) {
  return new Promise(res => window.__adobe_cep__.evalScript(code, r => {
    try { res(JSON.parse(r)); } catch (e) { res({ ok: false, error: "host: " + r }); }
  }));
}
function log(m, cls) { const d = document.createElement("div"); if (cls) d.className = cls; d.textContent = m; $("#log").appendChild(d); $("#log").scrollTop = 1e9; }
function status(m, pct) { $("#status").textContent = m; if (pct != null) $("#bar").style.width = pct + "%"; }
const h = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function install() {
  try {
    const p = path.join(process.env.APPDATA || "", "Chhanni", "install.json");
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch (e) { return null; }
}
async function ping() {
  try { const r = await fetch(API + "/api/ping", { cache: "no-store" }); return (await r.json()).app === "chhanni"; } catch (e) { return false; }
}
function launch(serverOnly) {
  const inst = install();
  if (!inst || !cp) { log("Chhanni install nahi mila - setup.bat chalaiye.", "e"); return false; }
  const args = [path.join(inst.app_dir, "Chhanni.pyw")].concat(serverOnly ? ["--server"] : []);
  const ch = cp.spawn(inst.pythonw, args, { cwd: inst.app_dir, detached: true, stdio: "ignore" });
  ch.unref();
  return true;
}
async function ensureServer() {
  if (await ping()) { $("#srv").className = "on"; return true; }
  $("#srv").className = "off";
  status("Chhanni engine start ho raha hai…");
  if (!launch(true)) return false;
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 500));
    if (await ping()) { $("#srv").className = "on"; status(""); return true; }
  }
  status("Engine start nahi hua.");
  return false;
}
async function api(p) { const r = await fetch(API + p, { cache: "no-store" }); const j = await r.json(); if (!r.ok || j.error) throw new Error(j.error || r.statusText); return j; }

async function loadProjects() {
  if (!(await ensureServer())) return;
  const d = await api("/api/projects");
  projects = d.projects.filter(p => p.summary);
  $("#proj").innerHTML = projects.map((p, i) => `<option value="${i}">${h(p.name)}</option>`).join("") || `<option>— koi analysed project nahi —</option>`;
  showInfo();
  const pi = await es("chhanniInfo()");
  $("#pp").textContent = pi.ok ? "Premiere: " + pi.name : "";
}
function showInfo() {
  loadVersions().catch(() => {});
  const p = projects[+$("#proj").value];
  if (!p) { $("#info").innerHTML = "Chhanni app mein footage analyse kijiye, phir Refresh."; return; }
  const s = p.summary;
  $("#info").innerHTML = `<b>${s.clips}</b> clips · <b>${s.selects}</b> selects (${s.select_min} min) · A-roll ${s.a_roll} · no usable ${s.no_usable}<br><span style="color:#8b8b95">${h(p.out)}</span>`;
}

function targetFps(seqs) {
  const votes = {};
  seqs.forEach(s => s.items.forEach(it => { const f = Math.round(it.fps * 1000) / 1000; if (f <= 61) votes[f] = (votes[f] || 0) + 1; }));
  const best = Object.keys(votes).sort((a, b) => votes[b] - votes[a])[0];
  return best ? +best : 25;
}
const grade = sc => sc >= 80 ? "good" : sc >= 65 ? "ok" : "weak";

async function doImport() {
  const p = projects[+$("#proj").value];
  if (!p) return;
  const include = [$("#iA").checked && "A", $("#iB").checked && "B"].filter(Boolean);
  if (!include.length) return log("A-roll ya B-roll chuniye.", "e");
  $("#go").disabled = true; $("#log").innerHTML = "";
  try {
    const info = await es("chhanniInfo()");
    if (!info.ok) throw new Error(info.error);
    status("Plan load ho raha hai…", 2);
    const plan = await api(`/api/plan?out=${encodeURIComponent(p.out)}&group_by=${$("#grp").value}&include=${include.join(",")}`);
    const slow = $("#sm").checked, tfps = targetFps(plan.sequences);
    const clips = plan.clips.filter(c => !c.selects.length || include.includes(c.type));
    let r = await es(`chhanniStart(${JSON.stringify(plan.project)})`);
    if (!r.ok) throw new Error(r.error);
    log("Bin: " + r.bin, "o");
    const bins = { "A-roll": [], "B-roll": [], "No usable": [] };
    clips.forEach(c => bins[!c.selects.length ? "No usable" : c.type === "A" ? "A-roll" : "B-roll"].push(c.path));
    const totalSteps = clips.length * 2 + plan.sequences.reduce((a, s) => a + s.items.length, 0) || 1;
    let done = 0;
    for (const [bn, paths] of Object.entries(bins)) {
      for (let i = 0; i < paths.length; i += 20) {
        const chunk = paths.slice(i, i + 20);
        status(`Importing ${bn}: ${Math.min(i + 20, paths.length)}/${paths.length}`, done / totalSteps * 100);
        r = await es(`chhanniImport(${JSON.stringify(bn)}, ${JSON.stringify(chunk)})`);
        if (!r.ok) throw new Error(r.error);
        if (r.found < r.asked) log(`${bn}: ${r.asked - r.found} files import nahi hue`, "e");
        done += chunk.length;
      }
      if (paths.length) log(`${bn}: ${paths.length} clips`);
    }
    const labels = plan.labels;
    const marks = clips.map(c => {
      const best = Math.max(0, ...c.selects.map(s => s.score));
      return { path: c.path, fps: (plan.sequences.flatMap(s => s.items).find(it => it.path === c.path) || {}).fps || 0,
        label: c.selects.length ? labels[grade(best)] : labels.none, selects: c.selects, conform: 0, speech: c.speech };
    });
    marks.forEach(m => { if (slow && !m.speech && m.fps && m.fps > tfps * 1.9) m.conform = tfps; });
    let slowed = 0;
    for (let i = 0; i < marks.length; i += 25) {
      status(`Labels & markers ${Math.min(i + 25, marks.length)}/${marks.length}`, done / totalSteps * 100);
      r = await es(`chhanniMark(${JSON.stringify(marks.slice(i, i + 25))}, ${JSON.stringify(LABEL_IDX)}, ${slow})`);
      if (!r.ok) throw new Error(r.error);
      slowed += r.slowed; done += Math.min(25, marks.length - i);
    }
    if (slowed) log(`${slowed} high-fps clips slow-motion (${tfps} fps)`);
    for (const s of plan.sequences) {
      status(`Sequence: ${s.name}`, done / totalSteps * 100);
      r = await es(`chhanniSequence(${JSON.stringify({ name: s.name, items: s.items.map(it => ({ path: it.path, name: it.name, start: it.start, end: it.end, fps: it.fps, star: it.star })) })})`);
      if (!r.ok) { log(`${s.name}: ${r.error}`, "e"); continue; }
      log(`${r.name}: ${r.placed} selects`, "o");
      done += s.items.length;
    }
    status("Ho gaya ✔", 100);
  } catch (e) { log("ERROR: " + e.message, "e"); status("Error", 0); }
  $("#go").disabled = false;
}

let versions = [];
async function loadVersions() {
  const p = projects[+$("#proj").value];
  versions = p ? (await api("/api/versions?out=" + encodeURIComponent(p.out))).versions : [];
  $("#ver").innerHTML = versions.map((v, i) => `<option value="${i}">${h(v.name)} · ${h(v.mode)} · ${Math.round(v.duration || 0)}s</option>`).join("")
    || `<option>— app mein Script &amp; assemble se version banaiye —</option>`;
}
async function importVersion() {
  const p = projects[+$("#proj").value], vi = versions[+$("#ver").value];
  if (!p || !vi) return;
  $("#gov").disabled = true; $("#log").innerHTML = "";
  try {
    const v = await api(`/api/version?out=${encodeURIComponent(p.out)}&id=${vi.id}`);
    const paths = [...new Set(v.items.map(i => i.path))];
    for (let i = 0; i < paths.length; i += 20) {
      status(`Importing media ${Math.min(i + 20, paths.length)}/${paths.length}`, i / paths.length * 50);
      const r = await es(`chhanniVersionStart(${JSON.stringify("Chhanni - " + p.name + " - Versions")}, ${JSON.stringify(paths.slice(i, i + 20))})`);
      if (!r.ok) throw new Error(r.error);
    }
    const counts = {};
    v.items.filter(i => i.media !== "a" && i.fps && i.fps <= 61).forEach(i => counts[i.fps] = (counts[i.fps] || 0) + 1);
    const tf = +(Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || 25);
    const markers = (v.beats || []).filter(b => b.rec != null && b.text && b.kind !== "section").map(b => ({ rec: b.rec, name: (b.missing ? "MISSING: " : "") + b.text.slice(0, 60), note: b.text }))
      .concat((v.titles || []).map(t => ({ rec: t.rec, name: `${t.kind.toUpperCase()}: ${t.text}`.slice(0, 60), note: "Chhanni title - Essential Graphics se add karein: " + t.text })));
    status("Sequence bana rahe hain…", 70);
    const payload = { name: v.name, hasAV: v.items.some(i => i.media === "av"), markers, replace: $("#repl") ? $("#repl").checked : true,
      srt: v.captions_srt || "",
      items: v.items.map(i => ({ path: i.path, start: i.start, end: i.end, rec: i.rec, fps: i.fps || 0, track: i.track, media: i.media, zoom: i.zoom || 0 })) };
    const r = await es(`chhanniVersion(${JSON.stringify(payload)}, ${$("#sm").checked ? tf : 0})`);
    if (!r.ok) throw new Error(r.error);
    log(`${r.name}: ${r.placed} clips${r.skipped ? `, ${r.skipped} skipped` : ""}${r.captions === "yes" ? ", captions track" : ""}`, "o"); status("Ho gaya ✔", 100);
  } catch (e) { log("ERROR: " + e.message, "e"); status("Error", 0); }
  $("#gov").disabled = false;
}
async function smartBins() {
  const p = projects[+$("#proj").value];
  if (!p) return;
  $("#smart").disabled = true; $("#log").innerHTML = "";
  try {
    const plan = await api(`/api/plan?out=${encodeURIComponent(p.out)}`);
    const SB = { CU: "Close-ups", MCU: "Close-ups", MS: "Mediums", WS: "Wides", WIDE: "Wides" };
    const cats = [], subs = [];
    for (const c of plan.clips) {
      let bin;
      if (c.type === "A") bin = "Interviews & dialogue";
      else if (!c.selects.length) bin = "No usable";
      else if (c.selects.some(s => s.move === "Drone / aerial")) bin = "Drone & aerial";
      else {
        const cnt = {}; c.selects.forEach(s => { if (s.shot) cnt[s.shot] = (cnt[s.shot] || 0) + 1; });
        const top = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0];
        bin = "B-roll - " + (SB[top] || "Wides");
      }
      const best = Math.max(0, ...c.selects.map(s => s.score));
      cats.push({ path: c.path, bin, label: c.selects.length ? plan.labels[grade(best)] : plan.labels.none });
      const base = c.path.split(/[\\/]/).pop().replace(/\.[^.]+$/, "");
      c.selects.forEach((s, k) => {
        const nm = `${base} ${k + 1} (${s.score})`;
        const add = bin => subs.push({ path: c.path, bin, name: nm, start: s.start, end: s.end });
        if (s.hero) add("★ Hero shots");
        if ((s.tags || []).includes("Golden hour")) add("Golden hour");
        if (["Slider / gimbal move", "Drone / aerial", "Smooth pan", "Smooth tilt / crane"].includes(s.move)) add("Smooth moves");
        if (c.type === "A") add("Soundbites");
      });
    }
    const root = JSON.stringify("Chhanni - " + p.name + " - Smart bins");
    let nc = 0, ns = 0;
    for (let i = 0; i < cats.length; i += 20) {
      status(`Smart bins ${Math.min(i + 20, cats.length)}/${cats.length}`, i / cats.length * 60);
      const r = await es(`chhanniSmartBins(${root}, ${JSON.stringify(cats.slice(i, i + 20))}, [], ${JSON.stringify(LABEL_IDX)})`);
      if (!r.ok) throw new Error(r.error); nc += r.clips;
    }
    for (let i = 0; i < subs.length; i += 25) {
      status(`Subclips ${Math.min(i + 25, subs.length)}/${subs.length}`, 60 + i / subs.length * 40);
      const r = await es(`chhanniSmartBins(${root}, [], ${JSON.stringify(subs.slice(i, i + 25))}, ${JSON.stringify(LABEL_IDX)})`);
      if (!r.ok) throw new Error(r.error); ns += r.subclips;
    }
    log(`${nc} clips, ${ns} subclips`, "o"); status("Ho gaya ✔", 100);
  } catch (e) { log("ERROR: " + e.message, "e"); status("Error", 0); }
  $("#smart").disabled = false;
}
$("#gov").onclick = importVersion;
$("#smart").onclick = smartBins;
$("#ref").onclick = loadProjects;
$("#proj").onchange = showInfo;
$("#go").onclick = doImport;
$("#app").onclick = () => { if (launch(false)) log("Chhanni app khul raha hai…"); };
setInterval(async () => { $("#srv").className = (await ping()) ? "on" : "off"; }, 15000);
loadProjects();
