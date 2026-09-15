#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""swcore - the machine room of the SOLIDWORKS bridge.

Everything that is easy to get wrong about driving SOLIDWORKS over COM lives
here, so the rest of the tool (and any job script) never has to know about it:

  * ATTACHING WITHOUT THE REGISTRY.  This install registers its type libraries
    under the wrong version (registry says 21.0/1.0, the .tlb files say 33.0),
    so anything that resolves types through the registry - LoadRegTypeLib,
    gencache.EnsureModule, gencache.EnsureDispatch, and .NET's
    Marshal.GetActiveObject - fails with TYPE_E_LIBNOTREGISTERED
    (0x8002801D) or TYPE_E_ELEMENTNOTFOUND.  We therefore go
        ProgID -> CLSID (registry, and only the ProgID entry)
        pythoncom.GetActiveObject(CLSID)   (pure ROT, no type library)
        cast(raw, 'ISldWorks')             (our own generated stubs)
    which also means nothing depends on the volatile %TEMP%\\gen_py.

  * LATE-BOUND TRAPS.  ISldWorks.ActiveDoc and GetFirstDocument carry no CLSID
    in the type library, so pywin32 returns a bare late-bound CDispatch.  On
    those, a property accessed as an attribute comes back as a *callable*, and
    calling it raises DISP_E_MEMBERNOTFOUND ('找不到成员').  PyIDispatch
    .GetTypeInfo() fails with DISP_E_BADINDEX on SW objects, so the type cannot
    be recovered at runtime: cast() takes the interface name EXPLICITLY.
    Never infer interfaces by probing methods - a wrong interface means a wrong
    memid, which can invoke a completely unrelated method.

  * PROPERTIES vs METHODS.  On an early-bound class these are told apart with
    pywin32's own _prop_map_get_; callable() cannot be trusted because
    PyIDispatch wrappers are themselves callable.

  * BUSY SERVER.  SOLIDWORKS rejects calls while it is busy with
    RPC_E_CALL_REJECTED / RPC_E_SERVERCALL_RETRYLATER.  pywin32 does not expose
    CoRegisterMessageFilter, so the official IOleMessageFilter recipe is not
    available and retry_call() does the job instead.

  * MODAL DIALOGS HANG FOREVER.  CloseDoc on a modified document opens a
    "save changes?" dialog and the next COM call never returns.  Anything that
    closes a document goes through close_document(), which calls SetSaveFlag()
    first.  There is no timeout to save you: the call simply blocks.

