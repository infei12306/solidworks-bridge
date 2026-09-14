# -*- coding: utf-8 -*-
"""Recipe: an L bracket with 4 countersunk holes, checked against analytic values.

Geometry (as requested): 80 mm leg, 60 mm leg, 5 mm plate, four 6.5 mm through
holes with 90 degree countersinks (12 mm, the M6 flat-head pairing).
The width across the bend was NOT specified, so it is 40 mm - change WIDTH and
everything else follows.

Why this job is shaped the way it is:

  * The L profile is ONE closed polygon of six lines on the front plane, extruded
    across the bend.  Six lines are cheaper and far more predictable than nested
    rectangles, and a closed contour is what FeatureExtrusion3 needs.
  * The hole sketches go on the STANDARD PLANES, not on model faces.  The outer
    face of the horizontal leg is y=0 and of the vertical leg is x=0, which are
    exactly the Top and Right planes - so no face has to be selected by
    coordinate, which is the fragile part of this API.
  * Features are cut "both directions, through all" so the result cannot depend
    on guessing which way the cut normal points.
  * Countersinks are a 2.75 mm / 45 degree CHAMFER on the four rim edges: a
    chamfer on a circular edge IS a countersink (6.5 + 2 x 2.75 = 12 mm, 90
    degrees included).  Edges are picked with SelectByID2("", "EDGE", x, y, z),
    which is the documented way to select an edge by a point on it.
  * Everything is checked against arithmetic done up front, because "the API
    returned a feature" is not evidence that the solid is right.
"""

import math
import os
import struct

from PIL import Image

# --------------------------------------------------------------------- parameters
LEG_LONG = 80.0        # mm, horizontal leg
LEG_SHORT = 60.0       # mm, vertical leg
THICKNESS = 5.0        # mm plate
WIDTH = 40.0           # mm across the bend  <-- ASSUMED (not given in the request)
HOLE_DIA = 6.5         # mm through hole
CSK_DIA = 12.0         # mm countersink, 90 degrees (M6 flat head)
CSK_DEPTH = (CSK_DIA - HOLE_DIA) / 2.0        # 2.75 mm
HOLE_R = HOLE_DIA / 2.0
INSET_LONG = 20.0      # hole centre from each end of the long leg
INSET_SHORT = 15.0     # hole centre from each end of the short leg
MID = WIDTH / 2.0

SW_END_COND_BLIND = 0
SW_END_COND_THROUGH_ALL = 1
SW_START_SKETCH_PLANE = 0


def mm(value):
    """Millimetres -> the metres the API works in."""
    return value / 1000.0


# ------------------------------------------------------- the arithmetic, up front
# CARE: the countersink REPLACES the top CSK_DEPTH of the bore, it does not sit on
# top of it.  Counting a full-thickness bore plus a full cone frustum double-counts
# 2.75 mm of hole and lands 1.4 % high - which is exactly what happened first time
# round, and the check below reported the geometry as wrong when it was the
# arithmetic that was wrong.
PROFILE_AREA = LEG_LONG * THICKNESS + THICKNESS * (LEG_SHORT - THICKNESS)   # 675 mm^2
BORE_DEPTH = THICKNESS - CSK_DEPTH                                          # 2.25 mm
HOLE_VOL = math.pi * HOLE_R ** 2 * BORE_DEPTH                               # bore below the sink
CSK_VOL = (math.pi * CSK_DEPTH / 3.0
           * ((CSK_DIA / 2) ** 2 + (CSK_DIA / 2) * HOLE_R + HOLE_R ** 2))    # cone frustum
EXPECT_VOLUME = PROFILE_AREA * WIDTH - 4 * (HOLE_VOL + CSK_VOL)             # mm^3

PERIMETER = LEG_LONG + THICKNESS + (LEG_LONG - THICKNESS) + (LEG_SHORT - THICKNESS) \
    + THICKNESS + LEG_SHORT
