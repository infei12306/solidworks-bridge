---
name: solidworks-bridge
description: Control an already-running SOLIDWORKS session from DSH over Windows COM automation - run Python jobs against the live model (build sketches and features, measure mass properties, export STEP/STL/IGES/PDF/Parasolid/3MF, open and close documents), capture the graphics area as PNG, and recover a session wedged on a modal dialog. Use when a task involves SOLIDWORKS or SolidWorks, .SLDPRT/.SLDASM/.SLDDRW files, CAD modelling, mass properties or exports, or when SOLIDWORKS is open and the work should land in that live session.
---

# SOLIDWORKS Bridge

Drive the user's **live** SOLIDWORKS session on Windows. Nothing is clicked:
commands go in over COM, data comes back through files, and the console only
receives a summary.

SOLIDWORKS must be running (or start it with `launch`). The tooling is plain
Python - pywin32, no add-in, no VBA.

## Locate the driver

```powershell
$SW = Join-Path $env:LOCALAPPDATA 'swbridge\swbridge.py'
if (-not (Test-Path $SW)) {
    # not deployed yet: install from this skill bundle, then read its DRIVER line
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install.ps1"
}
```

Invoke it as:

```powershell
& 'C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe' $SW <command>
```

`doctor` first if anything looks wrong: it reports the Python build, the
SOLIDWORKS version, the type-library mismatch, the Running Object Table and
whether a session can be attached.

| Goal | Command |
|---|---|
| Sanity check | `doctor` |
| Report the session | `attach [--verbose]` |
| Start SOLIDWORKS | `launch [--wait 240]` |
| **Run code** | `run --file job.py` or `run --code "..."` |
| Slow job, return at once | `run --code "..." --async` → then `status --id <id>` |
| Open a document | `open <file.sldprt>` |
| Summarise the active doc | `info` |
| Export, verified | `save <path> --as step\|stl\|iges\|pdf\|x_t\|3mf` |
| See the model view | `shot [--out x.png]` |
| Close documents | `close --all` |
| Exit SOLIDWORKS | `quit [--force]` |
| Look up a real signature **offline** | `api IPartDoc` / `api GetBodies2` |
| Look up a constant **offline** | `enum swFileSave` |
| Which name is a property? | `api IModelDoc2 --props` |
| Find / answer a modal dialog | `dialogs` / `dismiss --button cancel` |

## Job scripts

`run` injects these names; nothing has to be imported:

`app` (ISldWorks) · `doc` (IModelDoc2 or None) · `session` · `L` (interface
classes) · `E` (8199 constants) · `cast` · `getv` · `call` · `call_out` ·
`OUTARG` · `retry` · `OUT` (write your outputs here) · `HOME` · `log`

[scripts/../../tests/jobs/make_box.py](../tests/jobs/make_box.py) is a worked
example: 50x30x20 mm box → mass properties → save → render.

## Hard rules

1. **Lengths are METRES, mass is KILOGRAMS, angles are RADIANS.** A 50 mm box
   is `0.05`. This is the commonest source of a silently wrong model.
2. **`cast()` anything that comes back from `ActiveDoc`, `GetFirstFeature`,
   `GetNextFeature` and friends.** Those return *late-bound* objects (the type
   library gives no CLSID), and on a late-bound object pywin32 silently resolves
   an unknown name with PROPERTYGET - so `feature.GetTypeName2` is already the
   string `'RefPlane'` and calling it raises
   `TypeError: 'str' object is not callable` with no hint of the cause. There is
   no safe way to infer the interface at runtime, so name it explicitly:
   `cast(feature, 'IFeature')`.
3. **Reads go through `getv()`, actions through `call()`.** `GetTitle` and
   `FirstFeature` are methods; `Extension`, `SketchManager` and `FeatureManager`
   are properties. `call()` refuses a property and tells you to use `getv`. Check
   any name with `api <Interface> --props`.
