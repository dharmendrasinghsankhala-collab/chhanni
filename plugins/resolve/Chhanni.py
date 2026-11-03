"""
Chhanni panel for DaVinci Resolve  -  Workspace > Scripts > Chhanni
Pick a Chhanni project, choose options, Import -> bins + timelines + colours + markers in the current project.
"""
import json
import os
import subprocess
import sys
import traceback


def _install():
    p = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Chhanni", "install.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


INST = _install()
sys.path.insert(0, os.path.join(INST["app_dir"], "app"))
import plan            # noqa: E402
import resolve_bridge  # noqa: E402
assemble = plan       # version list / load are pure-stdlib in plan.py

try:
    RV = resolve  # noqa: F821
except NameError:
    RV = resolve_bridge.get_resolve()
try:
    BMD = bmd  # noqa: F821
except NameError:
    import DaVinciResolveScript as BMD

fu = RV.Fusion()
ui = fu.UIManager
disp = BMD.UIDispatcher(ui)

GROUPS = [("group", "One timeline per camera / card"), ("folder", "One timeline per folder"), ("none", "One timeline per type (A / B)")]
ACCENT = "#f2a93b"

win = disp.AddWindow({"WindowTitle": "Chhanni - Footage QC & Selects", "ID": "ChhanniWin",
                      "Geometry": [260, 140, 580, 660]},
    ui.VGroup({"Spacing": 8}, [
        ui.Label({"Text": f"<span style='font-size:18px;font-weight:700;color:{ACCENT}'>Chhanni</span>"
                          "<span style='color:#9097a6'>&nbsp;&nbsp;footage QC &amp; selects</span>",
                  "Weight": 0}),
        ui.HGroup({"Weight": 0}, [ui.Label({"Text": "Chhanni project", "Weight": 0, "MinimumSize": [110, 0]}),
                                  ui.ComboBox({"ID": "Proj", "Weight": 1})]),
        ui.Label({"ID": "Info", "Text": "", "WordWrap": True, "Weight": 0,
                  "StyleSheet": "color:#c8ccd4;padding:6px;background:#1c2029;border-radius:6px"}),
        ui.HGroup({"Weight": 0}, [ui.Label({"Text": "Timelines", "Weight": 0, "MinimumSize": [110, 0]}),
                                  ui.ComboBox({"ID": "Group", "Weight": 1})]),
        ui.HGroup({"Weight": 0}, [ui.CheckBox({"ID": "IncA", "Text": "A-roll (interviews)", "Checked": True}),
                                  ui.CheckBox({"ID": "IncB", "Text": "B-roll", "Checked": True}),
                                  ui.CheckBox({"ID": "Slow", "Text": "High-fps clips slow-motion", "Checked": True})]),
        ui.HGroup({"Weight": 0}, [ui.Button({"ID": "Open", "Text": "Open Chhanni app"}),
                                  ui.Button({"ID": "Refresh", "Text": "Refresh"}),
                                  ui.HGap(0, 1),
                                  ui.Button({"ID": "Import", "Text": "Import into current project",
                                             "StyleSheet": f"background:{ACCENT};color:#1a1206;font-weight:700;padding:6px 14px"})]),
        ui.Label({"Text": "<b>Script / music versions</b> (Chhanni app › Script &amp; assemble)", "Weight": 0}),
        ui.HGroup({"Weight": 0}, [ui.ComboBox({"ID": "Ver", "Weight": 1}),
                                  ui.CheckBox({"ID": "Repl", "Text": "Purani timeline replace", "Checked": True}),
                                  ui.Button({"ID": "ImpVer", "Text": "Import version"})]),
        ui.HGroup({"Weight": 0}, [ui.Label({"Text": "Smart bins: shot-size bins, keywords, Hero / Golden hour / Smooth-move collections",
                                            "WordWrap": True, "Weight": 1}),
                                  ui.Button({"ID": "Smart", "Text": "Build smart bins"})]),
        ui.TextEdit({"ID": "Log", "ReadOnly": True, "Weight": 1,
                     "StyleSheet": "font-family:Consolas;font-size:12px;background:#0f1115;color:#aeb5c2"}),
    ]))
itm = win.GetItems()
PROJECTS = []


def log(msg):
    itm["Log"].Append(str(msg))


def refresh(ev=None):
    global PROJECTS
    PROJECTS = plan.recent_projects()
    itm["Proj"].Clear()
    for p in PROJECTS:
        itm["Proj"].AddItem(p.get("name") or os.path.basename(p["out"]))
    if not PROJECTS:
        itm["Info"].Text = "Koi Chhanni project nahi mila. 'Open Chhanni app' se footage analyse kijiye."
    show_info()