Lengths are METRES and angles are RADIANS throughout the API.  0.05 is 50 mm.
"""

import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

PROGID = "SldWorks.Application.33"
SW_MAJOR = 33  # SOLIDWORKS 2025; the registry ProgID suffix equals the year - 1992

HOME = os.environ.get("SWBRIDGE_HOME") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "swbridge")
STUBS_DIR = os.path.join(HOME, "stubs")
TASKS_DIR = os.path.join(HOME, "tasks")
LOGS_DIR = os.path.join(HOME, "logs")
STATUS_DIR = os.path.join(HOME, "status")
SHOTS_DIR = os.path.join(HOME, "shots")
OUT_DIR = os.path.join(HOME, "out")

# Neutral CAD formats.  On this build OpenDoc6 rejects every one of them with
# swFileRequiresRepairError, so open_document() retries these with LoadFile4.
NEUTRAL_EXTS = (".step", ".stp", ".iges", ".igs", ".x_t", ".x_b", ".sat", ".sab",
                ".jt", ".sldxml", ".3dxml", ".prt", ".catpart")

# HRESULTs worth a human sentence.  Keyed by the signed 32-bit value pywin32 reports.
HRESULT_HINTS = {
    -2147418111: "SOLIDWORKS is busy and rejected the call (RPC_E_CALL_REJECTED). "
                 "retry_call() handles this; a raw call does not.",
    -2147417846: "SOLIDWORKS is busy and asked the caller to try later "
                 "(RPC_E_SERVERCALL_RETRYLATER).",
    -2147352573: "member not found (DISP_E_MEMBERNOTFOUND). Almost always a "
                 "late-bound object: wrap it with cast(obj, 'IInterface') first, or "
                 "you are calling a property as if it were a method.",
    -2147352565: "bad index (DISP_E_BADINDEX) - e.g. asking a SOLIDWORKS object for "
                 "its type information, which this install does not support.",
    -2147319779: "type library not registered (TYPE_E_LIBNOTREGISTERED). This install "
                 "registers its .tlb files under the wrong version; do not use "
                 "gencache.EnsureDispatch here, use the bundled stubs.",
    -2147221164: "class not registered (REGDB_E_CLASSNOTREG) - is SOLIDWORKS installed?",
    -2147023174: "the SOLIDWORKS process is gone (RPC server unavailable).",
    -2147417848: "the object is disconnected (RPC_E_DISCONNECTED) - SOLIDWORKS "
                 "was closed while this script held a reference to it.",
    -2147467259: "unspecified COM failure (E_FAIL); SOLIDWORKS often means "
                 "'the operation is not valid in the current state'.",
}

RETRY_HRESULTS = {
    -2147418111,  # RPC_E_CALL_REJECTED
    -2147417846,  # RPC_E_SERVERCALL_RETRYLATER
}


class SwError(RuntimeError):
    """A COM failure translated into something readable."""

    def __init__(self, message, hresult=None, hint=None, step=None):
        if not isinstance(hresult, int):   # never format a non-int as an HRESULT
            hresult = None
        self.hresult = hresult
        self.hint = hint
        self.step = step
        text = message
        if hresult is not None:
            text += " (0x%08X)" % (hresult & 0xFFFFFFFF)
        if step:
            text = "[%s] %s" % (step, text)
        if hint:
            text += "\n  -> " + hint
        super().__init__(text)


def _hresult_of(exc):
    """The signed HRESULT of a COM error, or None for ordinary exceptions.

    Only ints count: a plain TypeError carries its message in args[0], and
    treating that string as an HRESULT used to mask the real failure with a
    confusing 'unsupported operand type(s) for &: str and int'."""
    hr = getattr(exc, "hresult", None)
    if isinstance(hr, int):
        return hr
    if exc.args and isinstance(exc.args[0], int):
        return exc.args[0]
    return None


def translate(exc, step=None):
    """Turn a pywin32 com_error into an SwError with an actionable hint."""
    if isinstance(exc, SwError):
        return exc
    hr = _hresult_of(exc)
    hint = HRESULT_HINTS.get(hr)
    if hint is None and hr is not None:
        hint = "unmapped HRESULT; the COM error text above is all SOLIDWORKS gave us."
    return SwError(str(exc), hresult=hr, hint=hint, step=step)


def ensure_dirs():
    for d in (HOME, STUBS_DIR, TASKS_DIR, LOGS_DIR, STATUS_DIR, SHOTS_DIR, OUT_DIR):
        os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------- stubs

_stub_cache = {}


def stubs():
    """Import the generated early-binding modules from our own stubs directory.

    Deliberately NOT via win32com.client.gencache: gencache looks the library up
    in the registry, which is broken on this install, and it writes into %TEMP%.
    """
    if "L" in _stub_cache:
        return _stub_cache["L"], _stub_cache["E"]
    if not os.path.isdir(STUBS_DIR) or not os.path.isfile(
            os.path.join(STUBS_DIR, "_sldworks_gen.py")):
        raise SwError(
            "COM stubs are missing from %s" % STUBS_DIR,
            hint="Run:  python scripts/genstubs.py")
    if STUBS_DIR not in sys.path:
        sys.path.insert(0, STUBS_DIR)
    import _sldworks_gen as L  # noqa: E402
    import swenums as E  # noqa: E402
    _stub_cache["L"], _stub_cache["E"] = L, E
    return L, E


def cast(obj, iface):
    """Re-wrap a COM object in the early-bound class for `iface`.

    Two jobs, one operation: binding a late-bound object (ActiveDoc), and
    casting between interfaces of the same object (IModelDoc2 -> IPartDoc, which
    is where GetBodies2 lives).  The interface must be named explicitly - see the
    module docstring for why guessing is not an option."""
    if obj is None:
        return None
    L, _ = stubs()
    cls = getattr(L, iface, None)
    if cls is None:
        raise SwError("no interface %r in the generated stubs" % iface,
                      hint="Check the spelling, or run: python sw/swapi.py %s" % iface)
    return cls(getattr(obj, "_oleobj_", obj))


def is_bound(obj):
    return hasattr(obj, "_prop_map_get_")


def getv(obj, name):
    """Read a member that may be either a property or a zero-argument method."""
    if not is_bound(obj):
        raise SwError(
            "%r is a late-bound object; cast(obj, '<IInterface>') before reading %r"
            % (type(obj).__name__, name),
            hint="Late-bound objects cannot tell properties from methods.")
    value = getattr(obj, name)
    if name in (obj._prop_map_get_ or {}):
        return value
    return value() if callable(value) else value


def call(obj, name, *args):
    """Invoke a real method (never a property).

    The guard matters: pywin32 sometimes resolves a name to its VALUE on
    attribute access, so calling it produces a bare
    "TypeError: 'str' object is not callable" with no clue about what went wrong.
    (Measured: feature.GetTypeName2 is a property on one path and a method on
    another; GetTitle is a method while Extension is a property.)"""
    value = getattr(obj, name)
    if not callable(value):
        raise SwError(
            "%s.%s is a property (value %r), not a method"
            % (type(obj).__name__, name, value),
            hint="Read it with getv(obj, %r) instead. Check with: "
                 "swbridge.py api %s --props" % (name, type(obj).__name__))
    return value(*args)


def dump_signature(iface, method):
    """Offline signature lookup (see sw/swapi.py)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import swapi
    hits = [p for c, p in swapi.find_method(method) if c == iface]
    return hits[0] if hits else None


