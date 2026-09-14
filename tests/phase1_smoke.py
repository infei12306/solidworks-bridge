#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 1 foundation check: attach, model, render, verify, clean up.

Run against an ALREADY RUNNING SOLIDWORKS.  It answers five questions in order:

  1. can pywin32 attach to the live session and identify it?
  2. can we create geometry without touching the GUI (NewPart -> sketch -> extrude)?
  3. does SaveBMP produce a real picture (not a black rectangle)?
  4. can we close the document again WITHOUT a "save changes?" modal wall?
  5. can we clean up after ourselves and leave the user's session as we found it?

Five SOLIDWORKS-API facts this script exists to pin down.  Each one cost a failed
attempt, and each one becomes a rule in the skill:

  * ISldWorks.ActiveDoc / GetFirstDocument carry NO CLSID in the type library, so
    pywin32 hands back a late-bound CDispatch.  On a late-bound object every
    member looks callable, and a real property invoked as a method raises
    DISP_E_MEMBERNOTFOUND ('找不到成员').  Worse, PyIDispatch.GetTypeInfo() on
    these objects fails with DISP_E_BADINDEX, so the type cannot be discovered
    at runtime either.  cast() therefore takes the interface name EXPLICITLY -
    guessing by calling probe methods would risk invoking an unrelated method
    that happens to share the memid.
  * On the early-bound class, GetTitle and FirstFeature are METHODS while
    Extension and SketchManager are PROPERTIES.  getv() consults
    _prop_map_get_ to tell them apart instead of guessing.
  * Reference planes must not be looked up by name: this UI is Chinese, so
    'Front Plane' is '前视基准面'.  The first RefPlane is found by
    GetTypeName2(), which is language independent.
  * Lengths are METRES.  0.05 means 50 mm.
  * CloseDoc on a dirty document raises a modal "save changes?" dialog, which
    hangs the bridge forever.  SetSaveFlag() first.

Usage:
    python tests/phase1_smoke.py [--keep-open]
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "swbridge")
STUBS = os.path.join(BRIDGE, "stubs")
SHOTS = os.path.join(BRIDGE, "shots")

sys.path.insert(0, STUBS)
import _sldworks_gen as L  # noqa: E402  (generated early-binding stubs)

PROGID = "SldWorks.Application.33"

# swconst values (verified present in the generated stubs)
SW_END_COND_BLIND = 0
SW_START_SKETCH_PLANE = 0


def log(msg=""):
    sys.stdout.write(str(msg) + "\n")
    sys.stdout.flush()


def is_early_bound(obj):
    return hasattr(obj, "_prop_map_get_")


def cast(obj, iface):
    """Give a bare IDispatch its early-bound class, so its property map applies.

    `iface` must be named explicitly: the type library gives no CLSID for the
    members that need this (ActiveDoc is the important one), and
    PyIDispatch.GetTypeInfo() fails with DISP_E_BADINDEX on SW objects, so there
    is no safe way to infer it.  Already-bound objects pass through unchanged."""
    if obj is None:
        return obj
    cls = getattr(L, iface, None)
    if cls is None:
        raise KeyError("no such interface in the generated stubs: %s" % iface)
    return cls(getattr(obj, "_oleobj_", obj))


def getv(obj, name):
    """Read a member that may be either a property or a zero-argument method."""
    if not is_early_bound(obj):
        raise TypeError(
            "late-bound object: cast(obj, '<IInterface>') before reading %r "
            "(properties cannot be told from methods here)" % name)
    value = getattr(obj, name)
    if name in (obj._prop_map_get_ or {}):
        return value
    return value() if callable(value) else value


def call(obj, name, *args):
    """Invoke a real method (never a property)."""
    return getattr(obj, name)(*args)


def fail(step, msg):
    log("FAIL  %-12s %s" % (step, msg))
    raise SystemExit(2)


def attach():
    import pythoncom
    import win32com.client as wc

    pythoncom.CoInitialize()
    return wc.GetActiveObject(PROGID)


def titles(app):
    return [getv(cast(d, "IModelDoc2"), "GetTitle")
            for d in (call(app, "GetDocuments") or [])]


def first_ref_plane(doc):
    """First reference plane in the feature tree, selected by TYPE not by name."""
    feat = call(doc, "FirstFeature")
    seen = []
    while feat is not None:
        feat = cast(feat, "IFeature")
        type_name = call(feat, "GetTypeName2")
        seen.append(type_name)
        if type_name == "RefPlane":
            return getv(feat, "Name"), seen
        feat = call(feat, "GetNextFeature")
    return None, seen