BASE_AREA = 2 * PROFILE_AREA + PERIMETER * WIDTH
R_CSK = CSK_DIA / 2.0
SLANT = math.hypot(R_CSK - HOLE_R, CSK_DEPTH)
AREA_DELTA = (-math.pi * R_CSK ** 2                      # face loses the countersink disc
              - math.pi * HOLE_R ** 2                    # far face loses the bore
              + 2 * math.pi * HOLE_R * BORE_DEPTH        # bore wall BELOW the sink
              + math.pi * (R_CSK + HOLE_R) * SLANT)      # countersink cone
EXPECT_AREA = BASE_AREA + 4 * AREA_DELTA

log("analytic: profile %.1f mm^2, volume %.1f mm^3, area %.1f mm^2"
    % (PROFILE_AREA, EXPECT_VOLUME, EXPECT_AREA))

# ------------------------------------------------------------------------ helpers
if doc is None:
    log("no active document - creating a part")
    call(app, "NewPart")
d = cast(session.active(), "IModelDoc2")
ext = cast(getv(d, "Extension"), "IModelDocExtension")
sm = cast(getv(d, "SketchManager"), "ISketchManager")
fm = cast(getv(d, "FeatureManager"), "IFeatureManager")


PLANES, ALL_PLANES = reference_planes(d)
log("reference planes on this install: %s" % ALL_PLANES)
log("using front=%r top=%r right=%r" % (PLANES["front"], PLANES["top"], PLANES["right"]))
if not all(PLANES.values()):
    raise RuntimeError("could not identify front/top/right among %s" % ALL_PLANES)


def new_sketch(plane_name):
    # A FAILED feature leaves its sketch OPEN, and InsertSketch is a TOGGLE - so
    # the next call would close the open sketch instead of starting a new one, and
    # every retry after the first would be testing nothing.  Exit first, always.
    if getv(sm, "ActiveSketch") is not None:
        call(sm, "InsertSketch", True)          # toggles out of the open sketch
        log("  (exited a sketch left open by a failed feature)")
    call(d, "ClearSelection2", True)
    if not call(ext, "SelectByID2", plane_name, "PLANE", 0, 0, 0, False, 0, None, 0):
        raise RuntimeError("could not select plane %r" % plane_name)
    call(sm, "InsertSketch", True)


def cut_both_ways(label):
    """Through-all in BOTH directions, so the cut cannot miss for want of a sign."""
    feature = call(
        fm, "FeatureCut4",
        False, False, False,                     # Sd=False: both directions
        SW_END_COND_THROUGH_ALL, SW_END_COND_THROUGH_ALL,
        0.0, 0.0,                                # D1, D2 unused for through-all
        False, False, False, False, 0.0, 0.0,    # drafts
        False, False, False, False,              # offset/translate surface
        False,                                   # NormalCut
        False, True,                             # UseFeatScope, UseAutoSelect
        False, False, False,                     # assembly scope flags
        SW_START_SKETCH_PLANE, 0.0, False,       # start condition
        True)                                    # OptimizeGeometry
    if feature is None:
        raise RuntimeError("the %s cut returned no feature" % label)
    log("cut %-12s -> %r" % (label, getv(cast(feature, "IFeature"), "Name")))


# ------------------------------------------------- 1. the L, extruded across it
new_sketch(PLANES["front"])
PROFILE = [(0.0, 0.0), (LEG_LONG, 0.0), (LEG_LONG, THICKNESS),
           (THICKNESS, THICKNESS), (THICKNESS, LEG_SHORT), (0.0, LEG_SHORT)]
for index in range(len(PROFILE)):
    x1, y1 = PROFILE[index]
    x2, y2 = PROFILE[(index + 1) % len(PROFILE)]
    call(sm, "CreateLine", mm(x1), mm(y1), 0.0, mm(x2), mm(y2), 0.0)
log("profile: %d closed lines on %r" % (len(PROFILE), PLANES["front"]))

