#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate early-binding COM stubs for the SOLIDWORKS API, straight from the .tlb files.

WHY FROM THE FILES (measured on SOLIDWORKS 2025 SP5, build 33.5.0.0053):

    The type libraries are registered under the WRONG version number.  Inside the
    file the version is 33.0, but the registry says 21.0 (win64) and 1.0 (win32).
    COM validates the requested version against the version embedded in the .tlb,
    so every registry-based lookup fails:

        pythoncom.LoadRegTypeLib('{83A33D31-...}', 21, 0, 0)
        gencache.EnsureModule('{83A33D31-...}', 0, 21, 0)
        gencache.EnsureDispatch('SldWorks.Application.33')
            -> com_error 0x8002801D  TYPE_E_LIBNOTREGISTERED

    pythoncom.LoadTypeLib(<path>) does NOT consult the registry and works fine, so
    stubs are generated from the file path and stored in a stable directory of our
    own instead of the volatile %TEMP%\\gen_py.

Outputs (default %LOCALAPPDATA%\\swbridge\\stubs):
    _swconst_gen.py    raw makepy output for swconst.tlb  (8199 named constants)
    _sldworks_gen.py   raw makepy output for sldworks.tlb (998 interface classes)
    swenums.py         flat, importable constant namespace (swOpenDocOptions_Silent, ...)
    meta.json          what was generated, from where, and when
"""

import argparse
import datetime as _dt
import json
import os
import shutil
import sys

DEFAULT_OUT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "swbridge", "stubs"
)

# LIBID -> (stub module name, human name)
TLBS = [
    ("{4687F359-55D0-4CD3-B6CF-2EB42C11F989}", "swconst.tlb", "_swconst_gen.py", "SwConst"),
    ("{83A33D31-27C5-11CE-BFD4-00400513BB57}", "sldworks.tlb", "_sldworks_gen.py", "SldWorks"),
]

SW_FOLDER_HINTS = [
    r"D:\Mango\sw2025\SOLIDWORKS Corp 2025\SOLIDWORKS",
    r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS",
]


def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def find_sw_dir(explicit=None):
    """Locate the SOLIDWORKS program folder, most reliable source first."""
    if explicit:
        if os.path.isfile(os.path.join(explicit, "sldworks.tlb")):
            return explicit
        raise SystemExit("--sw-dir given but no sldworks.tlb under: %s" % explicit)

    # 1) installer record
    try:
        import winreg
        for year in range(2030, 2009, -1):
            key = r"SOFTWARE\SolidWorks\SOLIDWORKS %d\Setup" % year
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                    folder = winreg.QueryValueEx(k, "SolidWorks Folder")[0]
                if folder and os.path.isfile(os.path.join(folder, "sldworks.tlb")):
                    return os.path.normpath(folder)
            except OSError:
                continue
    except ImportError:
        pass

    # 2) the COM server path (LocalServer32 of SldWorks.Application)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"SldWorks.Application\CLSID") as k:
            clsid = winreg.QueryValueEx(k, "")[0]
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, r"CLSID\%s\LocalServer32" % clsid
        ) as k:
            exe = winreg.QueryValueEx(k, "")[0].strip('"')
        folder = os.path.dirname(os.path.expandvars(exe))
        if os.path.isfile(os.path.join(folder, "sldworks.tlb")):
            return folder
    except OSError:
        pass

    # 3) well-known locations
    for folder in SW_FOLDER_HINTS:
        if os.path.isfile(os.path.join(folder, "sldworks.tlb")):
            return folder

    raise SystemExit(
        "Cannot locate the SOLIDWORKS program folder. Pass --sw-dir <folder containing sldworks.tlb>."
    )


def generate_one(tlb_path, dest_py):
    import pythoncom
    from win32com.client import makepy, gencache

    tlb = pythoncom.LoadTypeLib(tlb_path)  # bypasses the registry on purpose
    attr = tlb.GetLibAttr()
    guid, lcid, major, minor = str(attr[0]), attr[1], attr[3], attr[4]
    makepy.GenerateFromTypeLibSpec(tlb, verboseLevel=0)
    # GetGeneratedFileName returns the BASE name; makepy itself appends ".py".
    produced = os.path.join(
        gencache.GetGeneratePath(),
        gencache.GetGeneratedFileName(guid, lcid, major, minor) + ".py",
    )
    if not os.path.isfile(produced):
        raise SystemExit("makepy did not produce %s" % produced)
    shutil.copyfile(produced, dest_py)
    return {"libid": guid, "lcid": lcid, "version": "%d.%d" % (major, minor),
            "tlb": tlb_path, "produced": produced, "name": tlb.GetDocumentation(-1)[0],
            "type_count": tlb.GetTypeInfoCount()}


def load_module(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def write_enums(stubs_dir, swconst_stub):
    """Flatten swconst's `constants` class into an importable namespace."""
    mod = load_module(swconst_stub, "_swconst_gen")
    consts = {}
    for holder_name in ("constants",):
        holder = getattr(mod, holder_name, None)
        if holder is None:
            continue
        for k, v in vars(holder).items():
            if k.startswith("_") or not isinstance(v, int):
                continue
            consts.setdefault(k, v)

    out = os.path.join(stubs_dir, "swenums.py")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write('"""Flat SOLIDWORKS constant namespace - generated by genstubs.py. Do not edit.\n\n')
        f.write("Usage:  from swenums import swOpenDocOptions_Silent, swDocPART\n")
        f.write('        swenums.name_of(1)  -> first matching constant name\n"""\n\n')
        f.write("name_of_value = {}\n\n")
        for k in sorted(consts):
            f.write("%s = %d\n" % (k, consts[k]))
        f.write("\n\nALL = {\n")
        for k in sorted(consts):
            f.write("    %r: %d,\n" % (k, consts[k]))
        f.write("}\n\n")
        f.write("REVERSE = {}\n")
        f.write("for _n, _v in ALL.items():\n")
        f.write("    REVERSE.setdefault(_v, []).append(_n)\n\n\n")
        f.write("def name_of(value, prefix=''):\n")
        f.write('    """Best-effort: turn a raw enum value back into a name (for diagnostics)."""\n')
        f.write("    names = [n for n in REVERSE.get(value, []) if n.startswith(prefix)]\n")
        f.write("    return names[0] if names else str(value)\n")
    return out, len(consts)