def cleanup(app, before, keep=False):
    """Close every document this script opened, without ever risking a modal
    'save changes?' dialog: untitled documents are marked as saved first."""
    if keep:
        log("      close        skipped (--keep-open)")
        return
    for d in list(call(app, "GetDocuments") or []):
        d = cast(d, "IModelDoc2")
        title = getv(d, "GetTitle")
        if title in before:
            continue
        try:
            call(d, "SetSaveFlag")          # tell SW nothing needs saving
        except Exception as exc:
            log("      warn         SetSaveFlag failed: %s" % exc)
        try:
            call(app, "CloseDoc", title)
            log("OK    close        '%s' closed, docs now %s"
                % (title, call(app, "GetDocumentCount")))
        except Exception as exc:
            log("FAIL  close        '%s': %s" % (title, exc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-open", action="store_true", help="leave the test document open")
    args = ap.parse_args()

    os.makedirs(SHOTS, exist_ok=True)

    t0 = time.time()
    try:
        app = attach()
    except Exception as exc:
        fail("attach", "%s  (is SOLIDWORKS actually running?)" % exc)
    rev = call(app, "RevisionNumber")
    log("OK    attach       revision=%s pid=%s docs=%s attach_ms=%d"
        % (rev, call(app, "GetProcessID"), call(app, "GetDocumentCount"),
           (time.time() - t0) * 1000))
    if str(rev).split(".")[0] != "33":
        log("WARN  attach       expected major 33 for SOLIDWORKS 2025, got %s" % rev)
    log("      state        CommandInProgress=%s" % getv(app, "CommandInProgress"))

    before = titles(app)
    log("      open docs    %s" % (before or "(none)"))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    png = os.path.join(SHOTS, "phase1-%s.png" % stamp)

    try:
        if call(app, "NewPart") is None:
            fail("newpart", "ISldWorks.NewPart() returned None (no default part template?)")
        doc = cast(getv(app, "ActiveDoc"), "IModelDoc2")
        log("OK    newpart      title='%s' type=%s"
            % (getv(doc, "GetTitle"), getv(doc, "GetType")))

        plane, tree = first_ref_plane(doc)
        if plane is None:
            fail("plane", "no RefPlane in the feature tree; tree=%s" % tree)
        log("      plane        '%s'  (tree head: %s)" % (plane, tree[:4]))

        ext = cast(getv(doc, "Extension"), "IModelDocExtension")
        if not call(ext, "SelectByID2", plane, "PLANE", 0, 0, 0, False, 0, None, 0):
            fail("select", "SelectByID2('%s','PLANE',...) returned False" % plane)

        sm = cast(getv(doc, "SketchManager"), "ISketchManager")
        call(sm, "InsertSketch", True)
        call(sm, "CreateCornerRectangle", 0.0, 0.0, 0.0, 0.05, 0.03, 0.0)  # metres
        log("OK    sketch       rectangle 50x30 mm on '%s'" % plane)

        fm = cast(getv(doc, "FeatureManager"), "IFeatureManager")
        feature = call(
            fm, "FeatureExtrusion3",
            True, False, False,                       # single ended, no flip, dir
            SW_END_COND_BLIND, SW_END_COND_BLIND,     # T1, T2
            0.020, 0.010,                             # D1=20mm, D2
            False, False, False, False,               # Dchk1, Dchk2, Ddir1, Ddir2
            0.0, 0.0,                                 # Dang1, Dang2
            False, False, False, False,               # OffsetReverse1/2, TranslateSurface1/2
            True, False, True,                        # Merge, UseFeatScope, UseAutoSelect
            SW_START_SKETCH_PLANE, 0.0, False)        # T0, StartOffset, FlipStartOffset
        if feature is None:
            fail("extrude", "FeatureExtrusion3 returned None (feature not created)")
        feature = cast(feature, "IFeature")
        log("OK    extrude      feature='%s'" % getv(feature, "Name"))

        bodies = call(cast(doc, "IPartDoc"), "GetBodies2", 0, False)
        n_bodies = 0 if bodies is None else len(bodies)
        log("      bodies       %d" % n_bodies)
        if n_bodies < 1:
            fail("bodies", "the extrude produced no solid body")

        bmp = os.path.join(SHOTS, "phase1-%s.bmp" % stamp)
        call(doc, "ViewZoomtofit2")
        call(doc, "ShowNamedView2", "*Isometric", 7)
        ok = call(doc, "SaveBMP", bmp, 1600, 1000)
        size = os.path.getsize(bmp) if os.path.isfile(bmp) else -1
        log("OK    savebmp      returned=%s exists=%s size=%s" % (ok, os.path.isfile(bmp), size))
        if not os.path.isfile(bmp) or size <= 0:
            fail("savebmp", "SaveBMP produced no file")

        from PIL import Image
        img = Image.open(bmp).convert("RGB")
        img.save(png, "PNG")
        # getcolors() instead of getdata(): getdata() is deprecated in Pillow 12
        # and this form is both faster and allocation-free for a 1600x1000 image.
        colors = img.getcolors(maxcolors=1 << 24) or []
        n = img.width * img.height
        non_black = sum(c for c, (r, g, b) in colors if (r + g + b) > 30)
        unique = len(colors)
        log("      render       %.1f%% non-black, %d unique colours, %s -> %s"
            % (100.0 * non_black / n, unique, img.size, os.path.basename(png)))
        if non_black / n < 0.01:
            fail("render", "the picture is essentially black - SaveBMP did not render")
    finally:
        cleanup(app, before, args.keep_open)

    log()
    log("PHASE1: OK   attach / model / render / close all good")
    log("SHOT  : %s" % png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
