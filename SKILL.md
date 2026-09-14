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
| `'str' object is not callable` | Same cause: the name is a property on that path. Use `getv`. |
| `type library not registered` | Something used gencache. Run `genstubs.py`; never `EnsureDispatch`. |
| `[<step>] SOLIDWORKS is busy ...` | `retry_call` gave up. Retry the job; if it persists, look for a progress dialog. |
| Job stuck at `RUNNING`, log flat | A modal dialog. `dialogs` then `dismiss`. |
| `save` fails with `SaveAs3 returned 256` | That translator is not installed (measured for `.OBJ`). |
| `info` shows `bodies: 0` | The part has no solid body (surface-only, or suppressed features). |

Design decisions, the measured numbers, and the full pitfall log:
[REFERENCE.md](REFERENCE.md).