# --------------------------------------------------------------------------- retry


def retry_call(fn, *args, tries=8, base=0.25, cap=4.0, what=None, **kwargs):
    """Call something that SOLIDWORKS may reject because it is momentarily busy.

    Pywin32 does not expose CoRegisterMessageFilter, so the documented
    IOleMessageFilter approach is unavailable; exponential backoff is the
    substitute.  Only the two 'try again' HRESULTs are retried - anything else is
    a real failure and is raised immediately."""
    label = what or getattr(fn, "__name__", "call")
    last = None
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised below unless retryable
            hr = _hresult_of(exc)
            if hr not in RETRY_HRESULTS:
                raise translate(exc, step=label) from None
            last = exc
            time.sleep(min(cap, base * (2 ** attempt)))
    raise translate(last, step="%s (gave up after %d tries)" % (label, tries))


class _OutArg:
    """Marker for an [in,out] parameter - see call_out()."""

    def __repr__(self):
        return "OUTARG"


OUTARG = _OutArg()

#: What actually goes to COM in an [in,out] slot.  Measured against SOLIDWORKS
#: 2025 + pywin32 312, every "proper" spelling fails:
#:   win32com.client.VARIANT(VT_BYREF|VT_I4, 0) -> TypeError: int() argument ... not 'VARIANT'
#:   [0]                                        -> TypeError: int() argument ... not 'list'
#:   pythoncom.Missing                          -> com_error DISP_E_PARAMNOTOPTIONAL
#:   plain 0                                    -> works
_OUT_PLACEHOLDER = 0


def call_out(obj, name, *args):
    """Call a method that reports through [in,out] parameters.

    Put OUTARG in each out slot and unpack the extra values from the return:

        props, (status,) = call_out(ext, 'GetMassProperties2', 1, OUTARG, False)
        ok, (errors, warnings) = call_out(app, 'OpenDoc6', path, 1, 1, '', OUTARG, OUTARG)

    pywin32 does NOT write back into whatever you pass; it APPENDS the out values
    to the return value, so the tuples above are what the calls really return.
    Forgetting this is how 'errors=0' silently becomes 'it just did not work'."""
    out_positions = [i for i, a in enumerate(args) if a is OUTARG]
    wire = tuple(_OUT_PLACEHOLDER if a is OUTARG else a for a in args)
    raw = getattr(obj, name)(*wire)
    if not out_positions:
        return raw, []
    if not isinstance(raw, tuple) or len(raw) < 1 + len(out_positions):
        raise SwError(
            "%s returned %r; expected the result plus %d out value(s)"
            % (name, raw, len(out_positions)),
            hint="pywin32 appends [in,out] values to the return value; check the "
                 "signature with:  swbridge.py api %s" % name)
    return raw[0], list(raw[1:1 + len(out_positions)])


# --------------------------------------------------------------------------- ROT


def _clsid(progid=PROGID):
    import pywintypes
    return pywintypes.IID(progid)  # ProgID -> CLSID, registry, safe on this install


def rot_entries():
    """Everything currently registered in the Running Object Table.

    Useful diagnostics: it shows whether SOLIDWORKS actually published its
    automation object, without needing to bind to it."""
    import pythoncom
    out = []
    try:
        rot = pythoncom.GetRunningObjectTable()
        for moniker in rot:
            try:
                name = moniker.GetDisplayName(pythoncom.CreateBindCtx(), None)
            except Exception:
                name = "?"
            out.append(name)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never explode
        out.append("<ROT enumeration failed: %s>" % exc)
    return out


def is_running():
    """True if SOLIDWORKS has published its automation object."""
    import pythoncom
    try:
        return pythoncom.GetActiveObject(_clsid()) is not None
    except Exception:
        return False