def main():
    ap = argparse.ArgumentParser(description="Generate SOLIDWORKS COM stubs from the .tlb files.")
    ap.add_argument("--sw-dir", help="SOLIDWORKS program folder (contains sldworks.tlb)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output directory (default: %s)" % DEFAULT_OUT)
    ap.add_argument("--json", action="store_true", help="print the result as JSON only")
    args = ap.parse_args()

    sw_dir = find_sw_dir(args.sw_dir)
    os.makedirs(args.out, exist_ok=True)
    report = {"sw_dir": sw_dir, "out": args.out, "generated_utc": _dt.datetime.utcnow().isoformat() + "Z",
              "typelibs": [], "python": sys.executable, "python_bits": 64 if sys.maxsize > 2**32 else 32}
    if not args.json:
        log("SOLIDWORKS folder : %s" % sw_dir)
        log("stub output       : %s" % args.out)

    for guid, tlb_name, stub_name, label in TLBS:
        tlb_path = os.path.join(sw_dir, tlb_name)
        if not os.path.isfile(tlb_path):
            if not args.json:
                log("  MISSING %s - skipped" % tlb_path)
            continue
        info = generate_one(tlb_path, os.path.join(args.out, stub_name))
        info["label"] = label
        info["stub"] = os.path.join(args.out, stub_name)
        report["typelibs"].append(info)
        if not args.json:
            log("  %-10s %-11s %5d types -> %s" % (label, info["version"], info["type_count"],
                                                   os.path.basename(info["stub"])))

    swconst_stub = os.path.join(args.out, "_swconst_gen.py")
    if os.path.isfile(swconst_stub):
        enum_path, count = write_enums(args.out, swconst_stub)
        report["enums"] = {"file": enum_path, "count": count}
        if not args.json:
            log("  enums       %5d constants -> %s" % (count, os.path.basename(enum_path)))

    # record the executable version so `doctor` can spot a SW upgrade later
    exe = os.path.join(sw_dir, "SLDWORKS.exe")
    if os.path.isfile(exe):
        try:
            import win32api
            vi = win32api.GetFileVersionInfo(exe, "\\")
            ms, ls = vi["FileVersionMS"], vi["FileVersionLS"]
            report["sw_version"] = "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
            if not args.json:
                log("  SW version  %s" % report["sw_version"])
        except Exception as exc:  # pragma: no cover - cosmetic only
            report["sw_version_error"] = repr(exc)

    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    if args.json:
        log(json.dumps(report, ensure_ascii=False))
    else:
        log("done. meta: %s" % os.path.join(args.out, "meta.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
