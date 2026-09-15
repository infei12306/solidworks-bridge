# Modelling with the bridge - what works, what does not, and the traps

Everything here was measured on SOLIDWORKS 2025 SP5 (33.5.0.0053), Chinese UI,
through this bridge. Nothing is copied from documentation that was not also
verified against the live session.

## Status of the parts that have been built

| Part | State |
|---|---|
| `tests/jobs/make_box.py` - 50x30x20 mm block | **Complete.** Volume, area, centroid, STL triangle count and render all reconcile. |
| `tests/jobs/make_bracket.py` - L bracket, 80 + 60 legs, 5 mm plate, 40 mm wide, four 6.5 mm countersunk holes | **Complete.** Volume and surface area match the analytic values to **0.0000 %**, all four hole axes verified from the body, envelope exactly 80 x 60 x 40 mm, STEP + STL + two renders produced. |
| 36-body Arduino MEGA 2560 Rev3, built from the vendor's own EAGLE board file (`D:\桌面文件\车架复刻交付\arduino-mega2560\build_mega.py`) | **Complete.** Board outline area, board volume, whole-part volume, envelope and all six mounting-hole axes match analytic values exactly; STEP carries 36 solids; STL is watertight. See [§ Second part](#second-part-a-36-body-board-from-a-vendor-cad-file). |
| 41-body 16-channel 12 V relay board, dimensions triangulated from a flat vendor photo because no vendor CAD exists (`D:\桌面文件\workspace\relay-board\build_relay_board.py`) | **Complete for the modelled scope.** PCB volume and total volume match analytic to **0.0000 %**, 41 bodies, envelope exactly 179 x 90 x 16.6 mm, 70 terminals asserted (48 relay outputs + 20 input header + 2 power poles), control parts proven non-overlapping and inside their strip, 4/4 mounting-hole rims found on the body, STEP carries 41 solids, STL watertight (1476 triangles, 0 open edges). See [§ Third part](#third-part-a-41-body-relay-board-where-the-input-had-to-be-reconstructed). |

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

## Second part: a 36-body board from a vendor CAD file

The Arduino MEGA 2560 Rev3 (built in `D:\桌面文件\workspace\arduino-mega2560\`,
result: 101.6 x 53.34 mm board, 34 components, 36 separate bodies). Unlike the box
and the bracket, the geometry was **not invented** - it was converted from the
vendor's own EAGLE board file, which changes the shape of the whole job: every
coordinate is given, so the entire risk moves from "is the design right" to "did
the API do what I asked". Nine more traps came out of it.

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

## Third part: a 41-body relay board where the *input* had to be reconstructed

Same recipe, different failure mode - here the vendor CAD did not exist and the
dimensions had to be argued from four independent sources before a single feature
was built. Job: `D:\桌面文件\workspace\relay-board\build_relay_board.py`. Made in one
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
2-pole power block and two electrolytic cans. The build asserts 48 + 20 + 2 terminals.

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