def find_exe():
    """Locate SLDWORKS.exe, preferring the installer record."""
    try:
        import winreg
        for year in range(2030, 2009, -1):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                    r"SOFTWARE\SolidWorks\SOLIDWORKS %d\Setup" % year) as key:
                    folder = winreg.QueryValueEx(key, "SolidWorks Folder")[0]
                exe = os.path.join(folder, "SLDWORKS.exe")
                if os.path.isfile(exe):
                    return exe
            except OSError:
                continue
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"SldWorks.Application\CLSID") as key:
            clsid = winreg.QueryValueEx(key, "")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            r"CLSID\%s\LocalServer32" % clsid) as key:
            exe = winreg.QueryValueEx(key, "")[0].strip('"')
        if os.path.isfile(exe):
            return exe
    except OSError:
        pass
    for guess in (r"D:\Mango\sw2025\SOLIDWORKS Corp 2025\SOLIDWORKS\SLDWORKS.exe",
                  r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"):
        if os.path.isfile(guess):
            return guess
    return None


def exe_version(exe):
    try:
        import win32api
        info = win32api.GetFileVersionInfo(exe, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:  # noqa: BLE001 - cosmetic
        return None


# --------------------------------------------------------------------------- session


class Session:
    """An attached, early-bound SOLIDWORKS application object."""

    def __init__(self, app, progid=PROGID):
        self.app = app
        self.progid = progid
        self.attach_ms = None

    # ---- construction ----------------------------------------------------
    @classmethod
    def attach(cls, progid=PROGID):
        import pythoncom
        pythoncom.CoInitialize()
        t0 = time.time()
        try:
            raw = pythoncom.GetActiveObject(_clsid(progid))  # pure ROT lookup
        except Exception as exc:
            raise SwError(
                "no running SOLIDWORKS automation object (%s)" % progid,
                hresult=_hresult_of(exc),
                hint="SOLIDWORKS is not running, or it has not finished starting. "
                     "Try:  swbridge.py launch") from None
        raw = raw.QueryInterface(pythoncom.IID_IDispatch)
        session = cls(cast(raw, "ISldWorks"), progid)
        session.attach_ms = int((time.time() - t0) * 1000)
        return session

    # ---- facts -----------------------------------------------------------
    @property
    def revision(self):
        return call(self.app, "RevisionNumber")

    @property
    def pid(self):
        return call(self.app, "GetProcessID")

    @property
    def busy(self):
        return bool(getv(self.app, "CommandInProgress"))

    def documents(self):
        return [cast(d, "IModelDoc2") for d in (call(self.app, "GetDocuments") or [])]

    def document_count(self):
        return call(self.app, "GetDocumentCount")

    def titles(self):
        return [call(d, "GetTitle") for d in self.documents()]

    def active(self):
        doc = getv(self.app, "ActiveDoc")
        if doc is None:
            raise SwError("no active document",
                          hint="Open one first: swbridge.py open <file>")
        return cast(doc, "IModelDoc2")

    # ---- actions ---------------------------------------------------------
    def load_neutral(self, path):
        """Import a neutral-format file with LoadFile4 instead of OpenDoc6.

        Measured on SOLIDWORKS 2025 SP5.0 (33.5.0.53), 2026-09-15: OpenDoc6 refuses
        EVERY neutral file with errors=2097152 (swFileRequiresRepairError) in ~0.2 s -
        including a STEP that this same session had just exported, so the file is not
        the problem and no import preference fixes it (3D Interconnect was already
        off; CheckAndRepair made no difference).  ISldWorks.LoadFile4 opens the very
        same file with errors=0.

        Caveat worth knowing before you use the result: LoadFile4 brings a
        single-solid STEP in as an ASSEMBLY (.SLDASM), with the solid living in the
        component's part document.  Take that part doc
        (`cast(comp, "IComponent2").GetModelDoc2()`) and SaveAs3 it if you want a
        native .SLDPRT.
        """
        if not os.path.isfile(path):
            raise SwError("no such file: %s" % path)
        doc, (errors,) = call_out(self.app, "LoadFile4", path, "", None, OUTARG)
        if doc is None:
            raise SwError(
                "LoadFile4 returned no document for %s (errors=%s)" % (path, errors),
                hint="Look the codes up:  swbridge.py enum swFileLoadError")
        return cast(doc, "IModelDoc2")

    def open_document(self, path, doc_type=None, options=None, configuration=""):
        """Open a document silently and CHECK the error out-parameters.

        OpenDoc6 signals failure by returning None and stuffing the reason into
        Errors/Warnings ([in,out] VT_BYREF|VT_I4).  They must be collected with
        call_out(), otherwise the failure reason is lost and the caller only sees
        "it opened nothing".

        For a neutral format (STEP/IGES/Parasolid/...) a failure from OpenDoc6 is
        expected on this build and is retried with LoadFile4 - see load_neutral()
        for the measurement and for the assembly-not-part caveat."""
        L, E = stubs()
        if not os.path.isfile(path):
            raise SwError("no such file: %s" % path)
        if doc_type is None:
            doc_type = {".sldprt": E.swDocPART, ".sldasm": E.swDocASSEMBLY,
                        ".slddrw": E.swDocDRAWING}.get(
                os.path.splitext(path)[1].lower(), E.swDocPART)
        if options is None:
            options = E.swOpenDocOptions_Silent | E.swOpenDocOptions_ReadOnly
        doc, (errors, warnings) = call_out(
            self.app, "OpenDoc6", path, doc_type, options, configuration, OUTARG, OUTARG)
        if doc is None:
            if os.path.splitext(path)[1].lower() in NEUTRAL_EXTS:
                return self.load_neutral(path)
            raise SwError(
                "OpenDoc6 returned no document for %s (errors=%s warnings=%s)"
                % (path, errors, warnings),
                hint="Look the codes up:  swbridge.py enum swFileLoadError")
        return cast(doc, "IModelDoc2")

    def close_document(self, title, discard_changes=True):
        """Close a document WITHOUT ever raising a modal dialog.

        CloseDoc on a modified document pops 'save changes?' and the COM call
        blocks forever - there is no timeout.  SetSaveFlag() marks the document
        clean first, which is what makes this safe."""
        doc = None
        for candidate in self.documents():
            if call(candidate, "GetTitle") == title:
                doc = candidate
                break
        if doc is None:
            return False
        if discard_changes:
            try:
                call(doc, "SetSaveFlag")
            except Exception as exc:  # noqa: BLE001
                raise translate(exc, step="SetSaveFlag") from None
        retry_call(self.app.CloseDoc, title, what="CloseDoc")
        return True

    def quit(self, force=False):
        """Exit SOLIDWORKS.  Refuses while documents are open unless forced."""
        docs = self.documents()
        if docs and not force:
            raise SwError(
                "%d document(s) are open: %s" % (len(docs), self.titles()),
                hint="Save and close them yourself, or pass --force to DISCARD "
                     "unsaved changes and exit.")
        for title in self.titles():
            self.close_document(title, discard_changes=True)
        retry_call(self.app.ExitApp, what="ExitApp")


def launch(exe=None, wait=240, poll=3.0, log=print):
    """Start SOLIDWORKS if it is not running and wait for COM to answer."""
    import pythoncom
    pythoncom.CoInitialize()
    if is_running():
        log("already running (COM answers) - nothing to launch")
        return Session.attach()
    exe = exe or find_exe()
    if not exe:
        raise SwError("cannot find SLDWORKS.exe",
                      hint="Pass --exe <path>, or check the SOLIDWORKS installation.")
    t0 = time.time()
    log("starting %s" % exe)
    subprocess.Popen([exe], close_fds=True)
    deadline = t0 + wait
    while time.time() < deadline:
        time.sleep(poll)
        try:
            session = Session.attach()
        except SwError:
            continue
        session.launch_seconds = round(time.time() - t0, 1)
        log("COM ready after %.1fs (pid %s, revision %s)"
            % (session.launch_seconds, session.pid, session.revision))
        return session
    raise SwError("SOLIDWORKS did not answer COM within %ds" % wait,
                  hint="It may be sitting on a license or welcome dialog. Check the "
                       "screen for a modal dialog and dismiss it.")


# --------------------------------------------------------------------------- units

#: The API is SI throughout: lengths in METRES, mass in KILOGRAMS, angles in
#: RADIANS.  A 50 mm box is 0.05.  This is the single most common way to get a
#: silently wrong result, so the helpers are here to be used, not remembered.


def mm(value):
    """Millimetres -> the metres the API expects."""
    return value / 1000.0


def to_mm(value):
    """Metres -> millimetres, for reporting."""
    return value * 1000.0


# --------------------------------------------------------------------------- export

#: Export formats VERIFIED on this install (SOLIDWORKS 2025 SP5, 33.5.0.0053), all
#: produced by  SaveAs3(path, swSaveAsCurrentVersion=0, swSaveAsOptions_Silent=1):
#:
#:   .STEP  15752 B  ISO-10303-21
#:   .STL     684 B  binary, header declares 12 triangles and 84 + 12*50 == 684
#:   .IGS   22386 B  ASCII IGES
#:   .PDF  156800 B  %PDF-1.4 ... %%EOF
#:   .X_T    7427 B  **ABCDEFGHIJKLMNOPQRSTUVWXYZ (Parasolid)
#:   .3MF    6618 B  PK zip
#:   .OBJ            FAILS with code 256 (no translator installed)
#:
#: So: the FORMAT comes from the extension, the version must be 0, and the options
#: must include Silent.  A different version (1) fails with code 32.  The return
#: value is an error code, not a bool - 0 means success.
EXPORT_FORMATS = {
    "step": {"ext": ".STEP", "magic": b"ISO-10303-21"},
    "stl": {"ext": ".STL", "kind": "stl-binary"},
    "iges": {"ext": ".IGS", "contains": b"IGES"},
    "pdf": {"ext": ".PDF", "magic": b"%PDF", "tail": b"%%EOF"},
    "x_t": {"ext": ".X_T", "magic": b"**ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    "3mf": {"ext": ".3MF", "magic": b"PK"},
}


def validate_export(path, fmt):
    """Structurally check an export.  "The file exists" is not evidence.

    The STL test is the strongest one available here: a binary STL declares its
    triangle count at offset 80, and the file must then be exactly
    84 + 50 * count bytes - so a truncated, empty or wrong-body export cannot
    pass.  (A 50x30x20 mm box reports exactly 12 triangles.)"""
    import struct
    size = os.path.getsize(path)
    if size == 0:
        return False, "the file is empty"
    with open(path, "rb") as handle:
        head = handle.read(84)
        rest = handle.read()
    spec = EXPORT_FORMATS[fmt]
    if "magic" in spec and not head.startswith(spec["magic"]):
        return False, "expected %r at the start, found %r" % (spec["magic"], head[:16])
    if "contains" in spec and spec["contains"] not in head:
        return False, "expected %r in the header, found %r" % (spec["contains"], head[:48])
    if "tail" in spec and spec["tail"] not in rest[-2048:]:
        return False, "truncated: no %r near the end" % spec["tail"]
    if spec.get("kind") == "stl-binary":
        if size < 84 or (size - 84) % 50:
            return False, "%d bytes is not 84 + 50*n, so it is truncated" % size
        count = struct.unpack("<I", head[80:84])[0]
        if 84 + 50 * count != size:
            return False, ("the header claims %d triangles (%d bytes) but the file "
                           "is %d bytes" % (count, 84 + 50 * count, size))
        if count == 0:
            return False, "the STL declares zero triangles"
        return True, "%d triangles, %d bytes" % (count, size)
    return True, "%d bytes" % size


def export(doc, path, fmt):
    """Export the document and verify the result.  Returns a summary dict."""
    L, E = stubs()
    fmt = fmt.lower()
    if fmt not in EXPORT_FORMATS:
        raise SwError("unknown export format %r" % fmt,
                      hint="Known formats: %s" % ", ".join(sorted(EXPORT_FORMATS)))
    spec = EXPORT_FORMATS[fmt]
    if not path.lower().endswith(spec["ext"].lower()):
        path = os.path.splitext(path)[0] + spec["ext"]
    if os.path.isfile(path):
        os.remove(path)
    code = call(doc, "SaveAs3", path, E.swSaveAsCurrentVersion, E.swSaveAsOptions_Silent)
    if code != 0:
        raise SwError(
            "SOLIDWORKS refused the %s export (SaveAs3 returned %d = %s)"
            % (fmt, code, E.name_of(code, "swFileSave")),
            hint="A non-zero SaveAs3 return value is an error code, not a bool. "
                 "Formats whose translator this install does not have fail here "
                 "(measured: .OBJ -> 256). Exactly what the code means is "
                 "ambiguous - several enum families share these numbers - so check "
                 "swbridge.py enum swFileSaveAs if you need the detail.")
    if not os.path.isfile(path):
        raise SwError("SaveAs3 reported success but %s does not exist" % path)
    ok, detail = validate_export(path, fmt)
    if not ok:
        raise SwError("the exported %s failed verification: %s" % (fmt, detail))
    return {"format": fmt, "path": path, "bytes": os.path.getsize(path), "check": detail}


# --------------------------------------------------------------------------- dialogs


def dialogs(pid):
    """Visible top-level windows belonging to a process, dialogs included.

    A modal dialog is the one failure this bridge cannot time out: the COM call
    simply never returns, and the job sits at RUNNING forever with no clue why.
    This is how you find out that is what happened, and dismiss_dialog() is how
    you get out of it."""
    import win32gui
    import win32process
    found = []

    def collect(hwnd, _):
        try:
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid != pid:
                return True
            if not win32gui.IsWindowVisible(hwnd):
                return True
            texts = []
            win32gui.EnumChildWindows(
                hwnd, lambda h, _p: texts.append(win32gui.GetWindowText(h)) or True, None)
            found.append({
                "handle": hwnd,
                "class": win32gui.GetClassName(hwnd),
                "title": win32gui.GetWindowText(hwnd),
                # Buttons are enumerated before the message Static, so the cap has
                # to be generous or the actual message text gets cut off.
                "child_text": [t for t in texts if t][:24],
                "modal": win32gui.GetClassName(hwnd) == "#32770",
            })
        except Exception:  # noqa: BLE001 - diagnostics must never explode
            pass
        return True

    win32gui.EnumWindows(collect, None)
    return found


def dismiss_dialog(handle, button="cancel"):
    """Answer a modal dialog from outside, so a wedged bridge can recover.

    Defaults to CANCEL on purpose: pressing OK on a "save changes?" dialog would
    mean "yes, overwrite the file", while Cancel aborts that operation and only
    leaves the document open.  Nothing is posted until the caller asks."""
    import win32con
    import win32gui
    command = win32con.IDOK if button.lower() == "ok" else win32con.IDCANCEL
    win32gui.PostMessage(handle, win32con.WM_COMMAND, command, 0)
    return command


# --------------------------------------------------------------------------- tree


def iter_features(doc, limit=5000):
    """Walk the feature tree, yielding EARLY-BOUND IFeature objects.

    This exists because feature navigation is the single most repeated trap in
    this API: `FirstFeature()` and `GetNextFeature()` carry no CLSID in the type
    library, so pywin32 hands back a late-bound CDispatch, and on a late-bound
    object an unknown name is silently resolved with PROPERTYGET - making
    `feature.GetTypeName2` already a string, so calling it raises
    "'str' object is not callable".  Casting here, once, means no job has to
    remember.  (Written after forgetting the cast in a real job.)"""
    feature = cast(call(doc, "FirstFeature"), "IFeature")
    count = 0
    while feature is not None and count < limit:
        yield feature
        count += 1
        feature = cast(call(feature, "GetNextFeature"), "IFeature")


def reference_planes(doc):
    """Return ({'front','top','right'} -> names, [all plane names in tree order]).

    Planes are matched by NAME in either language (the UI may be Chinese:
    '前视基准面'), and only fall back to tree position - front, top, right in a
    default template - when no name matches."""
    names = [getv(feature, "Name") for feature in iter_features(doc)
             if getv(feature, "GetTypeName2") == "RefPlane"]

    def pick(needles, index):
        for name in names:
            if any(needle in name.lower() for needle in needles):
                return name
        return names[index] if len(names) > index else None

    return ({"front": pick(["front", "前视"], 0),
             "top": pick(["top", "上视"], 1),
             "right": pick(["right", "右视"], 2)}, names)


# --------------------------------------------------------------------------- shots


def shot(session, out=None, width=1920, height=1080, log=print):
    """Render the active document's graphics area to a PNG.

    SaveBMP is the good path: it renders the model only - no window chrome, no
    focus stealing, and it works even when the window is hidden behind others.
    Measured on a 1600x1000 part view: 4.8 MB BMP, 100% non-black, 793 colours.
    """
    ensure_dirs()
    doc = session.active()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bmp = os.path.join(SHOTS_DIR, "shot-%s.bmp" % stamp)
    png = out or os.path.join(SHOTS_DIR, "shot-%s.png" % stamp)
    if not call(doc, "SaveBMP", bmp, width, height):
        raise SwError("SaveBMP returned False",
                      hint="Some documents have no graphics area to render "
                           "(e.g. nothing visible); check the model view.")
    from PIL import Image
    img = Image.open(bmp).convert("RGB")
    img.save(png, "PNG")
    try:
        os.remove(bmp)
    except OSError:
        pass
    log("SHOT   : %s  (%dx%d)" % (png, img.width, img.height))
    return png


# --------------------------------------------------------------------------- tasks


def new_id():
    return time.strftime("%Y%m%d_%H%M%S") + "_" + str(int(time.time() * 1000) % 1000)


def status_path(task_id):
    return os.path.join(STATUS_DIR, "%s.txt" % task_id)


def log_path(task_id):
    return os.path.join(LOGS_DIR, "%s.log" % task_id)


def write_status(task_id, state, detail=""):
    ensure_dirs()
    tmp = status_path(task_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(state + "\n")
        if detail:
            handle.write(detail)
    os.replace(tmp, status_path(task_id))


def read_status(task_id, tail_lines=40):
    ensure_dirs()
    state, detail = "NO-STATUS", ""
    path = status_path(task_id)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read().strip()
        if raw:
            first, _, rest = raw.partition("\n")
            state, detail = first.strip(), rest.strip()
    log_file = log_path(task_id)
    tail = ""
    if os.path.isfile(log_file):
        with open(log_file, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        tail = "\n".join(lines[-tail_lines:])
        if len(tail) > 4000:
            tail = tail[-4000:]
    return {"id": task_id, "state": state, "detail": detail,
            "log": log_file, "tail": tail}


def prune(days=7):
    """Housekeeping: drop task files older than N days."""
    cutoff = time.time() - days * 86400
    for folder in (LOGS_DIR, STATUS_DIR, TASKS_DIR):
        for path in glob.glob(os.path.join(folder, "*")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


# --------------------------------------------------------------------------- jobs


JOB_PREAMBLE = '''\
# ---- injected by swbridge: the live session is already attached for you ----
# app   : early-bound ISldWorks          doc  : early-bound IModelDoc2 (if any)
# L     : generated sldworks interface classes
# E     : generated constants (E.swOpenDocOptions_Silent, ...)
# cast/getv/call : COM helpers (see swcore docstring)
# retry : retry_call, for calls SOLIDWORKS may reject while busy
# OUT   : per-task output directory, use it for anything you produce
# log() : print with an immediate flush
'''


def run_job(source, task_id=None, out_dir=None, log=print):
    """Execute a job body in-process against the live session, with a status file.

    The body is user code.  It fails loudly: any exception is written to the
    status file as ERR with the traceback in the detail field, so a caller that
    only has files (the async path) can still see what happened."""
    task_id = task_id or new_id()
    out_dir = out_dir or os.path.join(OUT_DIR, task_id)
    os.makedirs(out_dir, exist_ok=True)
    write_status(task_id, "RUNNING")
    started = time.time()
    try:
        session = Session.attach()
        try:
            doc = session.active()
        except SwError:
            doc = None
        L, E = stubs()
        namespace = {
            "__name__": "__swjob__",
            "app": session.app,
            "doc": doc,
            "session": session,
            "L": L,
            "E": E,
            "cast": cast,
            "getv": getv,
            "call": call,
            "retry": retry_call,
            "retry_call": retry_call,
            "call_out": call_out,
            "OUTARG": OUTARG,
            "mm": mm,
            "to_mm": to_mm,
            "iter_features": iter_features,
            "reference_planes": reference_planes,
            "dialogs": lambda: dialogs(call(session.app, "GetProcessID")),
            "OUT": out_dir,
            "HOME": HOME,
            "log": lambda *a: (log(*a), sys.stdout.flush()),
        }
        code = compile(source, "<swjob>", "exec")
        exec(code, namespace)
    except Exception as exc:  # noqa: BLE001 - this is the reporting boundary
        import traceback
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        write_status(task_id, "ERR", detail + "\n" + traceback.format_exc())
        log("STATE  : ERR")
        log("DETAIL : %s" % detail)
        log("LOG    : %s" % log_path(task_id))
        return 1
    elapsed = time.time() - started
    write_status(task_id, "OK", "elapsed_sec=%.2f\nout=%s" % (elapsed, out_dir))
    log("STATE  : OK  (%.2fs)" % elapsed)
    log("OUT    : %s" % out_dir)
    log("LOG    : %s" % log_path(task_id))
    return 0


# --------------------------------------------------------------------------- doctor


def environment_report():
    """Everything `doctor` knows, as plain data."""
    import platform
    report = {
        "python": sys.version.split()[0],
        "python_exe": sys.executable,
        "python_bits": 64 if sys.maxsize > 2 ** 32 else 32,
        "bridge_home": HOME,
        "progid": PROGID,
        "stubs_dir": STUBS_DIR,
    }
    # pywin32 is a distribution, not an importable module name, so probe the
    # modules we actually call and report the build from its DLL version.
    import pythoncom
    pythoncom_path = pythoncom.__file__
    build = exe_version(pythoncom_path)
    missing = []
    for modname in ("pythoncom", "win32com.client", "win32api"):
        try:
            __import__(modname)
        except Exception as exc:  # noqa: BLE001
            missing.append("%s (%s)" % (modname, exc))
    if missing:
        report["pywin32"] = "BROKEN: " + "; ".join(missing)
    else:
        report["pywin32"] = "build %s  (%s)" % (build or "?", pythoncom_path)
    for name in ("numpy", "PIL"):
        try:
            mod = __import__(name)
            report[name] = getattr(mod, "__version__", "present")
        except Exception as exc:  # noqa: BLE001
            report[name] = "MISSING (%s)" % exc
    meta = os.path.join(STUBS_DIR, "meta.json")
    if os.path.isfile(meta):
        with open(meta, "r", encoding="utf-8") as handle:
            stub_meta = json.load(handle)
        report["stubs"] = {"sw_version": stub_meta.get("sw_version"),
                           "generated_utc": stub_meta.get("generated_utc"),
                           "enums": (stub_meta.get("enums") or {}).get("count")}
    else:
        report["stubs"] = None

    exe = find_exe()
    report["exe"] = exe
    report["exe_version"] = exe_version(exe) if exe else None

    # The landmine this whole tool is built around: the registry advertises a
    # different version for the SOLIDWORKS type library than the .tlb file
    # actually contains, and COM refuses to load it as a result.  Report both
    # numbers so nobody has to rediscover it.
    registered = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            r"TypeLib\{83A33D31-27C5-11CE-BFD4-00400513BB57}") as key:
            index = 0
            while True:
                try:
                    registered.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except OSError:
        pass
    file_version = None
    if exe:
        tlb = os.path.join(os.path.dirname(exe), "sldworks.tlb")
        if os.path.isfile(tlb):
            try:
                import pythoncom
                attr = pythoncom.LoadTypeLib(tlb).GetLibAttr()
                file_version = "%d.%d" % (attr[3], attr[4])
            except Exception:  # noqa: BLE001
                pass
    report["typelib"] = {
        "registered": registered,
        "file_version": file_version,
        "mismatch": file_version is not None and file_version not in registered,
    }

    report["rot"] = rot_entries()
    report["sw_running"] = is_running()
    if report["sw_running"]:
        try:
            session = Session.attach()
            report["attach_ms"] = session.attach_ms
            report["revision"] = session.revision
            report["pid"] = session.pid
            report["documents"] = session.titles()
            report["busy"] = session.busy
        except SwError as exc:
            report["attach_error"] = str(exc)
    return report


def main():  # pragma: no cover - convenience only
    print(json.dumps(environment_report(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
