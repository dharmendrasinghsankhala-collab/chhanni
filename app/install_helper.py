"""
Chhanni installer helper (called by setup.bat with the real Python).
 - install.json for the Resolve / Premiere panels
 - Resolve panel  -> Workspace > Scripts > Chhanni
 - Premiere panel -> Window > Extensions > Chhanni  (+ PlayerDebugMode for unsigned CEP panels)
 - Desktop + Start-menu shortcuts
 - registers earlier Chhanni v1 runs so they show up in the app
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
TARGET = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Chhanni" / "app"


def ensure_fixed_location():
    """Install (or refresh) the code into %LOCALAPPDATA%\\Chhanni\\app so updates always land in one place."""
    global ROOT, APP
    if ROOT.resolve() == TARGET.resolve():
        return
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT, TARGET, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
    ROOT, APP = TARGET, TARGET / "app"
    step(f"{OK} Chhanni installed: {TARGET}")
APPDATA = Path(os.environ.get("APPDATA", Path.home()))
CH = APPDATA / "Chhanni"
OK, WARN = "   [ok]", "   [!]"


def step(msg):
    print(msg, flush=True)


def install_json():
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    data = {"app_dir": str(ROOT), "python": str(exe), "pythonw": str(pyw if pyw.exists() else exe), "version": json.loads((ROOT / "version.json").read_text(encoding="utf-8")).get("version") if (ROOT / "version.json").exists() else "?"}
    CH.mkdir(parents=True, exist_ok=True)
    (CH / "install.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    step(f"{OK} install.json")
    return data


def resolve_panel():
    d = APPDATA / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "plugins" / "resolve" / "Chhanni.py", d / "Chhanni.py")
    old = d / "Chhanni Import.py"
    if old.exists():
        old.unlink()
    step(f"{OK} DaVinci Resolve: Workspace > Scripts > Chhanni")


def premiere_panel():
    src = ROOT / "plugins" / "premiere" / "com.chhanni.panel"
    dst = APPDATA / "Adobe" / "CEP" / "extensions" / "com.chhanni.panel"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    try:
        import winreg
        versions = set(range(9, 14))
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Adobe")
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                if name.startswith("CSXS.") and name[5:].isdigit():
                    versions.add(int(name[5:]))
                i += 1
        except OSError:
            pass
        for v in sorted(versions):
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Adobe\CSXS.{v}")
            winreg.SetValueEx(key, "PlayerDebugMode", 0, winreg.REG_SZ, "1")
            winreg.CloseKey(key)
        step(f"{OK} Premiere Pro: Window > Extensions > Chhanni  (Premiere restart karna hoga)")
    except Exception as e:
        step(f"{WARN} Premiere panel copy ho gaya, par registry set nahi hui: {e}")


def shortcuts(inst):
    ico = ROOT / "assets" / "chhanni.ico"
    target, args = inst["pythonw"], f'"{ROOT / "Chhanni.pyw"}"'
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs'))) {{
  $s = $ws.CreateShortcut((Join-Path $dir 'Chhanni.lnk'))
  $s.TargetPath = '{target}'
  $s.Arguments = '{args}'
  $s.WorkingDirectory = '{ROOT}'
  $s.IconLocation = '{ico}'
  $s.Description = 'Chhanni - footage QC and selects'
  $s.Save()
}}"""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True)
    step(f"{OK} Desktop + Start menu shortcut: Chhanni" if r.returncode == 0 else f"{WARN} shortcut: {r.stderr.strip()[:200]}")


def register_old_runs():
    pf = CH / "projects.json"
    try:
        lst = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        lst = []
    known = {os.path.normcase(p.get("out", "")) for p in lst}
    cands = [Path.home() / "Chhanni_Out"]
    try:
        last = Path((CH / "last_selects.txt").read_text(encoding="utf-8").strip()).parent
        cands.append(last)
    except Exception:
        pass
    added = 0
    for c in cands:
        if (c / "selects.json").exists() and os.path.normcase(str(c)) not in known:
            name, roots = c.name, []
            try:
                roots = json.loads((c / "roots.json").read_text(encoding="utf-8"))
                if roots:
                    name = Path(roots[0]).name or name
            except Exception:
                pass
            pj = c / "project.json"
            if not pj.exists():
                pj.write_text(json.dumps({"name": name, "folders": roots}, indent=1), encoding="utf-8")
            lst.insert(0, {"out": str(c), "name": name, "updated": ""})
            known.add(os.path.normcase(str(c)))
            added += 1
    if added:
        pf.write_text(json.dumps(lst, indent=1, ensure_ascii=False), encoding="utf-8")
        step(f"{OK} {added} purana Chhanni run app mein add hua")


if __name__ == "__main__":
    try:
        ensure_fixed_location()
    except Exception as e:
        step(f"{WARN} fixed location: {e}")
    inst = install_json()
    for fn in (resolve_panel, premiere_panel, lambda: shortcuts(inst), register_old_runs):
        try:
            fn()
        except Exception as e:
            step(f"{WARN} {e}")