VERSIONS = []


def load_versions():
    global VERSIONS
    i = itm["Proj"].CurrentIndex
    itm["Ver"].Clear()
    VERSIONS = assemble.list_versions(PROJECTS[i]["out"]) if 0 <= i < len(PROJECTS) else []
    for v in VERSIONS:
        itm["Ver"].AddItem(f"{v['name']}  ·  {v['mode']}  ·  {int(v['duration'] or 0)}s  ·  {v.get('created', '')}")
    if not VERSIONS:
        itm["Ver"].AddItem("— koi version nahi (app mein Script & assemble) —")


def do_version(ev):
    i, j = itm["Proj"].CurrentIndex, itm["Ver"].CurrentIndex
    if not (0 <= i < len(PROJECTS)) or not (0 <= j < len(VERSIONS)):
        return log("Project aur version chuniye.")
    try:
        v = assemble.load_version(PROJECTS[i]["out"], VERSIONS[j]["id"])
        r = resolve_bridge.import_version(RV, PROJECTS[i]["out"], v, itm["Slow"].Checked, log=log, replace=itm["Repl"].Checked)
        log(f"Done: timeline '{r['timeline']}'")
    except Exception as e:
        log("ERROR: " + str(e))
        log(traceback.format_exc())


def do_smart(ev):
    i = itm["Proj"].CurrentIndex
    if not (0 <= i < len(PROJECTS)):
        return
    try:
        resolve_bridge.smart_bins(RV, PROJECTS[i]["out"], log=log)
    except Exception as e:
        log("ERROR: " + str(e))
        log(traceback.format_exc())


def show_info(ev=None):
    i = itm["Proj"].CurrentIndex
    if i < 0 or i >= len(PROJECTS):
        return
    load_versions()
    out = PROJECTS[i]["out"]
    try:
        data, review = plan.load_project(out)
        clips = plan.effective_clips(data, review)
        n_sel = sum(len(c["selects"]) for c in clips)
        mins = sum(s["end"] - s["start"] for c in clips for s in c["selects"]) / 60
        miss = sum(1 for c in clips if c.get("missing"))
        cur = RV.GetProjectManager().GetCurrentProject()
        itm["Info"].Text = (f"{len(clips)} clips · {n_sel} selects ({mins:.1f} min) · "
                            f"A-roll {sum(c['type']=='A' for c in clips)} · no usable {sum(not c['selects'] for c in clips)}"
                            + (f" · <span style='color:#ff8a8d'>{miss} files missing (drive connected?)</span>" if miss else "")
                            + f"<br><span style='color:#9097a6'>{out}</span>"
                            + f"<br>Import into Resolve project: <b>{cur.GetName() if cur else '-'}</b>")
    except Exception as e:
        itm["Info"].Text = f"Project load nahi hua: {e}"


def do_import(ev):
    i = itm["Proj"].CurrentIndex
    if i < 0 or i >= len(PROJECTS):
        return log("Pehle ek project chuniye.")
    inc = tuple(x for x, k in (("A", "IncA"), ("B", "IncB")) if itm[k].Checked)
    if not inc:
        return log("A-roll ya B-roll mein se kam se kam ek chuniye.")
    itm["Import"].Enabled = False
    itm["Import"].Text = "Importing…"
    try:
        s = resolve_bridge.import_project(RV, PROJECTS[i]["out"], group_by=GROUPS[itm["Group"].CurrentIndex][0],
                                          include=inc, slowmo_hfr=itm["Slow"].Checked, log=log)
        log(f"Done: bin '{s['bin']}', {len(s['timelines'])} timelines.")
    except Exception as e:
        log("ERROR: " + str(e))
        log(traceback.format_exc())
    itm["Import"].Enabled = True
    itm["Import"].Text = "Import into current project"


def open_app(ev):
    try:
        subprocess.Popen([INST["pythonw"], os.path.join(INST["app_dir"], "Chhanni.pyw")], cwd=INST["app_dir"])
        log("Chhanni app khul raha hai…")
    except Exception as e:
        log("App nahi khula: " + str(e))


def close(ev):
    disp.ExitLoop()


for _, label in GROUPS:
    itm["Group"].AddItem(label)
win.On.ChhanniWin.Close = close
win.On.Import.Clicked = do_import
win.On.Refresh.Clicked = refresh
win.On.Open.Clicked = open_app
win.On.ImpVer.Clicked = do_version
win.On.Smart.Clicked = do_smart
win.On.Proj.CurrentIndexChanged = show_info
refresh()

if not globals().get("CHHANNI_NO_LOOP"):
    win.Show()
    disp.RunLoop()
    win.Hide()