boss = call(fm, "FeatureExtrusion3",
            True, False, False, SW_END_COND_BLIND, SW_END_COND_BLIND,
            mm(WIDTH), 0.0, False, False, False, False, 0.0, 0.0,
            False, False, False, False, True, False, True,
            SW_START_SKETCH_PLANE, 0.0, False)
if boss is None:
    raise RuntimeError("FeatureExtrusion3 returned None")
log("extrude        -> %r (%g mm across the bend)"
    % (getv(cast(boss, "IFeature"), "Name"), WIDTH))

# ------------------------------- 2. two through holes in the horizontal leg
# The horizontal leg is 0 <= y <= THICKNESS, so its OUTER face is y = 0, which is
# the Top plane.  Sketching there needs no face selection at all.
new_sketch(PLANES["top"])
LONG_HOLES = [INSET_LONG, LEG_LONG - INSET_LONG]
for x in LONG_HOLES:
    # CreateCircleByRadius takes the SKETCH-LOCAL (u, v); the third argument is
    # ignored.  The top plane's local v axis points along -Z here, so +Z in the
    # model means -MID in the sketch.  Both wrong guesses are recorded in
    # REFERENCE.md - the check below is what makes this safe either way.
    call(sm, "CreateCircleByRadius", mm(x), mm(-MID), 0.0, mm(HOLE_R))
cut_both_ways("long-leg")

# ------------------------------- 3. two through holes in the vertical leg
# The vertical leg is 0 <= x <= THICKNESS, so its outer face is x = 0 = Right plane.
new_sketch(PLANES["right"])
SHORT_HOLES = [THICKNESS + INSET_SHORT, LEG_SHORT - INSET_SHORT]
for y in SHORT_HOLES:
    # Same story on the right plane (x = 0): the local axes are (Z, Y) and the u
    # axis also points along -Z, so +Z in the model is -MID in the sketch.
    call(sm, "CreateCircleByRadius", mm(-MID), mm(y), 0.0, mm(HOLE_R))
cut_both_ways("short-leg")

# ---------------------------- 3b. CHECK the holes landed where intended
# The sketch-coordinate convention above is an inference from measurement, so it
# is verified against the body's own geometry instead of being trusted.  This is
# the check that would have caught the first attempt immediately.
body = cast(call(cast(d, "IPartDoc"), "GetBodies2", 0, False)[0], "IBody2")


def hole_axes(a_body):
    axes = []
    for raw_face in call(a_body, "GetFaces") or []:
        face = cast(raw_face, "IFace2")
        surface = cast(call(face, "GetSurface"), "ISurface")
        if call(surface, "IsCylinder"):
            p = getv(surface, "CylinderParams")
            axes.append((round(p[0] * 1000, 2), round(p[1] * 1000, 2),
                         round(p[2] * 1000, 2), round(p[6] * 1000, 3)))
    return sorted(axes)


FOUND = hole_axes(body)
WANTED = sorted([(x, 0.0, MID, HOLE_R) for x in LONG_HOLES]
                + [(0.0, y, MID, HOLE_R) for y in SHORT_HOLES])
for entry in FOUND:
    log("hole axis at %s mm" % (entry,))
if len(FOUND) != 4:
    raise RuntimeError("expected 4 hole cylinders, found %d: %s" % (len(FOUND), FOUND))
for got, want in zip(FOUND, WANTED):
    if any(abs(a - b) > 0.01 for a, b in zip(got, want)):
        raise RuntimeError("hole at %s, expected %s" % (got, want))
log("hole positions verified against the intended layout")

