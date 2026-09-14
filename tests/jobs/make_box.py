# -*- coding: utf-8 -*-
"""Reference job for the bridge: build a box, measure it, save it, render it.

Run it with:
    swbridge.py run --file tests/jobs/make_box.py --id p2sync

Everything here is injected by `swbridge run`: app, doc, session, L, E, cast,
getv, call, call_out, OUTARG, retry, OUT, HOME, log.

Written the way the skill tells an agent to write jobs:
  * reads go through getv(), actions through call().  pywin32 resolves some
    names to their VALUE on attribute access (`feature.GetTypeName2` is the
    string 'RefPlane', while `doc.GetTitle` is a method), so guessing produces
    "TypeError: 'str' object is not callable" with no hint of the cause;
  * the sketch plane is found by TYPE (GetTypeName2 == 'RefPlane'), never by
    name, so it works on a Chinese UI ('前视基准面') and an English one alike;
  * objects that may come back late-bound are cast() to their interface;
  * lengths are metres;
  * out-parameters are built with byref_i4().
"""

import os

from PIL import Image

# A document must exist before anything can be modelled into it.
if doc is None:
    log("no active document - creating a part")
    call(app, "NewPart")
d = cast(session.active(), "IModelDoc2")
log("document: %s (type %s)" % (getv(d, "GetTitle"), getv(d, "GetType")))


def first_ref_plane(document):
    # FirstFeature() and GetNextFeature() come back LATE-BOUND (the type library
    # gives no CLSID for them), and on a late-bound object pywin32 silently
    # resolves an unknown name with PROPERTYGET - which is why an uncast
    # `feature.GetTypeName2` is already a string.  Cast first, then read.
    feature = cast(call(document, "FirstFeature"), "IFeature")
    while feature is not None:
        if getv(feature, "GetTypeName2") == "RefPlane":
            return getv(feature, "Name")
        feature = cast(call(feature, "GetNextFeature"), "IFeature")
    return None


plane = first_ref_plane(d)
if plane is None:
    raise RuntimeError("no reference plane in the feature tree")
log("sketch plane (found by type, not by name): %r" % plane)

ext = cast(getv(d, "Extension"), "IModelDocExtension")
if not call(ext, "SelectByID2", plane, "PLANE", 0, 0, 0, False, 0, None, 0):
    raise RuntimeError("could not select the plane")

sm = cast(getv(d, "SketchManager"), "ISketchManager")
call(sm, "InsertSketch", True)
call(sm, "CreateCornerRectangle", 0.0, 0.0, 0.0, 0.05, 0.03, 0.0)   # 50 x 30 mm
log("sketch: 50 x 30 mm rectangle")

fm = cast(getv(d, "FeatureManager"), "IFeatureManager")
feature = call(
    fm, "FeatureExtrusion3",
    True, False, False, 0, 0, 0.020, 0.0,
    False, False, False, False, 0.0, 0.0,
    False, False, False, False, True, False, True,
    0, 0.0, False)                                                  # 20 mm deep
if feature is None:
    raise RuntimeError("FeatureExtrusion3 returned None")
feature = cast(feature, "IFeature")
log("feature: %r" % getv(feature, "Name"))

bodies = call(cast(d, "IPartDoc"), "GetBodies2", 0, False)
log("solid bodies: %d" % (0 if bodies is None else len(bodies)))

# ---- mass properties -------------------------------------------------------
# OUTARG marks the [in,out] slot; pywin32 appends those values to the return,
# so this really yields (values, status).
props, (status,) = call_out(ext, "GetMassProperties2", 1, OUTARG, False)
log("mass properties: status=%s values=%s" % (status, len(props) if props else 0))
if props:
    log("  raw = %s" % [round(float(v), 6) for v in props])

# ---- save -------------------------------------------------------------------
target = os.path.join(OUT, "box.SLDPRT")
saved = call(d, "SaveAs3", target, 0, 1)          # current version, silent
# SaveAs3 is declared VT_I4 in the type library: it returns an ERROR CODE, not a
# bool, and 0 means success.  (SaveAs4 is the variant that also reports Errors and
# Warnings as [in,out] parameters.)  Trust the file, not the truthiness.
log("save: %s -> %s (%s bytes)"
    % (saved, target, os.path.getsize(target) if os.path.isfile(target) else "missing"))

# ---- render -----------------------------------------------------------------
call(d, "ViewZoomtofit2")
call(d, "ShowNamedView2", "*Isometric", 7)
bmp = os.path.join(OUT, "box.bmp")
png = os.path.join(OUT, "box.png")
if not call(d, "SaveBMP", bmp, 1200, 800):
    raise RuntimeError("SaveBMP returned False")
Image.open(bmp).convert("RGB").save(png, "PNG")
os.remove(bmp)
log("render: %s" % png)

log("JOB OK")