4. **`[in,out]` parameters go through `call_out()` with `OUTARG`.** pywin32 does
   not write back into what you pass - it appends the out values to the return
   value: `props, (status,) = call_out(ext, 'GetMassProperties2', 1, OUTARG, False)`.
   Every other spelling fails (`VARIANT(VT_BYREF|VT_I4)`, a list, `Missing`).
5. **`SaveAs3` returns an error CODE, not a bool: 0 means success.** The format
   comes from the extension, the version must be `0`
   (`swSaveAsCurrentVersion`) and the options must include
   `swSaveAsOptions_Silent`. Prefer `save`, which verifies the file afterwards.
6. **Never close a modified document without `SetSaveFlag()` first.** `CloseDoc`
   on a dirty document opens a "save changes?" dialog and the COM call then
   never returns. There is no timeout. `close`/`close_document` handle this.
7. **A modal dialog wedges its caller forever - and this is the one failure
   with no automatic recovery.** `dialogs` shows it, `dismiss --button cancel`
   answers it (Cancel is the default on purpose: OK on a save prompt would
   overwrite the file). While a modal is up, *other* clients keep being served
   **re-entrantly rather than rejected** (measured), so do not hammer the API
   from a second process in that state - it runs inside the modal loop.
8. **Use `--async` for anything slow,** then poll `status`. A synchronous call
   blocks the tool's own timeout, and the bridge cannot interrupt a COM call.
9. **The UI may be Chinese.** 'Front Plane' is `前视基准面`, an extrude is
   `凸台-拉伸1`. Never look reference geometry up by name: walk the tree with
   `FirstFeature`/`GetNextFeature` and match `GetTypeName2() == 'RefPlane'`.
10. **Do not try to fix the type library registration.** On this install the
    registry advertises version 21.0 while the `.tlb` files contain 33.0, so
    `EnsureModule`/`EnsureDispatch`/`LoadRegTypeLib` all fail with
    `TYPE_E_LIBNOTREGISTERED`. `genstubs.py` reads the `.tlb` files directly and
    the bridge works around it; leave the registry alone.
11. **Verify exports by structure, not by existence.** `save` already does:
    STEP must start with `ISO-10303-21`, STL must satisfy `84 + 50*triangles ==
    size` (a 50x30x20 box is exactly 12 triangles), PDF must end with `%%EOF`.
12. **Jobs must not quit SOLIDWORKS or discard the user's work.** Close what you
    opened; never `quit` unless asked (and then `--force` is required while
    documents are open, because it discards unsaved changes).
13. **Sketch create-methods take SKETCH-LOCAL coordinates and ignore the third
    argument.** `CreateCircleByRadius(XC, YC, Zc, R)` puts the circle at sketch
    `(XC, YC)`; measured local axes: front `(X, Y)`, top `(X, -Z)`, right
    `(-Z, Y)`. Two wrong guesses were needed to find that. **Never trust it** -
    read the result back off the body (see 15).
14. **Never select existing geometry by coordinate.**
    `SelectByID2("", "EDGE", x, y, z)` is a hit test in the *current view*: it
    picked 3 of 4 hole rims and silently failed on the one facing away. Find the
    entity from `IBody2.GetEdges()` / `ICurve.IsCircle()` / `CircleParams` and
    select it with `cast(edge, "IEntity").Select2(True, 0)` - `IEdge` does not
    expose the inherited `IEntity` members.
15. **Verify a model from the solid, not from the API's return value.** Feature
    calls return `None` on failure and `True`/a feature on success - and a
    "successful" feature can still be wrong geometry. Read volume, surface area
    and `GetBodyBox()` back and compare against values you computed first; check
    hole axes from the cylindrical faces. Use `swcore.iter_features()` instead of
    calling `FirstFeature`/`GetNextFeature` yourself.
16. **For a countersink use `IFeatureManager.InsertFeatureChamfer(Options,
    ChamferType, Width, Angle, OtherDist, 0, 0, 0)`.** The legacy
    `IModelDoc2.FeatureChamfer(Width, Angle, Flip)` returns `None` on this build,
    as does a drafted blind cut (tried with every sign and flip). A chamfer on a
    circular rim IS a countersink - verified: Ø6.5 + 2 x 2.75 = Ø12 at 90 degrees,
    with volume and area matching the analytic values to 0.0000 %.