# ---------------------------------------------- 4. countersinks on the outer rims
# The rims are found from the BODY's own geometry and selected by identity.
# Reason: SelectByID2("", "EDGE", x, y, z) is a hit test in the current view - it
# picked 3 of these 4 and silently failed on the one facing away from the camera,
# and a failed pick is invisible until a feature comes out the wrong shape.
def circular_edges(a_body, radius_mm, tol=0.05):
    found = []
    for raw_edge in call(a_body, "GetEdges") or []:
        edge = cast(raw_edge, "IEdge")
        curve = cast(call(edge, "GetCurve"), "ICurve")
        if not call(curve, "IsCircle"):
            continue
        p = [float(v) for v in getv(curve, "CircleParams")]
        centre = (round(p[0] * 1000, 2), round(p[1] * 1000, 2), round(p[2] * 1000, 2))
        if abs(round(p[6] * 1000, 3) - radius_mm) <= tol:
            found.append((edge, centre))
    return found


call(d, "ClearSelection2", True)
RIMS = [centre for _edge, centre in circular_edges(body, HOLE_R)
        if abs(centre[0]) < 0.01 or abs(centre[1]) < 0.01]
log("outer rims found from the body: %s" % (RIMS,))
if len(RIMS) != 4:
    raise RuntimeError("expected 4 outer rims, found %d" % len(RIMS))

# Strategy 1: chamfer the four rim edges.  The legacy 3-argument
# IModelDoc2.FeatureChamfer returned None; the modern 8-argument
# IFeatureManager.InsertFeatureChamfer is the entry point that works.  A chamfer
# on a circular rim IS a countersink: 6.5 + 2 x 2.75 = 12 mm, 90 deg included.
for edge, centre in circular_edges(body, HOLE_R):
    if abs(centre[0]) > 0.01 and abs(centre[1]) > 0.01:
        continue                    # an inner rim (x = 5 or y = 5), not a face rim
    if not call(cast(edge, "IEntity"), "Select2", True, 0):
        log("  WARN could not select the rim at %s" % (centre,))
chamfer = call(fm, "InsertFeatureChamfer",
               0, 1,                                   # Options, swChamferAngleDistance
               mm(CSK_DEPTH), math.radians(45.0),      # Width, Angle
               0.0, 0.0, 0.0, 0.0)                      # other distances unused
if chamfer is not None:
    log("countersinks   -> %r (%.2f mm x 45 deg on the 4 outer rims)"
        % (getv(cast(chamfer, "IFeature"), "Name"), CSK_DEPTH))
    RIMS = []                        # done - skip the fallback below
else:
    log("InsertFeatureChamfer returned None - falling back to drafted cuts")

# IModelDoc2.FeatureChamfer(Width, Angle, Flip) returned None with all four rims
# selected, so the countersink is made the long way round instead: a BLIND CUT of
# a 12 mm circle, 2.75 mm deep, with a 45 degree draft that closes it down onto
# the 6.5 mm bore - which is what a 90 degree countersink is.  Neither the draft
# sign nor the cut direction is documented, so each rim is tried with both and the
# volume reconciliation at the end is what proves the result (a wrong sign makes a
# funnel instead of a countersink and misses the analytic volume by ~50%).
for (cx, cy, cz) in RIMS:
    on_top = abs(cy) < 0.01
    plane = PLANES["top"] if on_top else PLANES["right"]
    # sketch-local centre: top plane is (X, -Z), right plane is (-Z, Y)
    local = (cx, -cz) if on_top else (-cz, cy)
    made = None
    for sign, flip in ((1.0, False), (-1.0, False), (1.0, True), (-1.0, True)):
        new_sketch(plane)
        call(sm, "CreateCircleByRadius", mm(local[0]), mm(local[1]), 0.0, mm(CSK_DIA / 2))
        made = call(fm, "FeatureCut4",
                    True, flip, False,                     # single ended, flip?, dir
                    SW_END_COND_BLIND, SW_END_COND_BLIND,
                    mm(CSK_DEPTH), 0.0,                    # D1 = 2.75 mm
                    True, False,                           # Dchk1: use draft, Dchk2
                    False, False,                          # Ddir1, Ddir2
                    sign * math.radians(45.0), 0.0,        # Dang1 = 45 degrees
                    False, False, False, False,
                    False, False, True,                    # UseAutoSelect
                    False, False, False,
                    SW_START_SKETCH_PLANE, 0.0, False, True)
        if made is not None:
            log("countersink at (%g, %g, %g) -> %r  (draft %+.0f deg, flip=%s)"
                % (cx, cy, cz, getv(cast(made, "IFeature"), "Name"),
                   sign * 45.0, flip))
            break
    if made is None:
        raise RuntimeError("countersink at (%g, %g, %g) failed for every sign/flip"
                           % (cx, cy, cz))

