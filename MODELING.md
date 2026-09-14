# Modelling with the bridge - what works, what does not, and the traps

Everything here was measured on SOLIDWORKS 2025 SP5 (33.5.0.0053), Chinese UI,
through this bridge. Nothing is copied from documentation that was not also
verified against the live session.

## Status of the two parts that have been built

| Part | State |
|---|---|
| `tests/jobs/make_box.py` - 50x30x20 mm block | **Complete.** Volume, area, centroid, STL triangle count and render all reconcile. |
| `tests/jobs/make_bracket.py` - L bracket, 80 + 60 legs, 5 mm plate, 40 mm wide, four 6.5 mm countersunk holes | **Complete.** Volume and surface area match the analytic values to **0.0000 %**, all four hole axes verified from the body, envelope exactly 80 x 60 x 40 mm, STEP + STL + two renders produced. |

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