17. **A failed feature leaves its sketch OPEN, and `InsertSketch` is a TOGGLE.**
    The next call then closes the open sketch instead of opening a new one, so
    every retry after the first tests nothing. Check `getv(SketchManager,
    "ActiveSketch")` and exit it before starting a new sketch.
18. **Reconcile against arithmetic you have checked, not just against the model.**
    The first correct bracket was reported as wrong because the expectation
    double-counted the bore under the countersink. The check is only as good as
    the number it compares to.
19. **`doc` is the USER's document, not "None when there is nothing open".** Start
    every job with `NewPart` and prove it worked:
    `int(getv(cast(getv(app,"ActiveDoc"),"IModelDoc2"), "GetType")) == 1`. A job
    that trusted `if doc is None` wrote a sketch into the user's open assembly;
    removing it afterwards is strictly worse than checking first. See MODELING.md
    §19.
20. **`SetAddToDB(True)` before creating sketch entities, `False` before the
    feature.** Without it automatic relation inference *deletes arcs and merges
    entity chains*: measured, a 9-line + 3-arc outline became 9 straight lines and
    the profile was 48.9 mm² wrong. Always log
    `GetLineCount`/`GetArcCount`/`GetSketchContourCount` back off the sketch.
21. **`CreateArc(..., Direction=True)` is counter-clockwise**; a clockwise corner
    passed `True` becomes the **270° major arc**. Choose per arc from the shorter
    sweep (`forward = normalise(a1 - a0) > 0`), and *derive* each arc centre from
    its endpoints instead of guessing it - a centre on the corner point is a notch,
    not a rounded corner (cost 0.5708 mm² here). MODELING.md §21.
22. **To start a boss away from the sketch plane use the start condition, not
    `Flip`.** `Flip` is a no-op for a blind boss on the front plane, `Dir` means
    *both directions*, and a negative depth returns `None`. The working recipe is
    `T0=3` (`swStartOffset`) + `StartOffset=mm(start)`. Cuts: use
    `FeatureCut4(Sd=False, T1=T2=1, ...)`, through-all both ways, which cannot miss
    for want of a sign.
23. **`ClearSelection2` immediately before every export.** A freshly created feature
    stays selected and `SaveAs3` exports **only the selection**: measured, an STL
    of 200 triangles and a STEP of 11 807 bytes from a 36-body part, with no error
    code. Then verify the file by parsing it - `MANIFOLD_SOLID_BREP` count for STEP,
    edge-use histogram plus divergence-theorem volume for STL.
24. **Keep components as separate bodies (`merge=False` in `FeatureExtrusion3`).**
    It is what an assembly wants, and it is the only way to a watertight STL:
    merged, a face with ~40 inner loops exported with 641 open boundary edges and a
    volume 11 % short. Multi-body also changes the volume bookkeeping - fully
    buried solids count in full.
25. **Use the injected `doc`; it is already early-bound. `app.ActiveDoc` is not.**
    The preamble says so and it is not cosmetic. On the late-bound `CDispatch` that
    `app.ActiveDoc` returns, a zero-argument method such as `FirstFeature` is
    resolved with PROPERTYGET and raises `DISP_E_MEMBERNOTFOUND` - so a hand-rolled
    walk of the feature tree fails on an assembly it can otherwise read fine. If
    you must fetch the document yourself:
    `cast(app.ActiveDoc, "IModelDoc2")`. Measured: `type(doc).__name__` is
    `IModelDoc2` for the injected handle and `CDispatch` for `app.ActiveDoc`.
26. **Assembly-level calls live on `IAssemblyDoc`.** `GetComponents` is *not* on
    `IModelDoc2`: the early-bound object raises `AttributeError ... has no
    attribute 'GetComponents'` while the late-bound one answers it, which makes the
    failure look random. `cast(app.ActiveDoc, "IAssemblyDoc")`. In the same spirit,
    `FeatureByName` is not on `IModelDoc2` either - reach a folder through
    `iter_features`.