# ============================================================== verification
props, (mass_status,) = call_out(ext, "GetMassProperties2", 1, OUTARG, False)
volume_mm3 = float(props[3]) * 1e9
area_mm2 = float(props[4]) * 1e6
mass_kg = float(props[5])
log("")
log("volume : %10.2f mm^3   analytic %10.2f   error %+.4f%%"
    % (volume_mm3, EXPECT_VOLUME, 100.0 * (volume_mm3 - EXPECT_VOLUME) / EXPECT_VOLUME))
log("area   : %10.2f mm^2   analytic %10.2f   error %+.4f%%"
    % (area_mm2, EXPECT_AREA, 100.0 * (area_mm2 - EXPECT_AREA) / EXPECT_AREA))
log("mass   : %10.5f kg     (status=%s, centroid %.1f, %.1f, %.1f mm)"
    % (mass_kg, mass_status, float(props[0]) * 1000, float(props[1]) * 1000,
       float(props[2]) * 1000))

bodies = call(cast(d, "IPartDoc"), "GetBodies2", 0, False)
log("bodies : %d" % (0 if bodies is None else len(bodies)))
body = cast(bodies[0], "IBody2")
try:
    box = call(body, "GetBodyBox")
    log("bbox   : %.2f x %.2f x %.2f mm  (expected %g x %g x %g)"
        % ((box[3] - box[0]) * 1000, (box[4] - box[1]) * 1000, (box[5] - box[2]) * 1000,
           LEG_LONG, LEG_SHORT, WIDTH))
except Exception as exc:  # noqa: BLE001 - GetBodyBox is a bonus check
    log("bbox   : unavailable (%s)" % exc)

volume_error = abs(volume_mm3 - EXPECT_VOLUME) / EXPECT_VOLUME
area_error = abs(area_mm2 - EXPECT_AREA) / EXPECT_AREA
if volume_error > 0.002:
    raise RuntimeError("volume is %.4f%% off the analytic value - geometry is wrong"
                       % (100 * volume_error))
if area_error > 0.01:
    raise RuntimeError("surface area is %.4f%% off - a hole is probably misplaced"
                       % (100 * area_error))

# --------------------------------------------------- exports, then a look at it
for suffix in (".STEP", ".STL"):
    target = os.path.join(OUT, "bracket" + suffix)
    code = call(d, "SaveAs3", target, 0, 1)          # version 0, Silent
    log("export %-6s -> code %s, %s bytes" % (suffix, code,
                                              os.path.getsize(target)
                                              if os.path.isfile(target) else -1))
stl = os.path.join(OUT, "bracket.STL")
with open(stl, "rb") as handle:
    head = handle.read(84)
triangles = struct.unpack("<I", head[80:84])[0]
size = os.path.getsize(stl)
log("STL    : %d triangles, %d bytes, %s"
    % (triangles, size, "84+50n consistent" if 84 + 50 * triangles == size
       else "INCONSISTENT"))

call(d, "ViewZoomtofit2")
for name, view_id, label in (("*Isometric", 7, "iso"), ("*Dimetric", 8, "dimetric")):
    call(d, "ShowNamedView2", name, view_id)
    bmp = os.path.join(OUT, "bracket_%s.bmp" % label)
    png = os.path.join(OUT, "bracket_%s.png" % label)
    if call(d, "SaveBMP", bmp, 1400, 1000):
        Image.open(bmp).convert("RGB").save(png, "PNG")
        os.remove(bmp)
        log("render : %s" % png)

log("BRACKET OK")
