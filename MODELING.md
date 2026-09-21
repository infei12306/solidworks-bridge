# Modelling with the bridge - what works, what does not, and the traps

Everything here was measured on SOLIDWORKS 2025 SP5 (33.5.0.0053), Chinese UI,
through this bridge. Nothing is copied from documentation that was not also
verified against the live session.

## Status of the parts that have been built

| Part | State |
|---|---|
| `tests/jobs/make_box.py` - 50x30x20 mm block | **Complete.** Volume, area, centroid, STL triangle count and render all reconcile. |
| `tests/jobs/make_bracket.py` - L bracket, 80 + 60 legs, 5 mm plate, 40 mm wide, four 6.5 mm countersunk holes | **Complete.** Volume and surface area match the analytic values to **0.0000 %**, all four hole axes verified from the body, envelope exactly 80 x 60 x 40 mm, STEP + STL + two renders produced. |
| 128-body Arduino MEGA 2560 Rev3, built from the vendor's own EAGLE board file (`D:\桌面文件\车架复刻交付\arduino-mega2560\build_mega.py`) | **Complete.** Board outline area, board volume, whole-part volume, envelope and all six mounting-hole axes match analytic values exactly; **102 header pins pulled straight from the EAGLE pads, one solid each**; 126 footprints checked pairwise for overlap; STEP carries 128 solids; STL is watertight. See [§ Second part](#second-part-a-128-body-board-from-a-vendor-cad-file) and [§ Fourth part](#fourth-part-per-pin-bodies-out-of-pad-data). |
| 93-body 16-channel 12 V relay board, dimensions triangulated from a flat vendor photo because no vendor CAD exists (`D:\桌面文件\车架复刻交付\relay-board\build_relay_board.py`) | **Complete for the modelled scope.** PCB volume and total volume match analytic to **0.0000 %**, 93 bodies, envelope exactly 179 x 90 x 16.6 mm, **70 individually-bodied pins asserted** (48 relay outputs + 20 input header + 2 power poles), all 92 boxes checked pairwise for overlap (4186 pairs), 4/4 mounting-hole rims found on the body, STEP carries 93 solids, STL watertight (2100 triangles, 0 open edges). See [§ Third part](#third-part-a-93-body-relay-board-where-the-input-had-to-be-reconstructed). |
| API-semantics probe for the LM2596 module: 30 x 20 x 1.6 mm plate + ten 2.04 mm posts (`D:\桌面文件\workspace\lm2596-voltmeter\probe_offset_boss.py`) | **Complete, 12.7 s, zero failures.** Settles the blind/extrude end condition, the six-argument rectangle, where `SetAddToDB` lives, the `GetBodyBox` field order, the `close_document` signature and the `SaveAs3` name collision. Measured volume equals the analytic PCB + posts sum exactly (1313.7360 mm3), envelope exactly 30 x 20 x 10.1 mm, 11 bodies, STEP 11 solids, STL watertight (132 triangles, 0 open edges, volume agreement -0.00001 %). See [§ Fifth part](#fifth-part-a-probe-that-cost-12-seconds-instead-of-a-269-second-mistake). |

The bracket job asserts at every step, so a wrong intermediate state fails loudly
instead of leaving a part that merely looks finished. It took four failed routes
and one arithmetic bug of my own to get there - all of them are recorded below.

## The recipe that works

1. **Parameters in millimetres at the top**, converted at the call with `mm()`.
2. **Compute the analytic answer first** - profile area, volume, surface area.
   These become assertions, not decoration.
3. **Build**: one closed profile of `CreateLine` on a reference plane, extruded;
   then cut features whose sketches go on the **standard planes**, chosen because
   they coincide with the outer faces (see trap 1).
4. **Verify the build from the solid itself**, before anything else:
   * volume and surface area against the analytic values (the bracket matches to
     < 0.01 %, and a missing countersink is a 6 % error - impossible to miss);
   * every hole's axis position and radius, read from the body's cylindrical
     faces, against the intended layout;
   * `GetBodyBox()` against the intended envelope (80 x 60 x 40 mm).
5. **Export and look**: STEP + STL (`84 + 50*n == size` is a real check), then
   `SaveBMP` render for a visual pass.

The ordering matters. Step 4 is what turns a wrong guess into a located failure
instead of a plausible-looking wrong part.

## Traps, in the order they bit

### 1. `CreateCircleByRadius` takes SKETCH-LOCAL coordinates, and the third is ignored

```
CreateCircleByRadius(XC, YC, Zc, Radius)   ->  the entity lands at sketch (XC, YC)
```

The third argument does not do what its name suggests. Measured on this build:

| plane | sketch-local axes | to place model `(x, 0, z)` on the top plane |
|---|---|---|
| front (z = 0) | `u = X`, `v = Y` | pass `(x, y)` |
| top (y = 0) | `u = X`, `v = -Z` | pass `(x, -z)` |
| right (x = 0) | `u = -Z`, `v = Y` | pass `(-z, y)` |

Two wrong guesses were needed to find this: passing `(x, 0, z)` put every hole on
the `z = 0` edge, and passing `(x, z, 0)` made the cut return no feature at all
(the circles landed outside the material, at negative Z).

**Did not trust it**: the job reads the hole axes back off the body and asserts
them. That check is what finally confirmed the positions, and it is what makes the
convention safe to use. To avoid the convention entirely, query the active
sketch's `ModelToSketchTransform` and transform the model point into (u, v).

### 2. Every feature-tree navigation call returns a LATE-BOUND object

`FirstFeature()` and `GetNextFeature()` carry no CLSID, so `feature.GetTypeName2`
is silently resolved to its VALUE - it is already a string, and calling it raises
`TypeError: 'str' object is not callable`. Cast, every time. This is why
`swcore.iter_features()` and `swcore.reference_planes()` exist: the trap is
absorbed once instead of re-learned in every job. (It was re-learned in this job
anyway, on the first run.)

### 3. `SelectByID2("", "EDGE", x, y, z)` is a hit test in the CURRENT VIEW

It selected 3 of the 4 hole rims and silently failed on the one facing away from
the camera. A failed pick is invisible until a feature comes out the wrong shape.

**Do this instead** - find the entity from the body's geometry and select it by
identity:

```python
for raw in call(body, "GetEdges"):
    edge = cast(raw, "IEdge")
    curve = cast(call(edge, "GetCurve"), "ICurve")
    if call(curve, "IsCircle"):
        p = getv(curve, "CircleParams")     # [centre xyz, axis xyz, radius]
        ...
        call(cast(edge, "IEntity"), "Select2", True, 0)
```

`IEdge` does **not** redeclare the inherited `IEntity` members, so `edge.Select2`
raises `AttributeError`; cast to `IEntity` over the same pointer and it works.

### 4. A feature that was created can still be wrong, and a failed one returns `None`

`FeatureExtrusion3`, `FeatureCut4` and friends return `None` on failure - and
nothing else. They do not raise, and SW does not necessarily show a dialog. So
every feature call needs a `None` check plus a geometric check.

### 5. Rectangles and circles are not interchangeable as profiles

Not a failure, but worth recording: `CreateCornerRectangle` is `(X1, Y1, Z1, X2,
Y2, Z2)` and takes the same sketch-local convention, so a rectangle drawn on the
front plane has its Z arguments ignored too.

## Countersinks: four routes, one that works

| route | call | result |
|---|---|---|
| legacy chamfer on the rim edges | `IModelDoc2.FeatureChamfer(0.00275, radians(45), False)` with all 4 rims selected | `None` |
| drafted blind cut | `FeatureCut4`, `Dchk1=True`, `Dang1=±45deg`, `D1=2.75 mm`, Ø12 circle on the outer face - both draft signs x both `Flip` values | `None` for all four combinations |
| **modern chamfer** | **`IFeatureManager.InsertFeatureChamfer(0, swChamferAngleDistance, 0.00275, radians(45), 0, 0, 0, 0)`** with the 4 rims selected | **works** |
| Hole Wizard | `HoleWizard5(GenericHoleType, ..., swWzdCounterSink=1, ...)` - 27 arguments | not needed |

The winning feature measured back off the body: four cone faces, each
`radius = 0.006 m` (Ø12), `half-angle = 0.7854 rad` (90 degrees included),
2.75 mm deep, cone lateral area 113.02 mm² against 113.01 mm² analytic, and the
remaining bore wall 45.95 mm² - exactly 2.25 mm of it, because the countersink
replaces the top 2.75 mm rather than sitting on top of it.

Two further lessons came out of this:

* **A failed feature leaves its sketch OPEN, and `InsertSketch` is a toggle.** So
  the next `InsertSketch` closes the open sketch instead of starting a new one,
  and every retry after the first is testing nothing. Three of the four drafted-cut
  attempts were wasted that way; the job now exits any active sketch before
  starting a new one, and says so in the log.
* **The reconciliation caught my arithmetic, not the geometry.** The first
  successful build was reported as wrong (+1.43 % volume, −1.75 % area) because
  the expectation counted a full-thickness bore *plus* a full cone frustum, double
  counting the 2.75 mm the countersink replaces. Fixing the expectation moved both
  errors to **0.0000 %**. A check that can only ever blame the model is a check
  that will eventually blame the wrong thing.

## What this says about the achievable modelling level

* Solids from a parametric profile plus cuts and holes: **demonstrated**, with
  analytic reconciliation.
* Feature positions from standard planes: **demonstrated**, but the coordinate
  convention has to be verified rather than assumed (trap 1).
* Selection of existing geometry (edges/faces) for a subsequent feature:
  **achievable via geometry lookup** (trap 3), not via coordinates.
* Chamfers on circular rims (i.e. countersinks): **demonstrated**, via
  `InsertFeatureChamfer`, reconciled to 0.0000 %.
* **Countersunk holes in a plate: demonstrated end to end** - build, verify
  against analytic volume and area, verify hole axes from the body, export STEP +
  STL, render both views, and the render agrees (4 countersunk holes, 2 per face).
* Fillets, Hole Wizard, patterns, revolves, lofts: **not yet demonstrated**. The
  failures above show that "the API exists" is not the same as "the call works",
  and that the legacy 3-argument entry points are not reliable - each one needs
  its own verified recipe.
* The real ceiling is not the API surface (268 methods on `IFeatureManager`
  alone) but (a) targeting existing geometry, (b) knowing the design-intent
  recipe, and (c) proving the result without a human looking at it. (c) is
  largely solved by the checks in `make_bracket.py`; (a) has a working pattern
  now; (b) is where each new part type costs real work.

## Second part: a 128-body board from a vendor CAD file

The Arduino MEGA 2560 Rev3 (built in `D:\桌面文件\车架复刻交付\arduino-mega2560\`,
result: 101.6 x 53.34 mm board, 24 lumped components + 102 individual header pins +
the reset button = 128 separate bodies). Unlike the box
and the bracket, the geometry was **not invented** - it was converted from the
vendor's own EAGLE board file, which changes the shape of the whole job: every
coordinate is given, so the entire risk moves from "is the design right" to "did
the API do what I asked". Nine more traps came out of it; the header pins were
split out later, see [§ Fourth part](#fourth-part-per-pin-bodies-out-of-pad-data).

### 19. The active document is the user's document, and `doc is None` does not protect you

`doc` (and `session.active()`) is whatever the user last had in front of them. A job
that began `if doc is None: NewPart` wrote a sketch **into the user's open assembly**
and left it there - the assembly then had a stray `ProfileFeature` in its tree.

```python
before = set(session.titles())
made = call(app, "NewPart")
d = cast(getv(app, "ActiveDoc"), "IModelDoc2")
if made is None or d is None or int(getv(d, "GetType")) != 1:   # 1 = swDocPART
    raise RuntimeError("NewPart did not give me a part")
log("documents this job opened: %s" % sorted(set(session.titles()) - before))
```

The mess is recoverable - walk the tree, confirm the stray feature is the only
`ProfileFeature` at assembly level, `cast(f,"IFeature").Select2(False,0)` then
`EditDelete` - but `InsertSketch` is a toggle, so an *open* sketch has to be exited
first (trap 17), which commits it as a feature. Cleaning up is strictly worse than
checking.

### 20. `SetAddToDB(True)` or the sketch is silently mangled

`IModelDoc2::SetAddToDB(True)` before creating sketch entities, `False` before the
feature call. Without it SOLIDWORKS' automatic relation inference **deletes arcs and
merges chains of entities**: a 9-line + 3-arc board outline came out as **9 straight
lines, 0 arcs**, and the profile area was 48.9 mm² wrong. The signature is
`GetArcCount() == 0` while `GetSketchContourCount() == 1` and the part still builds.

Always dump what you actually made:

```python
sk = cast(getv(sm, "ActiveSketch"), "ISketch")
log("lines=%s arcs=%s contours=%s" % (call(sk, "GetLineCount"),
    call(sk, "GetArcCount"), call(sk, "GetSketchContourCount")))
```
`ISketch::GetLines2(0)` also returns the line endpoints (8 doubles per entity, the
real ones interleaved with junk - read the ones that look like your coordinates).

### 21. `CreateArc(..., Direction)`: True is counter-clockwise, and the wrong way is a 270° arc

```
ISketchManager::CreateArc(XC,YC,Zc, X1,Y1,Z1, X2,Y2,Z2, Direction)
```
Sketch-local coordinates (the Z arguments are ignored, as everywhere else).
`Direction=True` walks CCW; if the short arc you want runs CW, SOLIDWORKS takes the
**major arc** instead. Decide per arc from the sweep of the shorter one:

```python
a0 = math.atan2(s[1]-c[1], s[0]-c[0]);  a1 = math.atan2(e[1]-c[1], e[0]-c[0])
sweep = a1 - a0                      # normalise into (-pi, pi]
forward = sweep > 0
```

A 20x20 square with one r = 2 corner, extruded 1 mm and measured, settles it:
chord 398.0000 mm², **convex rounded corner 399.1416 mm²** (the short sweep),
the other direction 386.5752 mm² (self-intersecting). `399.1416 = 400 - r^2(1 - pi/4)`,
which is the formula to check against.

**And derive the centre, do not guess it.** For an EAGLE `curve=-90` segment solve for
the candidate that makes the sweep clockwise. Guessing put the top-left corner's
centre on the corner point itself ((0, 53.34)) instead of 1 mm inside it
((1, 52.34)) - a notch instead of a rounded corner - and cost 0.5708 mm², i.e.
exactly two corner segments. A rounded corner's centre is tangent to *both* adjoining
edges; if the centre sits on the corner, it is not a rounded corner. And when a
number is exactly two units of some geometric feature, believe it: the eight possible
arc-direction combinations for that outline gave 5366.6850 / 5369.8266 / 5372.9682 /
5376.1098 mm² and **none** of them was the analytic 5373.5390 - which is the proof
that the *outline definition*, not the arc flags, was wrong.

### 22. Starting a boss away from the sketch plane

* `Flip` does **nothing** for a blind boss on the front plane: `Flip=True` and
  `Flip=False` both extruded `0 -> +depth` (measured with `GetBodyBox`).
* The third boolean is *both directions*, not a flip: `Dir=True` gave `-d .. +d`.
* A **negative depth returns no feature at all** (`None`).
* The only route that works is the start condition:
  `FeatureExtrusion3(..., T0=3 /*swStartOffset*/, StartOffset=mm(start), FlipStartOffset=False)`.
  Verified: `StartOffset=1.6, D1=10` -> a body spanning exactly `z = 1.6 .. 11.6`.

So "board from z = 0 to 1.6, every component from z = 1.6 upwards" needs one offset
extrusion per component and no reference plane at all. `swStartOffset` is not in the
stub enums; the raw values are `0 = sketch plane`, `3 = offset`.

### 23. Cuts: make them direction-proof

`FeatureCut4(Sd=False, T1=1, T2=1, ...)` = through-all in **both** directions. It
cannot miss for want of a sign. `Sd=True, Flip=True` with a blind depth returned
`None` on this build for a through hole in a 1.6 mm plate.

### 24. One sketch with many contours can fail where each one succeeds

A single sketch holding all 10 header rectangles made `FeatureExtrusion3` return
`None`, every time; the identical 10 rectangles each in their own sketch+feature all
built. A 9-rectangle group at another height *did* work, so there is no tidy contour
limit - treat `None` from a multi-contour sketch as this and split it. Per-component
features cost more calls and buy a multi-body part, which is usually what the
downstream assembly wants anyway.

### 25. `merge=False` (multi-body) is also the only way to a watertight STL

Merged, the board's top face carries ~40 inner loops (one per component footprint
plus the six holes) and SOLIDWORKS' tessellator leaves it open: **641 boundary edges,
all on z = 1.6**, mesh volume 16 539.875 mm³ against the solid's 18 653.646 (-11 %),
surface area 13 122 mm² against 17 086 mm². Doc-level `swSTLQuality = Fine`
(preference 78) had **no effect at all** - byte-identical output.

Multi-body: **0 open boundary edges**, 1 816 triangles, 18 688.460 mm³ against
18 688.228 (+0.0012 %, the usual inscribed-facet deficit). Edge-use histogram is the
test: every edge of a closed mesh is used exactly twice, and `3n` must be even
(1 617 triangles cannot be closed - that parity alone flags it before any geometry).

Multi-body changes the volume bookkeeping, and the check will tell you: a button cap
sitting inside a switch body contributes its **whole** cylinder (44.11 mm³) instead of
the 9.65 mm³ that stuck out of a merged model - exactly the +34.58 mm³ the assertion
reported.

### 26. Clear the selection before every export

A freshly created feature stays selected, and `SaveAs3` exports **only what is
selected**. Two exports in a row silently wrote one body: STL 200 triangles / STEP
11 807 bytes instead of 36 bodies / 435 995 bytes, with no error code.

```python
call(d, "ClearSelection2", True)          # immediately before the export loop
```
Verify the STEP by parsing it, not by its size:
`step_blob.count(b"MANIFOLD_SOLID_BREP") == len(bodies)`.

### 27. `SaveAs3` re-points the document

After `SaveAs3(part.STL)` the open document *is* the STL - a later save would write a
part file over the mesh. Re-save the part at the end of the export chain. And if the
target file is already open in the session, `SaveAs3` returns **1**, not 0: close that
document first (match by exact title) so the job stays re-runnable.

### 28. Renders: the numeric view id beats the name

`ShowNamedView2("*Top", 7)` is the **isometric** view. Passing 7 for every view
produced three byte-identical PNGs while the log claimed three different views.
`swFrontView=1`, `swLeftView=3`, `swRightView=4`, `swTopView=5`, `swDimetricView=6`,
`swIsometricView=7`, `swTrimetricView=8` (from the generated stubs: `enum View`).
Hash the outputs to prove they differ. And do not try to threshold a `SaveBMP`
render against its background to measure the model - the background is a gradient,
not flat white; rasterise the **STL** instead if you want a top view you can trust.

### What this part proves

* Vendor CAD (EAGLE `.brd`) -> a table -> a real solid is a **30-line parser plus this
  job**; every coordinate is machine-transcribed, and the doc can be generated from
  the same table so it cannot drift.
* Offsets, multi-body, through-all cuts and 36 bodies in one part: **demonstrated**,
  with volume/area/envelope/hole-axis/STEP/STL reconciliation.
* Still not demonstrated: fillets, revolves, lofts, patterns, assemblies, drawings.

## Third part: a 93-body relay board where the *input* had to be reconstructed

Same recipe, different failure mode - here the vendor CAD did not exist and the
dimensions had to be argued from four independent sources before a single feature
was built. Job: `D:\桌面文件\车架复刻交付\relay-board\build_relay_board.py`. Made in one
61-second run, first attempt, every assertion at 0.0000 % - because the geometry was
settled *before* SOLIDWORKS was opened.

The one thing that had to be redone twice is instructive. The first build *assumed*
eight channels spanned most of the 179 mm board and got a 19.25 mm pitch. The second
measured the silk dividers on the user's own photo (median 247.8 px) and got 18.19 mm.
The third measured the *seller's* flat product photo and got 17.24 mm - and that one is
right, because it is the only measurement whose scale is pinned by two independent
anchors that agree: the relay body is 59 px and 15.41 mm (known from the vendor STEP),
the screw pitch is 19.4 px and 5.08 mm (standard part), so the scale is 0.2612 and
0.2619 mm/px - 0.3 % apart - and 675 px x 0.2635 lands on 177.9 mm against a stated
179 mm. The user's tilted photo gave 18.19 mm, 5.5 % high, purely from perspective.

**A flat, unobstructed photo of the same product beats a tilted photo of the actual
object, and the way to know it is flat is to pin it with two known sizes.** Ask for the
seller's product shots before trusting your own perspective maths; the seller's photo
also happened to be the only view that showed the control end at all (the real board
runs 11.6 mm off the right edge of frame). Note the reference was *mirrored* relative
to the model - control end on the left - so every X came out as `179 - X_ref`.

### 31. `OpenDoc6` refuses every neutral file on this build; `LoadFile4` takes them

The trap that cost the most, and the one worth remembering, because its symptom
points at the wrong culprit:

```
OpenDoc6(any .step, ...) -> doc=None, errors=2097152  in ~0.2 s
```

`2097152` is `swFileRequiresRepairError`, which reads like "your file is corrupt".
It is not. The same session exported a STEP itself and could not reopen it. The
test that settles it in one call is to round-trip your *own* export before blaming
the download:

```python
self_test = export_a_box_to_step()
OpenDoc6(self_test, ...)      # errors=2097152 again -> it is the route, not the file
```

`ISldWorks.LoadFile4(path, "", None, OUTARG)` opens it with `errors=0`. What makes
this one *stick* rather than just annoy is the second-order effect: `LoadFile4`
returns a single-solid STEP as an **assembly**, so `cast(doc, "IPartDoc")` raises
`com_error(-2147352562, 'invalid parameter count')` and the body count comes back
as nonsense. Take the component's part document instead:

```python
comp = as_list(cast(asm, "IAssemblyDoc").GetComponents(True))[0]
part = cast(cast(comp, "IComponent2").GetModelDoc2(), "IModelDoc2")
bodies = as_list(cast(part, "IPartDoc").GetBodies2(0, False))
call(part, "SaveAs3", out_sldprt, 0, E.swSaveAsOptions_Silent)   # now a native part
```

Measured on top of that: enabling `swImportCheckAndRepair`, disabling
`swImportAutoRunImportDiagnostics` and setting `swImportNeutralAssemblyStructureMapping`
to "multibody part" all changed **nothing**; 3D Interconnect was already off.

### 32. Decode the error code from the generated stubs instead of guessing

```
grep 2097152 %LOCALAPPDATA%\swbridge\stubs\_swconst_gen.py
2419: swFileCriticalDataRepairError =4194304  # from enum swFileLoadError_e
2421: swFileRequiresRepairError     =2097152  # from enum swFileLoadError_e
```

One grep named the constant *and* the enum, and turned a 40-line hunt into a
lookup. The generated stubs carry every enum member with its value and origin -
use them as the dictionary they are.

### 33. Two naming traps on `ISldWorks`, both hit inside one job

* `GetUserPreferenceInteger` / `SetUserPreferenceInteger` **do not exist**; the pair
  is `GetUserPreferenceIntegerValue` / `SetUserPreferenceIntegerValue`. The boolean
  pair really is `GetUserPreferenceToggle` / `SetUserPreferenceToggle`, so the
  symmetry you would assume is wrong in exactly one place.
* `getv()` takes a property **name**; a getter that takes an argument must go
  through `call()`:
  `getv(app, "GetUserPreferenceToggle", 691)` is a `TypeError`, `call(app, ...)`
  works. `getv` only covers property-or-zero-arg-method reads.
* `SetSaveFlag()` takes **no** arguments. It marks the document clean, which is the
  whole reason `CloseDoc` does not open a modal (rule 6).

### 34. When there is no vendor CAD, triangulate the dimensions and say so

No free model of this board exists anywhere (109 open-source repos, every CAD
library, two paid sites - all checked, all documented in the workspace README).
What replaced it was four sources that agree, each checkable:

| number | source | how it was checked |
|---|---|---|
| board 179 x 90 x 19 mm | the seller's product drawing | quoted, not derived |
| relay 19.00 x 15.41 mm | the *relay's* vendor STEP | regex the `CARTESIAN_POINT`s offline - no SOLIDWORKS needed |
| relay 19.2 x 15.4 mm | LCSC package name + Hongfa datasheet | two more independent statements |
| terminal pitch 5.08 mm | standard part | measured on both photos: 19.4 px on the flat one, 69.25 px on the tilted one |
| channel pitch 17.24 mm | relay columns on the flat seller photo | 65.4 px, scale pinned two ways (see above) |
| control parts | the seller's flat photo, mirrored | X = 179 - X_ref, rescaled into this board's strip |

The photo measurement is worth keeping because it went wrong twice and the fix was
to *stop asking a vision model to count*: three runs at "how many relays" gave 5, 7
and 8. Detecting the screw heads as luminance plateaus (three flat tops at ~250,
33 px wide) gave a number good to a pixel, and it is what showed the board runs off
the right edge of the frame. **Use the photo for ratios between known parts; never
ask a model to count things.**

The control end's *content* is now measured rather than guessed - the seller's photo
shows a 2x10 input header (two pin columns, 10 pairs, silk `IN1`-`IN16` then `DC-` /
`DC+` / `DC+`, and the body measures 25.1 mm = 9 x 2.54 + 2.54, which only a 2x10
fits), two DIP-18 line drivers (measured 22.6 mm, and a DIP-18 body is 22.86 mm), a
2-pole power block and two electrolytic cans. The build asserts 48 + 20 + 2 = 70
separate pin solids; see [§ Fourth part](#fourth-part-per-pin-bodies-out-of-pad-data)
for why they are built rather than cut.

The overlap assertion earned its keep: the first run with the new control layout
aborted on `control parts CTRL_OPTO_A and CTRL_PWR_TERM overlap` before writing
anything, because the mirrored positions from a 38.4 mm strip had been squeezed into
a 34.3 mm one. **Assert that separately-placed parts do not overlap; you will not see
it in a render until it is baked in.**

One residual that stays open and is written down rather than hidden: the relay is
modelled as its exact envelope box, not its imported solid, because the importer
returns an assembly and 16 copies of a 291-face body is the wrong trade for a
wiring-harness part. The imported native part is delivered alongside so the choice
is the user's, not silently made for them.

## Fourth part: per-pin bodies out of pad data

Both boards ended up wanting the same thing - **every connector position its own
solid**, so the harness can attach a connection point and a wire to each. The two
cases were nothing alike in effort, and the difference is the whole lesson.

### 35. Check whether the source already has a pad list before you measure anything

The MEGA's 102 header pins cost **zero measurement**: the EAGLE `.brd` carries every
pad's local `(x, y)` next to each element's placement and rotation, so absolute pin
coordinates fall out of the same `rot_pt()` helper that was already turning package
boxes into board coordinates. `gen_table.py` grew ~15 lines and now emits

```
COMPONENTS  : 24 lumped parts  (name, x0, y0, x1, y1, h)
HEADER_PINS : 102 pins         (element, pad, x, y, h)
```

The relay board is the contrast: no vendor CAD exists, so its 70 pin positions had
to be argued from a photo (5.08 mm and 2.54 mm are standard pitches, the footprints
were measured, the control end's pin *count* came off the seller's wiring diagram).
Same outcome, hours apart.

**Before estimating any pin position, look for a pad/pin table in the source.**
EAGLE pads, KiCad footprints, IDF/EMN, Gerber plus pick-and-place, and most vendor
STEP files all carry one; a data sheet's recommended PCB layout does too.

### 36. One body per pin: build the posts, do not cut the block

Asked whether a single extruded cut could split all the pins out at once: **yes, in
principle** - SOLIDWORKS splits one solid into several bodies when a cut severs the
material completely, so one sketch holding every separating groove plus one
`FeatureCut4` would do it. Two reasons it is the wrong route here:

1. **A through-all cut from the front plane also slits the PCB underneath.** It has
   to be a blind cut starting on the PCB's top face (`T0=3` + `StartOffset=1.6`),
   which is trap 22 again - and now you are betting on a cut direction instead of a
   boss direction.
2. **A cut really removes material**, exactly the groove volume, so the analytic
   total becomes "block volume minus grooves". Building the posts omits the same
   material while keeping the sum trivially exact.

Size the post as **pitch minus a gap** so neighbours cannot touch: `2.54 - 0.5 =
2.04` on 0.1 in headers, `5.08 - 0.6 = 4.48` on the relay terminals. Then move the
volume bookkeeping with it - the old block volume leaves `COMP_VOL` and the posts'
sum enters as `PIN_VOL`. Forget that step and the total-volume assertion fails by
exactly the difference, which is the assertion doing its job.

### 37. Run the overlap check over every solid, not just the fiddly ones

Once parts are placed independently by formula, a collision is invisible in a render
until it is baked in. The check is O(n²) over footprints and costs nothing: 4 186
pairs on the relay board's 92 boxes, 7 875 on the MEGA's 126. It has already paid
for itself - the relay board's first run with the new control layout aborted on
`CTRL_OPTO_A and CTRL_PWR_TERM overlap` before writing a single file.

Keep one documented exception rather than weakening the check: the MEGA's reset
actuator is a cylinder standing inside the switch body on purpose.

### 38. A generated document that hardcodes numbers will drift

`make_readme.py` had "36 个实体" and "34 个元件盒表" as literals in a template, plus
the previous workspace path. After the pin split it was wrong in six places while
still looking perfectly plausible. Derive every count and volume from the same table
the model is built from and pass it through a placeholder - the MEGA README now
computes its own body count (128), pin count (102) and volumes, so it cannot be
right about the geometry and wrong about the summary.

## Fifth part: a probe that cost 12 seconds instead of a 269-second mistake

Before modelling the LM2596 buck-converter module, the six API semantics this part
needs were measured on a 30 x 20 x 1.6 mm plate with ten 2.04 mm posts - a job that
runs in **12.7 s**. Four of the six were wrong in the first draft, and every one of
them would have silently produced a plausible-looking part. That is the whole
argument for the probe: none of these raised an error at the call site.

### 39. `swEndCondBlind` is **0**, and **1** is `swEndCondThroughAll`

The worst trap of the set, because it does not fail - it produces geometry. Passing
`1` as the end condition makes SOLIDWORKS take a **through-all**, so `D1` is ignored
and the boss runs to the default 1000 mm. Measured on a 30 x 20 mm sketch extruded
with `D1 = 0.002 m`:

| T1 = T2 | resulting body box (mm) | volume |
|---|---|---|
| `1` (`swEndCondThroughAll`) | 30 x 20 x **1000** | 0.000600 m³ |
| `0` (`swEndCondBlind`) | 30 x 20 x **2** | 0.000002 m³ |

Note the volume *agrees with the geometry* in both cases (30 x 20 x 1000 mm³ =
0.000600 m³ exactly), so a volume assertion alone will not catch it - only a
**dimension** assertion will. `D1` does not even have to change: 0.002, 0.005 and
0.030 m all returned the same 1000 mm body. Get the value from the stubs, never from
memory: `grep swEndCond <LOCALAPPDATA>\swbridge\stubs\_swconst_gen.py` prints
`swEndCondBlind =0`, `swEndCondThroughAll =1`, `swEndCondThroughNext =2`,
`swEndCondUpToNext =11`. The working call is in
`relay-board/build_relay_board.py` (`SW_END_COND_BLIND = 0`).

### 40. `ISketchManager.CreateCornerRectangle` takes **six** arguments

`(x1, y1, z1, x2, y2, z2)`. The z pair is ignored by the sketch but **must be
present**: passing four values raises
`com_error(-2147352571, 'type mismatch', None, 5)` = `DISP_E_TYPEMISMATCH`
(`0x80020005`). The failure is clean - the rectangle is simply never created, the
sketch comes back `GetLineCount() == 0`, and the extrude that follows returns `None`
with no error. Expect `lines=4 arcs=0 contours=1` for a rectangle and assert it.

### 41. `SetAddToDB` is on `IModelDoc2`, not `ISketchManager`

`call(sm, "SetAddToDB", True)` raises
`AttributeError: ISketchManager ... has no attribute 'SetAddToDB'`. It is
`call(doc, "SetAddToDB", True)`. The same applies to `ClearSelection2`. Rule 20's
warning still holds - without it, relations eat sketch entities.

### 42. `IBody2.GetBodyBox()` returns **block** order, and the bridge brief says otherwise

Measured on this build: a 30 x 20 x 1.6 mm slab returns
`[0, 0, 0, 30, 20, 1.6]`, i.e.

```
[xmin, ymin, zmin, xmax, ymax, zmax]
```

The task brief that seeded this work states `IBody2.GetBodyBox` is "paired"
(`[xmin,xmax,ymin,ymax,...]`) while `IComponent2.GetBox` is block - that is backwards
for `IBody2`. With the paired reading, a correct 30 x 20 x 10.1 mm 11-body part reports
an envelope of `(7.54, 30.0, 3.06)`: plausible, wrong, and impossible to spot in a
render. Print the raw array before trusting any interpretation of it. (SKILL.md rule 43
now carries the corrected statement.)

### 43. `Session.close_document(title, ...)` takes a **title string**, and fails silently

The parameter is a title, and the implementation finds the document with
`call(candidate, "GetTitle") == title`. Hand it a document *object* and the
comparison is simply False, so it returns `False` and closes **nothing** - no
exception, no log. A cleanup loop that closed eight documents "succeeded" eight times
while `GetDocuments()` still listed all nineteen. Two lessons: call
`app.CloseDoc(title)` with the real title (or check the boolean), and **assert the
open-document count actually fell** afterwards. `swcore.retry_call(app.CloseDoc,
title)` is the form that works.

### 44. A leftover document blocks `SaveAs3` with code **1**, and an unsaved part owns a `~$` lock

If a document with the target name is still open, `SaveAs3` returns `1` and writes
nothing - while the STEP and STL exports of the same part succeed, which makes it look
like a format problem. The tell is a `~$<name>.SLDPRT` file (6 bytes, Hidden) next to
the part. A part created by `NewPart` that was never saved also keeps that lock, so it
can block its own earlier file. Close by title first (trap 43), then save.

### 45. A 1000 mm body is not a scaling bug - check the end condition before the units

The symptom (a body 1000 mm tall, volume exactly area x 1000, `props[3]` agreeing with
the envelope) looks like a metres/millimetres problem. It is not: `GetMassProperties2`
agreed with the box in both cases, which is exactly why the units hypothesis survives
inspection. Trap 39 is the cause. Rule: when a dimension is wrong but the volume is
*self-consistent*, suspect the **feature definition**, not the units.

### 46. The screenshot of a product photo can be anisotropic, and the dimension callouts prove it

The LM2596 vendor image is 1260 x 832 px. The board measures **657 x 455 px**, but
66 : 36 demands 1.8333 and 657 : 455 is **1.4444** - a 27 % mismatch. So the asset has
been rescaled unevenly (stretched vertically or squeezed horizontally) somewhere in
production. Consequence: one uniform mm/px must **not** be derived from it. Derive
`mm_per_px_x = 66 / board_w_px` and `mm_per_px_y = 36 / board_h_px` **separately**,
use them only for that axis, and never let a single dimension's callout set the scale
for the other axis. The KiCad forum thread for this module already warns that board
sizes vary (45x20, 44x21, 66x36 all ship under the name "LM2596 module"), so the
callout is the only authority for the specific unit in hand.

## Sixth part: a vendor assembly, and the STL export that refuses to work

The fifth part's probe paid off here - the blind/extrude end condition, the six-argument
rectangle and the `GetBodyBox` field order were all already settled, so none of them bit.
What did bite was the **export**, which is where a model that measures perfectly can still
hand over an empty file (traps 47-51). The pattern is worth remembering: the geometry API
on this build is reliable, and the *file* API is where the surprises live.

Source for this part: the vendor's own SolidWorks assembly from GrabCAD, imported through
`LoadFile4` (rule 31). Nothing was modelled by hand - 7 components, 5 part documents,
PCB 66.000 x 1.600 x 36.000 mm, assembly envelope 68.4 x 19.05 x 36.0 mm, volume
8221.485 mm3, STEP 12 `MANIFOLD_SOLID_BREP`, STL 39 276 triangles with **0 open edges**.

### 47. `SaveAs3` on one part exports an STL for **every open document**

Measured 2026-09-16 on the LM2596 job. With seven documents open (an assembly plus its
six parts), a single `SaveAs3(part, r"D:\out\x.STL", 0, 1)` wrote **seven** files:

```
x - 220 35V Capacitor, ...-1.STL      x - Base,  ...-1.STL
x - 220 35V Capacitor, ...-2.STL      x - DG301-5.0-02, ...-1.STL
x - 3 Digit 7 Segment Display, ...-1.STL   x - DG301-5.0-02, ...-2.STL
x - Trimpot 3296W, ..._Valor predeterminado-1.STL
```

The name is `<target stem> - <document title>.<ext>`. Two consequences, both of which
cost a cycle here:

* **"Find the file we just wrote" is ambiguous.** A `startswith(component_name)` lookup
  matched the wrong entry and the merge silently became **three meshes repeated** -
  envelope 62.4 x 18.0 x 30.3 mm instead of 68.4 x 19.05 x 36.0, with every piece
  reporting an identical bounding box (`shift` spread 34.67 mm).
* **The title is what identifies the file**, not the name you passed. The Base document
  is titled `Base,  LM2596 ...` - note the **two spaces** after the comma - so match on
  `filename.split(" - ", 1)[1].split(",")[0].strip().lower()`, and normalise whitespace
  on both sides.

The robust shape: give each export its **own directory**, so one folder holds one
document's mesh, and move the file out of that folder before the next export.

### 48. An assembly has **no STL translator** on this build

`SaveAs3(assembly, ".STL", 0, 1)` returns **`0`** and writes **zero bytes** - measured
on an already-saved assembly, from the same call pattern that exported STEP of the same
assembly successfully (1 113 628 bytes). A return-code check alone reports success.
Same family as trap 23: verify the artifact by parsing it, never by the code.

`IBody2.GetTessellation(None)` is not the workaround either: it raises
`com_error(-2147417851, ...)` = `0x80010105` (`RPC_E_SERVERFAULT`) on the first solid
tried, so the tessellation route is closed on this install.

Working route for a watertight board mesh:

1. Export each part with `SaveAs3` into its **own** directory (trap 47).
2. The mesh arrives in the **document's local frame plus one translation**: the DG301
   mesh measures `7.6 x 14.3 x 10.6` where its world box is `14.3 x 10.6 x 7.6` - the
   component is rotated 90 deg about Y. Recover the translation by matching the mesh's
   min corner to the document's own `GetBodyBox` min corner.
3. Map mesh -> document local -> world with `IComponent2.GetXform()` (trap 28 layout).
4. **Flip the winding when the rotation's determinant is negative.** This one is easy to
   miss and loud when you do: variants with det = -1 invert every triangle, so the
   divergence-theorem volume partly cancels - the first attempt read **4471 mm3 against
   the solid's 8221 mm3** and looked like a modelling error.
5. Assert open edges, non-manifold edges and the volume against `GetMassProperties2`.
   Final result here: 39 276 triangles, **0 open edges**, 0 non-manifold, volume
   8221.737 mm3 vs the solid's 8221.485 (**+0.0031 %**), envelope X/Z exact.

### 49. `GetBodies2`'s type flag does not separate solids from surfaces here

`GetBodies2(0, False)` returned all **21** bodies of the assembly and
`GetBodies2(1, False)` returned **0**. A per-body face count confirms all 21 are real
bodies with geometry. The STEP export of the same assembly carries only **12**
`MANIFOLD_SOLID_BREP`, so the count that means anything is the STEP's - the 9-body
difference is the DG301 pins and the capacitor leads, which SolidWorks holds as solid
bodies and the STEP writer emits as open shells. **Do not assert a solid-body count from
`GetBodies2` on this build**; cross-check STEP instead.

That same shortfall explains a matching envelope difference: the mesh comes out
16.60 mm on the axis whose extremes are the pin tips (y = -5.9), 2.45 mm under the
solid's 19.05 mm box. Report it; do not tune it away.

### 50. Named views: the two **dimetric** ones do not apply, and the camera is read-only

`ShowNamedView2("*上视"/"*前视"/"*等轴测"/"*右视", 7)` all work and render genuinely
different images. The dimetric pair does **not**:

* `"*上下二等角轴测"` and `"*左右二等角轴测"` each produced a PNG **byte-identical** to
  the isometric one - SHA-256 `bc95e43a1c1ba353` for all three. Both names are genuinely
  in the document's list (`GetModelViewNames()` returns 10 views, the last two being
  exactly those).
* `ShowNamedView2` returns **`None`** for every name, valid or not, so its return value
  proves nothing.
* `IModelView` (from `ActiveView`) exposes no `SetModelViewXForms` here, and
  `ICamera.SetPositionSpherical` reads back correctly (A2 = 20.70 deg) while the render
  stays byte-identical to isometric.

**A render that shares a hash with another is not a different view.** Hash every PNG
(`sha256`) and assert the set is distinct before promising four views - this is trap 28's
lesson, and it caught a fake "dimetric" here.

### 51. Author the deliverable documentation from measurements, and state the gaps

The README and HANDOFF for this part carry three numbers that a tidy report would
quietly drop: the STL's 2.45 mm Y shortfall, the 21-vs-12 body difference, and the fact
that `_dimetric.png` is the isometric image. Each is in the report with its cause and
its effect on the thing being handed over. The brief allows a few millimetres provided
the error is never *larger* than reality, which is only checkable if the residuals are
written down.

## Seventh part: 172 centre points on two boards, and the point-creation flake

Task: one sketch point on the **centre of the wire-entry face** of every connector pin -
top faces on the two pin headers, but **side** faces on the relay screw terminals,
because a real screw terminal takes its wire horizontally. Sketch points only; no
connection-point features were wanted.

Measured inventory, from `IBody2.GetBodyBox()` grouped boxes:

| part | class (mm) | n | entry face | point placed |
|---|---|---|---|---|
| `Arduino_Mega_2560_R3` | 2.04x2.04x8.50, z 1.60..10.10 | 102 | top, +Z | (cx, cy, 10.10) |
| `Relay_Board_16CH_12V` | 1.94x1.94x8.50, z 1.60..10.10 | 20 | top, +Z | (cx, cy, 10.10) |
| `Relay_Board_16CH_12V` | 4.48x10.20x10.00 | 48 | side, outward ±Y | (cx, y_face, 6.60) |
| `Relay_Board_16CH_12V` | 6.00x4.48x10.00 | 2 | side, outward +X | (x_face, cy, 6.60) |

The 48 terminals form two rows of 24 at y0 = 3.00 and y0 = 76.80 on a 179x90 board, so
"outward" is -Y for the first row and +Y for the second. The 2 power poles sit on the
x = 179 short edge, so theirs is +X. The entry face is *not* the top for a screw terminal -
ask which way the real part takes its wire before assuming.

### 52. `GetTitle` / `GetPathName` / `GetType` / `GetSaveFlag` are **properties** here, not methods

`doc.GetTitle()` raises `TypeError: 'str' object is not callable`, and `app.ActiveDocName`
does not exist at all. An unguarded call aborts the whole job and the bridge log shows
only a bare `pywintypes.com_error` with no traceback, so wrap every member read:

```python
import types
def P(obj, name, *a):
    v = getattr(obj, name)
    return v(*a) if isinstance(v, types.MethodType) else v
```

Also wrap the whole job body in `try/except` and write `traceback.format_exc()` into the
UTF-8 report file - the bridge log alone will not tell you which line died.

### 53. `SelectByID2` lives on `IModelDocExtension`, and its `Callout` argument rejects `None`

`IModelDoc2.SelectByID2` does not exist. `IModelDocExtension.SelectByID2(Name, Type, X, Y,
Z, Append, Mark, Callout, SelectOption)` declares `Callout` as VT_DISPATCH by value, so
`None` gives `com_error(-2147352571, '类型不匹配', None, 8)`.

Do not fight it - the entity-level selectors have **no** Callout argument:

| call | parameter types from the stub |
|---|---|
| `IEntity.Select2(Append, Mark)` | `((11,1),(3,1))` |
| `ISketchPoint.Select2(Append, Mark)` | `((11,1),(3,1))` |
| `ISketchPoint.Select4(Append, Data)` | `((11,1),(9,1))` |

`cast(face, "IEntity").Select2(True, 1)` and `cast(point, "ISketchPoint").Select2(True, 1)`
both return `True` and leave exactly 2 objects selected. To read a signature instead of
guessing, grep the generated stub - it is a plain Python file and every `def` ends in an
`InvokeTypes` list whose VT codes say which arguments accept `None`:

```
%LOCALAPPDATA%\Temp\gen_py\3.11\83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
```

### 54. `IConnectionPointFeatureData` **is** in the typelib - read a connection point back numerically

An earlier note in this file said nothing could read a connection point's position. That
is wrong: cast the feature definition and every field is exposed.

```python
cpd = cast(ft.GetDefinition(), "IConnectionPointFeatureData")
cpd.Location        # [x, y, z] in metres - the selected sketch point, exactly
cpd.Direction       # unit vector - the selected face's normal
cpd.RouteDiameter, cpd.StubLength
cpd.RouteType, cpd.RouteSubType, cpd.Name2, cpd.PortID, cpd.ElectricalPinID
```

Proven call `InsertConnectionPoint(3, 1, False, 0.001, 0.005, 0, 0, 0, "", 0, 0, 0, "", "")`:
`Location` came back equal to the sketch point to 4 decimals, `Direction` `[0, 0, 1]`,
`RouteDiameter` 0.001, `StubLength` 0.005 - and `RouteType` **6**, not the 3 that was
passed. Use this instead of a screenshot to prove where a connection point landed.

### 55. `ISketchManager.CreatePoint` can silently drop you onto an earlier point - read back and retry

Creating 70 points in one 3D sketch, 22 came back at the **wrong** coordinates: 20 header
points collapsed onto 6 positions and 2 power points onto 1, while the 48 terminal points
created in the same sketch were perfect. `CreatePoint` returned an object whose `X`/`Y`/`Z`
belonged to an *earlier* point, and `GetSketchPoints2` agreed with it. Repeating the
identical batch on a clean document gave **20/20 exact**, so it is a flake, not a snapping
rule. Do not spend rounds theorising about grids - verify.

```python
P(sm, "Insert3DSketch", True)
for p in targets:
    P(sm, "CreatePoint", p[0]/1000.0, p[1]/1000.0, p[2]/1000.0)
P(sm, "Insert3DSketch", True)          # the same call closes the sketch

got = sorted(key3((P(sp,"X")*1000.0, P(sp,"Y")*1000.0, P(sp,"Z")*1000.0))
             for sp in as_list(P(P(sk, "GetSpecificFeature2"), "GetSketchPoints2")))
if got != sorted(key3(p) for p in targets):
    drop_all(d)                        # then retry the whole sketch
```

Both parts then verified on the first attempt: 102/102 and 70/70, all distinct, and a
**second independent run** that recomputed every target from `GetBodyBox` matched again.

* `GetSketchPoints2` does **not** return points in creation order - compare sorted
  multisets, never sequences.
* Measured on the same 22-point probe: `ISketchManager.CreatePointDB`,
  `ISketchManager.CreatePoint2`, `IModelDoc2.CreatePoint` and `IModelDoc2.CreatePointDB`
  produced **0/22** usable points; `IModelDoc2.CreatePoint2` managed 21/22.
  `ISketchManager.CreatePoint` is the one to use.

### 56. `IFeature.Name` is writable - rename after creation

`InsertConnectionPoint` and `Insert3DSketch` take no name argument. Assign it afterwards
via `P(d, "FeatureByPositionReverse", 0).Name = "接线点-MEGA"`; Chinese names survive the
feature-tree walk and come back intact.

### 57. Delete features with `IFeature.Select2` + `IModelDoc2.EditDelete()`

```python
P(d, "ClearSelection2", True)
P(feature, "Select2", False, 0)
P(d, "EditDelete")               # returns None on success
```

Delete every `3DProfileFeature` / `ConnectionPoint` in a loop at the **start** of the job,
re-reading `FirstFeature` each pass. That makes the job idempotent, so a retry after a
partial failure is safe rather than additive. One clean run of the whole 172-point job
took 37 s.

### 58. `ShowNamedView2` needs a real view id - `-1` is accepted and does nothing

Calling `ShowNamedView2("*Top", -1)` and `ShowNamedView2("*Isometric", -1)` returned `None`
and left the camera exactly where it was, so the "top view" screenshot was really the
previous document's view - and a `-1` id is silent, not an error. Trap 50 above has the
working form: the view name **plus a real id**, e.g. `ShowNamedView2("*等轴测", 7)`.
`ViewZoomToSelection()` likewise returned `None` without zooming, and
`IModelDocExtension.ZoomByFactor` does not exist through dynamic dispatch
(`AttributeError('<unknown>.ZoomByFactor')`). When you cannot aim the camera, prove the
geometry numerically and treat the render as a secondary check that can only add
confidence.

### 59. `Insert3DSketch` **re-opens the selected 3D sketch** - one stray selection silently destroys the previous sketch

This is the worst trap in this file, because it reports success the whole way and leaves
you with one sketch instead of 172.

With a 3D sketch already in the part, `ISketchManager.Insert3DSketch(True)` does **not**
create a new one - it re-opens the sketch that is currently selected. Building
one-sketch-per-point, the loop was:

```python
P(sm, "Insert3DSketch", True); P(sm, "CreatePoint", ...); P(sm, "Insert3DSketch", True)
sk = P(d, "FeatureByPositionReverse", 0); sk.Name = name      # rename leaves it SELECTED
```

The rename leaves the sketch selected, so the next `Insert3DSketch` re-opened *that*
sketch and the next `CreatePoint` appended a second point to it. The check then saw a
2-point sketch, rejected it, and the cleanup **deleted the previous sketch**. Measured
trace, five targets:

```
target 1 attempt 1: opened '3D草图114' with 0 existing point(s)  -> OK
target 2 attempt 1: opened '点-TEST-001' with 1 existing point(s) -> rejected, deleting 点-TEST-001
target 2 attempt 2: opened '3D草图115' with 0 existing point(s)  -> OK
target 3 attempt 1: opened '点-TEST-002' with 1 existing point(s) -> rejected, deleting 点-TEST-002
...
```

Every target "succeeded" on attempt 2, so the job reported **0 failures** - and finished
with exactly **one** surviving sketch, the last one (`点-MEGA-102`, `点-PWR-2`), and a
feature count of `276+1` / `206+1` instead of `276+102` / `206+70`. A feature count that
grew by 1 when you asked for 172 is the tell.

**Fix - clear the selection before every `Insert3DSketch`, and assert the sketch you
opened is empty before you draw in it:**

```python
P(d, "ClearSelection2", True)
P(sm, "Insert3DSketch", True)
act = P(d, "GetActiveSketch2")
if act is None or len(as_list(P(act, "GetSketchPoints2"))) != 0:
    P(sm, "Insert3DSketch", True)      # close whatever re-opened
    P(d, "ClearSelection2", True)
    continue
P(sm, "CreatePoint", ...)
P(sm, "Insert3DSketch", True)
P(d, "ClearSelection2", True)          # do not leave it selected for the next pass
```

With that, all five probe sketches persisted side by side and the real job put **102 +
70 = 172 single-point sketches** in place, `VERIFIED 172/172` against a final
`FirstFeature`/`GetNextFeature` walk. Lesson: after building a large set of features,
**count them in the tree** - per-item return codes were all `True` while 171 of 172
sketches did not exist.

## Eighth part: turning a 6-way connector into a 12-way one, and the cuts a pattern leaves behind

Task: a lever wire connector sold as **SPL-122 / "二进十二出"** (2 in, 12 out) had to go into a
wiring-harness assembly. No model of it exists anywhere, so the job became "take the same
family's 6-way model (`Suplin SPL-62`, native `.SLDPRT`, 66 features) and grow it".

This part is mostly one lesson with teeth: **a linear pattern is not the only thing that
repeats geometry, and editing the pattern count touches only what is in the pattern.**

The frame of the source model: `X` = the pole row, `Y` = height, `Z` = wire direction.
Measured from the solid, not from the docs:

| quantity | value |
|---|---|
| pole pitch | **5.2 mm** |
| lever body | 2.8 (X) x 3.606 (Y) x 14.7 (Z), a separate solid each |
| wire bore | radius **2.15 mm**, centreline `y = -2.9`, axis along `Z` |
| shell, 6+2 | 34.599 (X) x 16.275 (Y) x 41.267 (Z) |
| bodies | 9 = 1 shell + 6 output levers + 2 input levers |

### 60. Bumping a linear pattern's instance count silently leaves every non-patterned repeat behind

Three features in the tree look like the whole story, and only two of them are:

```
F041  Линейный массив1  LPattern  D1TotalInstances=2  spacing=0.0052  rev=True
      PatternFeatureArray = ['Бобышка-Вытянуть3', 'Вырез-Вытянуть15']      <- the 2 input levers
F046  Линейный массив2  LPattern  D1TotalInstances=3  spacing=0.0052  rev=False
      PatternFeatureArray = ['Бобышка-Вытянуть4', 'Вырез-Вытянуть16']      <- output levers
F047  Линейный массив5  LPattern  D1TotalInstances=4  spacing=0.0052  rev=True
      PatternFeatureArray = ['Вырез-Вытянуть16', 'Бобышка-Вытянуть4']      <- the SAME seed pair
```

The output row is **two patterns sharing one seed** (`3 + 4 - 1 = 6`), one running `+X` and
one `-X`. So 12 poles is `F046 : 3 -> 6` and `F047 : 4 -> 7` (`6 + 7 - 1 = 12`), and the
housing is a single extrusion along `X`, so `Бобышка-Вытянуть1 : 33.700 -> 64.900 mm`.

Both edits return `True` and the measurement agrees:

```
Линейный массив2 : D1TotalInstances 3 -> 6   ModifyDefinition=True
Линейный массив5 : D1TotalInstances 4 -> 7   ModifyDefinition=True
ForceRebuild3 -> True
15 BODIES   TOTAL X extent = -30.000 .. 30.000  (len 60.000 mm)
```

12 levers, spanning exactly 60.000 mm. **And the wire holes were still 6.**

`Вырез-Вытянуть6` is a single cut whose sketch `Эскиз8` holds **all six circles itself**
(`x = -13.0 ... +13.0`, `y = -2.90`, r 2.15) and is in no pattern. Patterning it would have
been wrong (it would stamp the same six holes at each instance), and nothing about the
pattern edit told you it existed. The only way the gap showed up was by **counting**:

```
BEFORE: 16 circular edges r~2.15, distinct X = [-13.0 -7.8 -2.6 2.6 7.8 13.0]     <- 6
AFTER : 32 circular edges r~2.15, distinct X = [-28.6 ... +28.6]                 <- 12
```

**Before you touch a third-party multi-instance model, enumerate the repeats that are not
in a pattern** (see 61 for the cheap way), and **after** the edit verify by counting the
entity class - bores, rims, bodies - not by the feature's return value.

### 61. The cheapest audit of a foreign tree: read every sketch's own 2D extent

`ISketch.GetSketchPoints2()` -> `ISketchPoint.X/Y/Z` gives the sketch's **local** coordinates
(the third is always 0), so one pass over the tree prints a fingerprint per sketch. On
SPL-62 that immediately separates the features that grow from the ones that will not:

| feature | sketch | x range (mm) | verdict |
|---|---|---|---|
| `Вырез-Вытянуть2` | `Эскиз3` | -32.45 .. 32.45 | constrained to the body -> **grew by itself** |
| `Вырез-Вытянуть11` | `Эскиз12` | -32.45 .. 32.45 | grew by itself |
| `Вырез-Вытянуть-Тонкостенный1` | `Эскиз13` | -32.45 .. 32.45 | grew by itself |
| `Вырез-Вытянуть6` | `Эскиз8` | -13.00 .. 13.00 | the 6 wire holes -> **stale** |
| `Вырез-Вытянуть10` | `Эскиз9` | -14.45 .. 14.45 | the 6 bay windows -> **stale** |
| `Вырез-Вытянуть14` | `Эскиз18` | -19.48 .. 19.48 | stale |
| `Вырез-Вытянуть-Тонкостенный2` | `Эскиз14` | -19.85 .. 19.85 | stale |
| `Вырез-Вытянуть1` + mirror | `Эскиз2` | -19.50 .. 0 | stale - this is the one that leaves blank blocks at both ends |

Sketch-local and model axes coincide when the sketch plane's normal is `Z` (the front
plane), which is the common case for a part laid out in `X`; confirm it by checking that
the sketch's `x` range brackets the model `X` you already measured.

### 62. A blind cut's direction does **not** follow the boss's convention, and a wrong guess is `None`

Sampling a 20 mm part instead of the 1 000-line one, on the **front plane** of a fresh
`NewPart`:

```python
# boss: +Z, works
call(fm, "FeatureExtrusion3", True, False, False, 0, 0, mm(depth), 0.0, ...)

# cut: T1=T2=0 (blind) with the SAME triple returned None and cut nothing
call(fm, "FeatureCut4", False, False, False, 0, 0, mm(depth), 0.0, ...)
```

Measured working combinations, all in one build:

| feature | plane | what it does | `(Sd, Flip, Dir)` that worked |
|---|---|---|---|
| output bores | front | blind 12 mm along **+Z** | `(True, False, True)` |
| input bores | front | blind 25.8 mm from a 12 mm start offset, **+Z** | `(True, False, True)` |
| lever windows | top | blind 9.5 mm up (**+Y**) from a 4.5 mm start offset | `(True, False, False)` |
| ear holes | top | through-all both ways | `(False, False, False)` |

Through-all is direction-independent, which is why the earlier `cut_through` helper worked
with `Sd=False` and hid this. `None` is the only signal - there is no error code.

**Retry across the combinations rather than reasoning about them**, and remember that a
failed feature leaves its sketch **open** (trap 17), so the retry has to clean up first:

```python
CUT_VARIANTS = [(True, False, False), (False, False, True), (False, True, False),
                (True, False, True), (True, True, False)]

def cut(depth, start=0.0, through=False, draw=None):
    t1 = t2 = 1 if through else 0            # 0 = swEndCondBlind
    d1 = 0.0 if through else mm(depth)
    t0 = 3 if start else 0                   # 3 = swStartOffset
    for (sd, flip, dr) in ([(False, False, False)] if through else CUT_VARIANTS):
        draw()                               # re-create the sketch from scratch
        call(d, "SetAddToDB", False)
        f = call(fm, "FeatureCut4", sd, flip, dr, t1, t2, d1, 0.0, False, False,
                 False, False, 0.0, 0.0, False, False, False, False, False, False,
                 True, False, False, False, t0, mm(start), False, True)
        if f is not None:
            return cast(f, "IFeature")
        drop_last_sketch()                   # exit + delete the orphan

def drop_last_sketch():
    if getv(sm, "ActiveSketch") is not None:
        sm.InsertSketch(True)                # close the dangling sketch
    P(d, "ClearSelection2", True)
    f = P(d, "FeatureByPositionReverse", 0)
    if f is not None and P(cast(f, "IFeature"), "GetTypeName2") == "ProfileFeature":
        cast(f, "IFeature").Select2(False, 0)
        P(d, "EditDelete")
    P(d, "ClearSelection2", True)
```

### 63. Capture a zero-error baseline **before** editing a foreign model

`IFeature.GetErrorCode()` is `0` for clean. Walking all 66 features of SPL-62 recorded **66
zeros**; after the edit exactly one was non-zero:

```
F061 | Вырез-Вытянуть-Тонкостенный8 | CutThin | err=1
```

That single number is what proved the fault was introduced rather than inherited - and it
also explains a `ForceRebuild3 -> False` that would otherwise have looked like a failed
edit while the geometry was in fact correct. Take the baseline first; you cannot recover it
afterwards without a pristine copy.

### 64. `IModelDoc2.EditSketch()` opens the selected sketch; `InsertSketch(True)` closes it

To add geometry to an existing sketch, no `SelectByID2` and no `"SKETCH"` entity type is
needed (so trap 8's `Callout` argument never comes up):

```python
P(d, "ClearSelection2", True)
sketch_feature.Select2(False, 0)         # IFeature.Select2
d.EditSketch()                            # returns None - the return value proves nothing
assert getv(sm, "ActiveSketch") is not None
call(d, "SetAddToDB", True)
sm.CreateCircleByRadius(mm(x), mm(y), 0.0, mm(r))
call(d, "SetAddToDB", False)
sm.InsertSketch(True)                     # back out
P(d, "ClearSelection2", True)
```

Inside a front-plane sketch the local frame **is** model `(X, Y) - verified by measuring the
bores that came out, not by assuming: the six added circles landed at exactly
`x = ±18.2 / ±23.4 / ±28.6`, `y = -2.90`, which is where they were asked for.

### 65. `ISketch.GetLineCount2` takes exactly one argument

`GetLineCount2(0, 0)` raises `TypeError: ISketch.GetLineCount2() takes from 1 to 2 positional
arguments but 3 were given`. One pass of the tree audit died on every sketch because of it.
`GetArcCount()` takes none.

### 66. `IBody2.GetFeature()` does not exist on this build

`AttributeError: ...IBody2... object has no attribute 'GetFeature'`. "Which feature made
this body" is not answerable that way; use the tree walk, or suppress a candidate and
re-measure the body set.

### 67. `SaveAs3` to a native `.SLDPRT` returns **64** and still writes the file

The bridge's `save` command only owns the neutral translators:

```
ERROR  : unknown export format 'sldprt'
  -> Known formats: 3mf, iges, pdf, step, stl, x_t
```

Save a native part from inside a job instead, and do **not** treat a non-zero code as
failure - check the file and the title change:

```python
code = d.SaveAs3(r"...\part.SLDPRT", 0, 1)    # swSaveAsCurrentVersion, swSaveAsOptions_Silent
# measured: code = 64, file written (814 976 bytes), GetTitle() -> new name, GetSaveFlag() -> False
```

`swSaveAsOptions_Silent = 1` and `swSaveAsCurrentVersion = 0` are both in the generated
stubs - look them up rather than recalling (rule 34).

### 68. The structural answer: build the part with **no patterns at all**

After 60, the rebuild took the opposite approach deliberately. Every repetition lives in
**one** sketch, and nothing is patterned:

| feature | one sketch holds | one feature produces |
|---|---|---|
| output bores | 12 circles | 12 bores |
| input bores | 2 circles | 2 bores |
| lever windows | 14 rectangles | 14 pockets |
| levers | 14 rectangles, `merge=False` | 14 separate bodies |

Result, measured on the finished solid:

```
BODIES:   15  (expect 15 = 1 shell + 12 output levers + 2 input levers)
ENVELOPE: x -30.400..30.400 (60.800)   y 0.000..16.500   z 0.000..37.800
bore rim edges r~2.1: 54 ;  distinct bore X: 12
SaveAs3 -> 0
```

Changing the pole count is now one constant (`N_OUT = 12`), and the class of bug in 60 is
**structurally impossible** rather than merely tested for. When you know a model will be
re-scaled, prefer "all copies in one sketch" over a pattern.

The same job also re-confirmed trap 13's coordinate convention on the **top** plane:
local `(u, v) = (X, -Z)`, so a rectangle at model `z = 6.0` is drawn at `v = -6.0`. The
T-shaped footprint (a 60.8-wide output block plus a 15-wide input block offset along `Z`)
came out with the correct `60.800 x 37.800` plan on the first try because of it.

### 69. Sourcing: search the part number, then chase the remix chain

Not an API trap, but it decided this whole job. "lever wire connector" returns thousands of
mounting brackets; the part is a **SPL-122**, and searching *that* is what surfaced the
family (`SPL-62 / SPL-93 / LT-633`). The exact 12-way model does not exist anywhere, and the
only true 1:1 body found was reached by following a remix link backwards
(`thingiverse.com/thing:6710306` -> "Based on the original model" ->
`printables.com/model/842131`). Two licence facts that a search snippet will not tell you:

* GrabCAD community uploads fall under ToU **§6.2 - non-commercial internal use only**.
* Printables `842131` renders its licence as an **image**; the markdown extraction of the
  page shows nothing. Read the block, and note it is **CC BY-NC 4.0** (remix allowed,
  commercial use not, and Printables itself flags it "not a Free Cultural Work").

Read the licence from the model's own page before it goes into anything deliverable.

### 70. Which cut is stale is decided by its **plane and end condition**, not by its sketch's X range

Trap 61 narrows the suspects; it does not name the culprit. In this job the sketch-extent
audit pointed at `Вырез-Вытянуть1` + mirror (`Эскиз2`, x -19.50..0, the narrowest of the
stale-looking ones) - and that was **wrong**. Reading the sketch's own placement settles it
in one line:

```python
mt  = getv(sk, "ModelToSketchTransform")     # model -> sketch
arr = list(mt.ArrayData)                     # [0:9] 3x3 rotation row-major, [9:12] translation (m)
```

For `Эскиз2` that is `R = [[0,0,1],[0,1,0],[-1,0,0]]`, `t = (0, 0, -32.45)`, so
`p_model = R_transpose * (p_sketch - t)` gives **`X = 32.45` constant** - the sketch sits on
a plane normal to X and its `u`/`v` are `(Z, Y)`. A cut on an X-normal plane with
`T1 = swEndCondThroughAll` therefore already spans the *whole new length*; it was never a
problem. The same one-line read identifies the real ones:

| feature | sketch plane | end condition | follows a length change? |
|---|---|---|---|
| `Вырез-Вытянуть1` + mirror | X = 32.45 | through-all along X | **yes** |
| `Вырез-Вытянуть10` | Z = -19.50 | blind | **no** - and it carries 6 bay windows itself |
| `Вырез-Вытянуть14` | X = 0 | blind 29.5 | no - stops at X 29.5 |
| `Вырез-Вытянуть-Тонкостенный2` | Z = -19.50 | thin, X +-19.85 | no |

The observable symptom matched `Вырез-Вытянуть10` exactly: with its six windows
(`u = +-(1.15..4.05)`, `+-(6.35..9.25)`, `+-(11.55..14.45)`, all at `v` 3.80..8.50) covering
only `|X| <= 14.45`, the six *new* levers stood in solid material - the render showed them
as slivers half-buried in an uncut top face, while their neighbours sat in open slots. Adding
the six missing windows (`|X|` 16.75..19.65, 21.95..24.85, 27.15..30.05) took the sketch from
26 to 50 segments and freed them.

So the three-way test, in order: **is it in a pattern? does its end condition reach? is its
sketch dimensioned to the old length?** A `through-all` cut survives a length change for
free; a `blind` one and an all-copies-in-one-sketch one do not.

### 71. Sourcing round 2: the datasheet in the photo is the search key, and the *series sibling* is the prize

Trap 69 said "search the part number". The e-stop job shows what to do when the photo does
not *contain* a searchable part number, only a **spec table**:

| the photo said | what to search |
|---|---|
| 型号 `LA38` | `LA38` + the format (`grabcad LA38 3D model`) |
| 开孔 `22mm`, 触点 `一开一闭`, 操作 `自锁式` | nothing yet - translate to the industry code |
| all three together | **`LA38-11ZS`** - and *that* is what has hits |

The digit-code convention is the unlock: `<series>-<contact><type><action>` where
`11` = 1NO+1NC, `ZS` = 自锁 (push-lock / twist-release). Same part, different name, and only
the code appears in CAD titles. Do this translation **before** searching, not after the
first search comes back with shelving brackets.

**When the exact model does not exist, hunt the series sibling.** `LA38` is a button
*family*: `LA38-11D` is the illuminated flush-head version and it shares the 中座 (mounting
collar), contact module and base with `LA38-11ZS` - **only the head differs**. So
`grabcad.com/library/illuminated-push-button-switch-la38-11d-1` (`.SLDPRT`, 7125 downloads)
is not "a different part", it is the best available starting body for the e-stop. This is
the SPL-62 -> SPL-122 trick generalised: **when the exact variant is absent, take the
sibling that shares the tooling and re-cut the one feature that differs.** Sharper than
taking a same-spec part from another manufacturer, because the shared geometry is real.

**Verify a candidate by looking at its render, not its title.** Every one of six candidates
titled "22mm ... emergency stop" rendered as something materially different: the top
same-spec hit (`TOKCKYBL 22MM 1NO 1NC`) renders the **boxed** variant (mushroom + yellow
enclosure), and the most-liked one (9929 downloads) is an unnamed US-style twist-release
with routing points baked into its `.SLDASM`. Downloading the six card images and reading
them cost one command and changed the ranking against the titles twice. Card images live at
a stable, guessable URL once you have read the model page:

```
https://grabcad.com/screenshots/pics/<hash>/large.png     # or .JPG - do not trust the ext
```

**Two tooling corrections to earlier notes in this file:**

* `pwsh` **can** reach the network. `Invoke-WebRequest` failed with 无法连接到远程服务器 in an
  earlier session, but here it fetched six GrabCAD screenshots on the first try. Treat "no
  network in pwsh" as a per-session fact to re-test, not a standing one.
* A download saved as `x.png` whose bytes are JPEG makes `read_image` **refuse** the file
  (`the .png extension declares image/png, but the bytes use a different image format`).
  GrabCAD serves `.JPG` from `.png`-looking URLs. Sniff and rename in one pass:

```powershell
$b = Get-Content $f -Encoding Byte -TotalCount 4
$hex = ($b | ForEach-Object { $_.ToString('X2') }) -join ''
# FFD8FF->jpg  89504E47->png  47494638->gif  52494646->webp
```

**A site that resolves to a loopback address is not down, it is unreachable *from here*.**
`www.3dcontentcentral.com` resolved to `127.0.0.1` on this machine (proxy fake-IP), so both
`read_page` and `web_fetch` refused it with "resolves to a non-public IP address" /
"Blocked private network target". That is a local DNS artifact, not a dead link: the page
loads fine in the user's own browser. Say so and hand over the URL instead of burning turns
on retries.

## Ninth part: an illuminated pushbutton into a mushroom emergency stop

The part: a `LA38-11D` (22 mm panel pushbutton, flush illuminated head, 1NO+1NC) had to
become a `LA38-11ZS` (the same series' mushroom-head emergency stop). The manufacturer's own
photo gave three numbers - total height **75 mm**, footprint **32 x 29**, panel hole **Ø22** -
and nothing else. No dimensioned drawing, no data sheet.

### 72. On a foreign model, probe late-bound. `getv` is 2-argument and `cast(..,"ISketch")` was dead

Two readings that looked right and were not:

```python
getv(d, "GetPartBox", True)          # TypeError: getv() takes 2 positional arguments
cast(f, "ISketch") -> GetLineCount2  # com_error on EVERY sketch member
```

`getv(obj, name)` reads a *property* or a *zero-argument* method; anything with arguments
goes through `call(obj, name, *args)`. And on this model the generated `ISketch` interface
resolved every member to a COM error, while the **raw dispatch object** answered fine. So the
working pattern for a foreign part is the same late-bound helper used in the SPL jobs:

```python
def P(obj, name, *a):
    v = getattr(obj, name)                      # dynamic dispatch
    return v(*a) if isinstance(v, types.MethodType) else v
```

`P(cast(f, "IFeature"), "GetSpecificFeature2")` -> `P(sk, "GetSketchSegments")` ->
`cast(seg, "ISketchLine")` / `"ISketchArc"` all worked. Reach for `getv`/`call` on the
*modelling* side (where `swcore` is bound) and `P` on the *interrogation* side.

`GetTypeName2` reads `"ProfileFeature"` for a sketch and **`"ICE"`** for an extrude on this
build, while `GetTypeName` says `"Boss"` / `"Cut"` / `"Revolution"`. Test `GetTypeName2 ==
"ProfileFeature"` for sketches and use `GetTypeName` for everything else.

### 73. Validate a new feature against a BODY, never against the whole-part envelope

This cost four runs. The job added a Ø32 x 4 mm collar at `Y 0..4` and checked
`envelope.Ymax == 4` - but the source model already reaches `Ymax = 13`, so the check failed
for every flag triple and the job reported the false conclusion *"no (Sd,Flip,Dir) combination
puts material at Y<=4"*, when in fact the very first triple had worked. The fix is to look for
**a new body whose box is the intent**:

```python
def find_new_body(ymin, ymax, dia=None, tol=0.05):
    for b in solids():
        box = [v*1000 for v in P(b, "GetBodyBox")]      # [x0,y0,z0,x1,y1,z1]
        if abs(box[1]-ymin) < tol and abs(box[4]-ymax) < tol:
            if dia is None or (abs(box[3]-box[0]-dia) < tol and abs(box[5]-box[2]-dia) < tol):
                return b
    return None
```

### 74. Make every new feature direction-agnostic, and roll the failures back with `EditUndo2(1)`

The same `(Sd, Flip, Dir)` triple does not mean the same thing on the Top Plane as on the
Front Plane. On the Top Plane (normal `+Y`) a boss went `-Y` with the triple that goes `+Z` on
the Front Plane. Rather than reason about it, loop the triples, measure, and undo the misses:

```python
for (sd, flip, dr) in [(True,False,False), (True,True,False), (False,False,False),
                       (False,False,True), (True,False,True), (False,True,False)]:
    new_sketch(plane); draw(); end_sketch()
    f = P(fm, "FeatureExtrusion3", sd, flip, dr, SW_BLIND, SW_BLIND, ...)
    if f is not None and find_new_body(y0, y1, dia):
        return f
    P(d, "ClearSelection2", True); P(d, "EditUndo2", 1)      # clean rollback
```

`FeatureRevolve2` got the same treatment with the `(SingleDir, ReverseDir)` pairs. The undo is
what makes the retry safe: without it the wrong body sits there and poisons every later
measurement. Measured winners on this part: **`(True,False,False)`** for both extrudes off the
Top Plane, **`(True,False)`** for the revolve off the Front Plane.

### 75. Make the job idempotent: re-copy from the pristine source, and pre-close your own leftovers

A run that dies mid-way leaves its document **open**, which locks the file, so the next run
dies at `shutil.copyfile` with `PermissionError: [Errno 13]`. Fix both ends:

```python
for t in list(session.titles()):
    if "LA38-11ZS" in str(t):                 # only MY artifacts - never the user's
        session.close_document(t, discard_changes=True)
shutil.copyfile(ORIGINAL, WORKING)            # every run starts from the download
```

and close the deliverable again after `SaveAs3`. A job that can be re-run from scratch is what
let the direction bug in 73/74 be found by iteration instead of by one careful guess.

### 76. `ShowNamedView2` needs the **UI language's** view names, and returns True either way

`ShowNamedView2("*Isometric", 7)` returned `True`, `SaveBMP` returned `True`, eight files of
4 410 054 bytes each - and all eight were **byte-identical**. The install is Chinese, so the
names are `*等轴测 / *前视 / *后视 / *左视 / *右视 / *上视 / *下视`. Hash the renders inside the
job; "different sizes" is not a check when every BMP is the same canvas size.

### 77. Do not promise body colours on a foreign multi-body part

Two APIs, two different failures:

| call | returned | effect |
|---|---|---|
| `IBody2.MaterialPropertyValues = arr` | no error | **read-back empty, render unchanged** - inert |
| `IFeature.SetMaterialPropertyValues(arr)` | `True` | took on the extruded collar, **ignored** on the revolve and the second extrusion, which kept the source model's inherited face colours (one contact block renders orange from the front and green from the back) |

Colour on an imported multi-body tree is inherited from the source's face colours and is not
reliably overridable. Say so and hand the user a 10-second manual step, or suppress the
source's colour-carrying features first.

### 78. To change the shape class of a foreign model's front end, add an ENCLOSING body

The tempting route - delete `Revolve1` and re-cut the whole head - is wrong here: `Revolve1`
spans `Y -27..+13`, so it is also the central core of the 中座, and suppressing it guts the
part. What worked instead, with **zero edits to the foreign tree**:

```
source head:  Ø28 x 13 neck at Y 0..13  +  Ø23 x 1 lens at Y 11.5..12.5
new head   :  Ø32 x 4 collar (Y 0..4) + Ø30 x 6 neck (Y 4..10) + Ø36 dome (Y 10..29)
```

Every new radius is larger than the old one it covers, so the old head ends up *inside* the new
one and disappears from every view. Total height landed on **75.000 mm** on the first correct
run, and the hard-won 中座 + contact-block geometry was never touched. Generalises trap 54:
when a rebuild is risky, **enclose rather than replace.**

### 79. Read dimensions off a product photo by finding its own leader lines, and cross-check two scales

A render of a part with printed dimension callouts is measurable without a data sheet:

1. Segment by colour (`red`, `yellow`, `dark`, `blue`) to get each band's vertical run - that
   gives every band's height, and the widest run of each colour gives its diameter.
2. Find the dimension line itself by scanning for the **longest dark run** in a column band
   beside the part (found: `x=119`, 333 px) or in a row band below it.
3. Fix the scale from the labelled dimension, then convert every band.

**Cross-check two scales and report the disagreement.** Here `total = 387 px` against the
printed 75 mm gives 5.16 px/mm, while `flange = 173 px` against the printed 32 mm gives
5.41 px/mm - a 5% conflict in the manufacturer's own artwork. The derived head diameter,
186 px, is 36.0 mm under the first scale and 34.4 mm under the second, so **Ø36 with ±2 mm of
honest uncertainty** is the right thing to build and to say. Quoting one scale to three decimals
would have been false precision.

### 80. Face-level enumeration on a big foreign part dies with `RPC_E_DISCONNECTED`, and it is a dead end, not a flake

`face_dump.py` walked `IBody2.GetFaces()` on a 1 650-face imported frame part and every single
`IFace2` method answered:

```
ERR:(-2147417848, '被调用的对象已与其客户端断开连接。', None, None)
```

`-2147417848` is `RPC_E_DISCONNECTED`. It is **not** a transient COM hiccup and retrying does not
help; the marshalled face proxies were never usable at that count. Two consequences:

- **Cap every face-level probe** (e.g. first 50 faces) and **always record `face_count` first**, so a
  run that returns 1 650 errors still tells you the scale of the part. A probe that prints only
  failures teaches nothing.
- For "is there a bore of radius R" use the *aggregate* route that already worked
  (`holescan1`): walk bodies, ask each face `IsCylinder`, keep `CylinderParams[6]`, and
  **histogram the radii**. That ran 132 s over 38 documents and never tripped the disconnect.
  Per-face classification is the wrong granularity on imported geometry.

### 81. `IsCylinder` face counts are NOT hole counts - one physical bore can be several faces

Measured on the vendor e-stop (`急停按钮[QG115-B8-11ZS]_ZS.SLDPRT`): the R10 bore at
`(0, 61, 0)` around `+Y` reported **two** cylindrical faces, areas `251.3` and `62.8 mm²`.
`251.3 + 62.8 = 314.1 = 2*pi*10*5.0`, i.e. one 5 mm-tall cylinder that SW split into two
faces. Same trap in `000_3.SLDPRT`, one R11.85 face of `335.1 mm² = 2*pi*11.85*4.5`.

So when counting holes: group faces by **radius + axis + collinearity**, then **sum the areas**
and derive the height. Counting faces over-reports holes; and the split is exactly why a
concentric mate picks "a fragment".

### 82. When a bore will not mate, the fix is the axis or a rebuilt hole - never the face

The user's question ("这个圆孔为什么切除是这个形状会导致其他装配体没有办法进行配合") has one
resolution class. Ranked by cost:

| # | fix | cost | when |
|---|---|---|---|
| 1 | **Mate on temporary axes**, not the cylindrical face (视图 → 临时轴; pick face, the axis appears, then 同轴心 axis-to-axis) | 1 min, no model edit | always try first - an axis is analytic and immune to a shredded face |
| 2 | **Rebuild as a Hole Wizard hole** (异型孔向导, 完全贯穿) | one feature | when the model is yours / may be edited |
| 3 | **Fix the cut direction**: sketch the circle on the plane the hole is normal to (insert a 基准面 on a slanted/curved host face first), cut 完全贯穿-两者 | one sketch + feature | original feature must be kept |
| 4 | **Heal the face**: 插入 → 面 → 删除面 → 删除并修补, which stitches split fragments back into one cylinder | minutes | the split came from a chamfer/counterbore crossing the bore |

Diagnosis in 10 s before choosing: select the bore face in the assembly and read the status bar.
A radius (e.g. `R11.00`) means it is a true cylinder - use route 1. "样条曲面", or an impossible
single full-circle selection, means the bore is already degenerate - use 2 or 4.

### 83. For a Chinese electrical component, the vendor's own STEP library beats every CAD portal - and it is scriptable

CHINT (正泰) publishes **三维模型图** as `.stp` for hundreds of catalogue numbers, free and without an
account, under 资料中心:

- Browse: `https://www.chint.net/service/download/type` → 文档类型 = 三维模型图
  (the list pages carry `type/20%2C21%2C72%2C143`, and `p/N.html` paginates).
- Each row's `直接下载` points at `https://ztkbs.chint.com/kbs/upload/piecewise-file/<uuid>/<uuid>
  ?expires=<b64 unix ts>&signature=<b64 hmac>&platformCode=...`. The pair is **signed and
  short-lived** - `expires=MTc5MDE0Njc3Mg==` decodes to `1790146772`, i.e. ~3 days. Download
  immediately and hand the user the *page*, not the signed URL.

Measured this session, all three fetched with `curl.exe` straight from PowerShell:

| file | bytes | content |
|---|---|---|
| `NB1-63H 1P&N小型断路器三维模型202404.stp` | 7 476 919 | 5 solids, 4 391 faces |
| `NB5LE-63FB 1P&N剩余电流动作断路器三维模型202409.stp` | 19 352 731 | 57 solids (multi-variant layout, not one device) |
| `NXHB-125 2P隔离开关三维模型202307.stp` | 5 178 186 | 18 solids, 3 025 faces - clean **80.5 x 36.05 x 76.96 mm** |

Two things to carry forward:

1. **`MANIFOLD_SOLID_BREP` / `ADVANCED_FACE` counts and the `CARTESIAN_POINT` bounding box are
   readable from the STEP text with one regex**, no CAD needed - and they tell you whether you got
   one device or a catalogue sheet. The 57-solid RCBO measured `311 x 240 x 250 mm`, which is the
   tell that it is *not* a single 36 mm module; the 2P isolator measured a textbook
   **80.5 x 36 x 77 mm** (DIN 43880: 18 mm per module, ~80 mm tall). **Always measure before
   handing a downloaded STEP to a user as "the same part".**
2. **`web_fetch` reaches `chint.net` only through the JS-rendering reader.** A plain fetch returns
   `403 ... Denied by custom_acl`, and `www.chint.net` even failed DNS once. `read_page` got the
   full list including the signed links. Portal sites behave the same way: `grabcad.com` and
   `traceparts.com` both answered `403` (CloudFront / WAF) to a plain fetch but render fine through
   `read_page`. Reference prices also hold: GrabCAD CHINT MCB eBG C10 single pole (STEP+IGES, 1 826
   downloads) and 3DContentCentral's 16 A DIN-rail breaker are free-with-registration only - there
   is **no CC0/MIT-licensed miniature circuit breaker CAD** anywhere, so never call one "open source".

### 84. "The vendor publishes CAD" generalises by product LINE, not by brand - and KCD rockers live on GrabCAD

Asked for a model of a **KCD rocker switch** (红翘板/船型开关, grey bezel, 6.3 mm Faston tails), the
正泰 route that worked for the MCB (trap 83) **fails here**: CHINT resells a KCD4 30 A rocker for
welders, but its 资料中心 三维模型图 set covers its own industrial-control catalogue (HZ5 组合开关,
YBLX 行程开关, NP 按钮, NB1/NXHB/NB5LE breakers) and carries **no KCD 船型开关**. Check the
*product line* against the download centre before promising a vendor STEP.

Where the free supply actually is, measured this session:

| source | KCD coverage | cost |
|---|---|---|
| **GrabCAD** `tag/rocker switch` (2 pages) | best by far: **KCD4 30x25 / opening 28x22** (`KCD4.step`, `KCD4_black.step`; modelled from a real switch, **31.10 x 25.5 mm**, outer lip + snap clips + Faston); KCD1-104 15x21; KCD1-105 3-pin; KCD1 round 22.5; KCD11 round 16.5; KCD4-203; plus several generic 2-pin | free account |
| 3DContentCentral | `KCD11` (catalog 171, id **208370**) and a Chinese `220V船型开关` (id **1400914** on the `.cn` mirror) | free account |
| McMaster-Carr | rocker category exists and its CAD is login-free | **grid is JS-only**: `read_page` returned nothing but the BROWSE CATALOG shell - harvest part numbers through their SolidWorks add-in/API, not by scraping |
| 宏图网 | `船型开关KCD1-11` stp + x_t | **10 金币**, and the page footer said 图纸不存在或已下架 |
| 开拔网(sanweimoxing) / 爱给网 | 船形开关 entries exist | membership / 金币 |

Two rules from this: (1) on Chinese CAD sites **"免费下载" in the marketing copy does not mean free** -
read the 所需金币 / VIP tier before sending a user there; (2) GrabCAD's new uploads are genuinely
high quality (a 2026 KCD4 modelled to 31.10 mm with a size drawing beats anything on the paid
sites), so search it *by component family name* (`KCD1`, `KCD4`, `KCD11`) rather than by
"rocker switch" alone - the family code is what the uploaders title with.

### 85. The 产品资料 tab hides eight document kinds under signed links - only one of them is geometry, and CHINT's STEP scale is not consistent

Same vendor, pilot lights this time: `chint.net/products/91.html` (ND16系列信号灯) lists 三维模型图 for
`ND16-16D`, `ND16-22S`, `ND16-22BK`, `ND16-22LC`, ... With `read_page` the page text is truncated
at 50 000 chars, but the **Links section keeps every signed `piecewise-file` URL**, and its order
matches the visible section order:

```
产品样本 -> 认证证书 x2 -> 试验报告 x3 -> 使用说明书 -> 三维模型图 -> 外形安装尺寸图源文件
```

So the tail of the link list is the last two tabs - and the last group is 外形安装尺寸图源文件,
i.e. **drawing source files with zero solids** (`2ZTT-659-*`, 2.2-7.5 MB, bbox ~139 x 111 x 62).
Blind-downloading the tail cost a round here; one of them even came back **0 bytes from HTTP 200**.
Always count `MANIFOLD_SOLID_BREP` before believing a downloaded file is a part.

Measured, with the unit taken from the file's own `SI_UNIT`:

| file | unit | solids | bbox | what it is |
|---|---|---|---|---|
| `ND16-16D信号灯三维模型202204.stp` | `.MILLI.,.METRE.` | 1 | **18.89 x 45.71 x 26.61** | Ø16 pilot light, front ring Ø18.9, 45.7 long |
| `ND16-22BK信号灯三维模型202204.stp` | mm | 1 | **28.98 x 28.99 x 52.21** | Ø22 pilot light, front ring Ø29 |
| `NB1-63H 1P&N...三维模型202404.stp` | (m) | 5 | 0.09 x 0.04 x 0.22 | same catalogue, **metres** |

**CHINT's STEP exports are not unit-consistent across products** - parse `SI_UNIT` or cross-check one
known dimension before scaling; do not carry a scale over from the previous file. And note the
refinement to trap 84: CHINT's 三维模型图 set *does* cover 主令电器 (NP2/NP8 按钮, ND16 信号灯,
HZ5/YBLX 开关) - it is only the bought-in **KCD rocker** that is missing. Check the product line,
per trademark, not per brand.

## Process: what this project cost, and how to run the next one

Honest accounting, because the API traps above are only half the lesson.

**Where the time actually went.** Roughly: a third on network dead ends (model sites
either require a login or are unreachable from this machine), a third on four API
semantics that a single 20-second probe each would have settled, and a third on real
work. The expensive mistakes were all *ordering* mistakes, not knowledge gaps.

1. **Never guess a semantics - measure it with the smallest possible part.** The arc
   direction, the extrusion flip, the start offset and the multi-contour limit each
   burned several full build/verify cycles because the question was asked of the
   1 000-line model instead of a 20 mm square. A probe part that builds in 4 seconds
   answers it once, permanently. Two probes (`probe_corner.py`, `probe_group85.py`)
   each ended a multi-round argument immediately.
2. **Look for the vendor's own CAD before hunting a mesh.** One blocked website later,
   the EAGLE file gave exact outline, holes, footprints, placements *and rotations* -
   and it parses as XML. A downloaded STL would have been strictly worse (mesh, no
   datums, unknown provenance).
3. **Compute the analytic answer first, and assert it.** Five of the bugs found here
   were found by arithmetic and none by looking at the render: the wrong arc centre
   (0.5708 mm²), the buried button cap (9.651 mm³), the multi-body volume convention
   (34.58 mm³), the 11 %-short STL, and the empty exports. Every one of them would
   have shipped as a plausible-looking model.
4. **Verify the artifact you actually hand over.** A solid that measures right can
   still export wrong: the STEP and STL both contained one body out of 36. Parse the
   exported files - STEP entity counts, STL edge-use histogram and divergence-theorem
   volume - instead of trusting file size or the export return code.
5. **Make the job re-runnable before the first retry.** Close the previous document
   with the same target name, clear the selection, never depend on session state you
   did not set. The last runs of this job were single 45-second invocations with
   eleven assertions and no manual steps.
6. **Write down the trap the moment it costs you something**, with the measured
   number. Every §19-28 above carries its evidence; the number is what makes the next
   agent believe it instead of re-testing.