27. **VARIANT-array returns are plain tuples, not `.Count`/`.Item` collections.**
    `GetComponents(False)` hands back a tuple (pywin32 converts the array), and so
    does `IComponent2.GetXform()`. Write one
    `as_list(coll)` helper that accepts either, instead of assuming `.Count` -
    `'tuple' object has no attribute 'Count'` costs a whole job cycle.
28. **`IComponent2.GetXform()` layout is 16 doubles:** `[0:9]` = 3x3 rotation
    row-major, `[9:12]` = translation **in metres**, `[12]` = scale. Slicing
    `[4:7]` and `[8:11]` as if they were rows yields a plausible-looking nonsense
    matrix (it reported a translation in the rotation block). There is no
    `.ArrayData` on the result - it already *is* the array.
29. **Read `IComponent2.GetConstrainedStatus()` before telling anyone a part can be
    dragged.** Measured constants: `swUnderConstrained=2`, `swFullyConstrained=3`,
    `swOverConstrained=4`. A component can be unfixed and still immovable because
    its mates fully define it (that is exactly what happened here: the plate was
    floating but status 3, the board floating and status 2). `IsFixed()` alone
    cannot answer "why won't it move"; you need both numbers plus the mate list.
30. **Check that the *installed* `swcore` is the repo's `swcore`.** The install at
    `%LOCALAPPDATA%\swbridge\sw\` is a copy, not a clone, and it lags: on
    2026-09-15 it had neither `iter_features` nor `reference_planes` while the repo
    had both. Rule 15 tells you to call a helper the running code may not define.
    One line at the top of a job is the whole staleness test -
    `hasattr(swcore, "iter_features")` - and the fix is copying
    `sw/swcore.py` from the repo over the installed copy.

Worked examples and the full trap list: [MODELING.md](MODELING.md) - its
[second part](MODELING.md#second-part-a-36-body-board-from-a-vendor-cad-file) carries
traps 19-28 with the measured numbers, and its closing section is the process
post-mortem (probe before you build, compute the answer first, verify what you hand
over). Runnable examples: [tests/jobs/make_box.py](../tests/jobs/make_box.py),
[tests/jobs/make_bracket.py](../tests/jobs/make_bracket.py) and the MEGA 2560 job at
`D:\桌面文件\workspace\arduino-mega2560\build_mega.py`.

## Workflows

**Model something and report on it**
1. `run --file build.py` (create the part, then call `save` targets and `info`
   style measurements inside the same job - one job is far cheaper than several).
2. Read `STATE: OK` and the log tail. On `STATE: ERR` the traceback is in the
   status file and the last lines are in the log.

**Export and check**
1. `run --code "..."` to build or `open <file>`
2. `save D:\out\part --as step` (repeat per format; `--as` is optional when the
   extension is one of the six)
3. `check` in the output is the structural verdict, e.g. `12 triangles, 684 bytes`.

**Look at the model**
1. `shot --out D:\out\view.png` (renders the graphics area via `SaveBMP`: no
   window chrome, no focus stealing, works while occluded)
2. Inspect the PNG.

**Build a part from a vendor CAD file (EAGLE/KiCad/STEP source)**
1. Convert the source into a plain table first (`board.brd` -> JSON -> a Python
   literal of axis-aligned boxes in mm). Never hand-transcribe coordinates, and
   generate the documentation from the same table so the two cannot drift.
2. Compute the analytic answers (profile area in closed form, volumes, envelope,
   hole positions) **before** building; they are the assertions.
3. Build in one job: PCB outline + holes -> one sketch+feature per component with
   `T0=3 + StartOffset` -> measure -> `ClearSelection2` -> save SLDPRT/STEP/STL with
   `SaveAs3` (then re-save the part, see trap 27) -> parse the exports -> render.
4. One job, one document. Close a previous document with the same target name at
   the start so the job is re-runnable.

**Inspect an assembly the user already has open (read-only)**
1. Change nothing: no `NewPart`, no feature, no save, no close. Read `doc` (the
   injected one - rule 25), never `app.ActiveDoc`.
2. Walk with `swcore.iter_features(doc)`; components come back as features of type
   `Reference`, and the mate folder is the `MateGroup` (its name is `配合` on a
   Chinese UI). Take its children with `GetFirstSubFeature`/`GetNextSubFeature`,
   casting each to `IFeature`.
3. Per component: `cast(comp, "IComponent2")` → `Name2`, `IsFixed`,
   `GetConstrainedStatus`, `GetPathName`, and `GetXform` for the placement.
4. Per mate: `GetSpecificFeature2` → `cast(..., "IMate2")` →
   `GetMateEntityCount`/`MateEntity(k)` → `cast(..., "IMateEntity2")` →
   `ReferenceComponent` (cast to `IComponent2` for the name). `IMateEntity2`
   exposes `Reference` but **not** `Entity`/`EntityType`, so the face/edge a mate
   uses is not directly readable - report the component pairs and be explicit that
   the DOF count is inferred, or read `GetConstrainedStatus` instead of guessing.
5. Write the report to a file under `OUT` (a **directory path** - rule 27's sibling
   gotcha: `OUT["k"] = v` raises `'str' object does not support item assignment`);
   the job's `log()` output is not always surfaced, a file on disk always is.

**Something is stuck**
1. `status --id <id>` → `RUNNING` with a flat log means the call never returned.
2. `dialogs` → is there a MODAL window?
3. `dismiss --handle <h>` (or no handle: it picks the modal one), then `status`
   again.
4. If the session itself looks broken, `attach` and `doctor` are read-only and
   safe; `quit --force` is the last resort.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `no running SOLIDWORKS automation object` | Not running, or still starting. `launch`, or check the screen for a license dialog. |
| `member not found (DISP_E_MEMBERNOTFOUND)` | A late-bound object, or a property called as a method. `cast(...)` / `getv(...)`. |
| `DISP_E_MEMBERNOTFOUND` on `FirstFeature` only | You are holding `app.ActiveDoc` (late-bound). Use the injected `doc` (rule 25). |
| `IModelDoc2 ... has no attribute 'GetComponents'` | It is on `IAssemblyDoc` (rule 26). |
| `'tuple' object has no attribute 'Count'` | VARIANT array, not a COM collection (rule 27). |
| `'tuple' object has no attribute 'ArrayData'` | `GetXform()` already *is* the 16 doubles (rule 28). |
| `'str' object does not support item assignment` | `OUT` is a directory path, not a dict - write files into it. |
| `hasattr(swcore, "iter_features")` is False | The installed bridge is stale; copy `sw/swcore.py` from the repo (rule 30). |
| "why won't this component drag?" | `GetConstrainedStatus()`: 2 = under-constrained, 3 = fully defined (rule 29). |
| `'str' object is not callable` | Same cause: the name is a property on that path. Use `getv`. |
| `type library not registered` | Something used gencache. Run `genstubs.py`; never `EnsureDispatch`. |
| `[<step>] SOLIDWORKS is busy ...` | `retry_call` gave up. Retry the job; if it persists, look for a progress dialog. |
| Job stuck at `RUNNING`, log flat | A modal dialog. `dialogs` then `dismiss`. |
| `save` fails with `SaveAs3 returned 256` | That translator is not installed (measured for `.OBJ`). |
| `info` shows `bodies: 0` | The part has no solid body (surface-only, or suppressed features). |
| Sketch has 0 arcs / fewer lines than you drew | Automatic relations ate them. `SetAddToDB(True)` (trap 20). |
| A rounded corner comes out as a notch | The arc centre is wrong or `Direction` is the wrong sense (trap 21). |
| STEP/STL far too small, no error reported | Only the selected body was exported (trap 23). |
| STL not watertight, volume short, all open edges on one plane | Merged model with a many-loop face; rebuild multi-body (trap 24). |
| Several "different" renders are byte-identical | The numeric view id overrode the view name (trap 28). |

Design decisions, the measured numbers, and the full pitfall log:
[REFERENCE.md](REFERENCE.md).
