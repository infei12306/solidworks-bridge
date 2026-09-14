# solidworks-bridge - reference

Design decisions, measured numbers and the pitfall log. Everything here was
measured on the machine this was built for; nothing is copied from
documentation that was not also verified against the live session.

## Environment it was built against

| Item | Value |
|---|---|
| SOLIDWORKS | 2025 **SP5.0**, `33.5.0.0053`, UI language **Chinese** |
| Install path | `D:\Mango\sw2025\SOLIDWORKS Corp 2025\SOLIDWORKS\` (not on C:) |
| ProgID | `SldWorks.Application.33` (there is no `.31`/`.32` here). The suffix is the SOLIDWORKS major version; 2025 = 33 |
| CLSID | `{6AF263BB-EB9F-4176-89E9-4F892EB0CA3D}`, LocalServer32 = `sldworks.exe` |
| ROT entries | both `SolidWorks_PID_<pid>` and `!{6AF263BB-...}` |
| Python | 3.11.9, 64-bit, pywin32 build 312 |
| Type libraries | `sldworks.tlb` LIBID `{83A33D31-27C5-11CE-BFD4-00400513BB57}`, `swconst.tlb` LIBID `{4687F359-55D0-4CD3-B6CF-2EB42C11F989}` |

## Why Python, and why there is no VBA

SOLIDWORKS exposes the whole object model as dual `IDispatch` interfaces, so any
COM client can drive it. That makes Python the *injection language*: a job script
is executed in-process against the live session, and there is no need to generate
`.swp` macro files (which are OLE compound documents and would be fragile to
author programmatically). `RunMacro2` remains available for a user's existing
macros but nothing depends on it.

PowerShell was rejected as the driver despite the sibling `matlab-bridge` using
it: pywin32 is required anyway, and staying in one language removes a whole layer
of quoting between PowerShell and Python - which on this machine (Chinese paths,
parentheses) has repeatedly caused real failures.

## The type library mismatch - the reason this tool exists in this shape

The registry advertises the SOLIDWORKS type libraries under version **21.0**
(win64) and **1.0** (win32), while the `.tlb` files themselves contain version
**33.0**. COM validates the version you ask for against the version embedded in
the file, so every registry-based lookup fails:

```
pythoncom.LoadRegTypeLib('{83A33D31-...}', 21, 0, 0)   -> TYPE_E_LIBNOTREGISTERED
pythoncom.LoadRegTypeLib('{83A33D31-...}', 33, 0, 0)   -> TYPE_E_LIBNOTREGISTERED
gencache.EnsureModule('{83A33D31-...}', 0, 21, 0)      -> TYPE_E_LIBNOTREGISTERED
gencache.EnsureDispatch('SldWorks.Application.33')     -> TYPE_E_LIBNOTREGISTERED
[Runtime.InteropServices.Marshal]::GetActiveObject(...) -> TYPE_E_ELEMENTNOTFOUND
```

whereas

```
pythoncom.LoadTypeLib(r'...\sldworks.tlb')             -> works, 1015 types
```

`scripts/genstubs.py` therefore reads the `.tlb` files **by path**, generates the
makepy stubs (6.27 MB of interface classes + 688 KB of enums, in ~2 s), and loads
them from a private directory. Consequences worth knowing:

* `win32com.client.gencache` and `%TEMP%\gen_py` are **never** used, so nothing
  breaks when Windows cleans the temp directory.
* attaching is `ProgID -> CLSID` (the only registry lookup, and that entry is
  fine) then `pythoncom.GetActiveObject(CLSID)`, which is a pure Running Object
  Table query.
* the 8199 enum constants are available by their real names
  (`swOpenDocOptions_Silent`, `swMbOkCancel`, ...). `swapi.py --props` can also
  list which members are properties.

**Do not "fix" the registry.** The workaround is deliberate and the mismatch is
reported by `doctor`.

## COM conventions, all measured

| Question | Answer |
|---|---|
| Attach cost | **13-91 ms** with pywin32. .NET's `Marshal.GetActiveObject` fails outright here |
| Version string | `RevisionNumber()` returns `'33.5.0'`, not `33` |
| `ActiveDoc` binding | **late-bound** - no CLSID in the type library |
| `GetTypeInfo()` on SW objects | fails, `DISP_E_BADINDEX (0x8002000B)` - so the interface cannot be discovered at runtime, it must be named |
| Properties vs methods | `Extension`/`SketchManager`/`FeatureManager` are properties; `GetTitle`/`GetPathName`/`GetType`/`FirstFeature`/`GetTypeName2` are methods **on the early-bound class**. On a late-bound object the same name resolves to its value instead |
| `Feature.GetTypeName2` timing | measured as a *property* on one path and a *method* on another, which is exactly why `getv()`/`call()` exist |
| `[in,out]` params | pywin32 appends them to the return value; `VARIANT(VT_BYREF|VT_I4)`, `[0]` and `pythoncom.Missing` all fail. Use `OUTARG` + `call_out` |
| `SaveAs3` return | `VT_I4` **error code**: 0 = success, 256 = invalid extension/no translator, 32 = bad version |
| `OpenDoc6` | returns `None` on failure with the reason in two `[in,out]` ints |
| `CloseDoc` on a dirty doc | opens a modal dialog and **never returns** |
| Busy rejections | `RPC_E_CALL_REJECTED (0x80010001)` / `RPC_E_SERVERCALL_RETRYLATER (0x8001010A)`; pywin32 exposes no `CoRegisterMessageFilter`, so `retry_call` backs off and retries instead of using the documented `IOleMessageFilter` |
| Modal dialogs | do **not** reject other clients: measured 150/150 concurrent calls served while a modal was up (re-entrant), while the dialog's own caller stayed blocked |
| Lengths / mass | metres / kilograms |
| Localization | `前视基准面` (Front Plane), `凸台-拉伸1` (Boss-Extrude1). Find planes with `GetTypeName2() == 'RefPlane'` |
| Cross-process dialog text | buttons readable, the message Static is not; `SendMessage(WM_GETTEXT)` could block, so it is not attempted |

## Measured behaviour worth remembering

* Cold start: splash after ~5 s, main window within ~60 s, ~416 MB idle.
* `SaveBMP(1600, 1000)` on a part view: 4.8 MB BMP, 100 % non-black, 793 colours;
  the PNG contains the graphics area only - no menus, no window chrome, and it
  works with the window occluded. This is why `shot` uses it instead of a screen
  grab.
* `GetMassProperties2` returns **13** values, verified against an analytic
  50x30x20 mm box (relative error < 1e-6 on every entry):

  | index | meaning | box value |
  |---|---|---|
  | 0-2 | centre of mass x, y, z (m) | 0.025, 0.015, 0.01 |
  | 3 | volume (m³) | 3.0e-05 (30000 mm³) |
  | 4 | surface area (m²) | 0.0062 (6200 mm²) |
  | 5 | mass (kg) | 0.03 (default material, 1000 kg/m³) |
  | 6-8 | moments of inertia Lxx, Lyy, Lzz | 3.25e-06, 7.25e-06, 8.5e-06 = m(a²+b²)/12 |
  | 9-11 | products of inertia Lxy, Lzx, Lyz | ≈ 0 |
  | 12 | density (g/cm³) | 1.0 |

* Export matrix, all via `SaveAs3(path, 0, swSaveAsOptions_Silent)`:

  | format | bytes | structural check |
  |---|---|---|
  | `.STEP` | 15746 | starts `ISO-10303-21;` |
  | `.STL` | 684 | binary, header says 12 triangles, `84 + 12*50 == 684` |
  | `.IGS` | 22386 | `SolidWorks IGES file ...` |
  | `.PDF` | 164193 | `%PDF-1.4` ... `%%EOF` |
  | `.X_T` | 7425 | `**ABCDEFGHIJKLMNOPQRSTUVWXYZ` |
  | `.3MF` | 4340 | `PK` (zip) |
  | `.OBJ` | - | **fails**, code 256 - no translator installed |

* A job round trip (build a box, measure it, save it, render it) takes **~2 s**.

## Pitfall log

Every entry below cost a failed attempt during development.

1. **`GetBodies2` is on `IPartDoc`, not `IModelDoc2`.** `AttributeError: ... has
   no attribute 'GetBodies2'`. Cast: `cast(doc, 'IPartDoc')`.
2. **Late-bound `ActiveDoc` makes properties look callable.**
   `doc.GetTitle()` raised `TypeError: 'str' object is not callable` on one path.
   Fix: `cast(obj, 'IModelDoc2')` before reading anything.
3. **`FirstFeature`/`GetNextFeature` return late-bound objects too**, so
   `feature.GetTypeName2()` fails the same way. Cast to `IFeature` inside the
   loop.
4. **`pythoncom.VARIANT(VT_BYREF|VT_I4, 0)` for `[in,out]` params raises
   `TypeError: int() argument must be ... not 'VARIANT'`.** A plain `0` works and
   the out values come back appended to the return value.
5. **`SaveAs3` returns 0 on success** - treating it as a bool reports failure for
   every successful export. Use `== 0` as success and trust the file.
6. **`exc.args[0]` may be a string.** The first version of the error translator
   did `hresult & 0xFFFFFFFF` on it and raised
   `TypeError: unsupported operand type(s) for &: 'str' and 'int'`, hiding the
   real exception. `_hresult_of()` now only accepts ints.
7. **`GetWindowText` does not reach into another process's controls.** The dialog
   buttons read back fine, the message body does not.
8. **Reading a dialog with `SendMessage(WM_GETTEXT)` is not safe**: it is
   synchronous, and the thread that owns the dialog is exactly the one that is
   busy. Not attempted.
9. **`dismiss` defaults to Cancel.** Posting OK to a "save changes?" prompt means
   "yes, overwrite"; Cancel aborts the operation and only leaves the document
   open.
10. **The modal wedge is a block, not a rejection.** A test that expected
    `RPC_E_CALL_REJECTED` from a concurrent caller failed: 150/150 calls
    succeeded while the modal was up.
11. **`printf`-style state lines in a job's own output can fool a status
    scraper.** The test harness now reads the status file rather than parsing
    console prose.
12. **A parent must write `RUNNING` before spawning an async child**, or a child
    that finishes instantly has its `OK` overwritten.

## Paths and state

```
%LOCALAPPDATA%\swbridge\
  stubs\        generated COM stubs, swenums.py, meta.json   (regenerate: scripts/genstubs.py)
  tasks\ logs\ status\   one pair per `run`; the status file's first line is the state
  shots\        PNG captures
  out\<id>\     a job's own output directory, injected as OUT
```

`SWBRIDGE_HOME` overrides the root. Nothing is written outside it, and nothing in
the repository is needed at runtime except the `sw/` modules and `scripts/genstubs.py`.

## Re-verifying

```powershell
python tests/phase1_smoke.py     # attach / model / render / close
python tests/phase2_driver.py    # 22 checks over the CLI, async, exports, errors
python tests/phase3_hard.py      # 26 checks: retry, guards, units, all exports, dialogs
python tests/phase3_modal.py     # raises a real modal and recovers from it
python tests/selftest.py         # runs all of the above and summarises
```

`phase2_driver.py`, `phase3_hard.py` and `phase3_modal.py` refuse to run while
documents are open, so they cannot disturb work in progress.

## Deliberately not done

* No .NET add-in (that is the reverse direction: SOLIDWORKS calling us).
* No macro (`.swp`) generation.
* No silent mutation of the user's SOLIDWORKS settings: dialog suppression is
  documented, not applied behind their back. The recovery path (`dialogs` /
  `dismiss`) is preferred over changing preferences.
* `quit` is never automatic, and requires `--force` while documents are open
  because that discards unsaved changes.
