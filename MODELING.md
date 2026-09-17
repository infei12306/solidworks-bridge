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

## Sixth part: 172 centre points on two boards, and the point-creation flake

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

