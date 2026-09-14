# solidworks-bridge

Drive a **live SOLIDWORKS session** from [DeepSeek Harness](https://github.com/infei12306)
over Windows COM automation. Nothing is clicked: jobs run in Python against the
running application, data comes back through files, and the console only receives
a summary.

Sibling project: [`matlab-bridge`](https://github.com/infei12306/matlab-bridge) does
the same for MATLAB.

```
swbridge.py doctor                     environment + attach diagnostics
swbridge.py launch                     start SOLIDWORKS and wait for COM
swbridge.py run --file job.py          run a Python job against the live session
swbridge.py run --code "..." --async   fire and poll with `status`
swbridge.py open part.SLDPRT           open silently
swbridge.py info                       mass properties, bodies, features
swbridge.py save out --as step|stl|iges|pdf|x_t|3mf   export AND verify
swbridge.py shot --out view.png        render the graphics area
swbridge.py dialogs / dismiss          find and answer a modal dialog
swbridge.py api GetBodies2             real COM signature, offline
swbridge.py enum swFileSave            constant lookup, offline
```

## Requirements

* SOLIDWORKS installed and licensed (**built and verified against 2025 SP5**,
  `33.5.0.0053`; the ProgID is `SldWorks.Application.33`)
* 64-bit Python 3.11+ with `pywin32` (`pillow` for image handling)
* The session may be running or not; `launch` starts it and waits for COM

## Install

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

It finds a suitable Python, copies the driver to `%LOCALAPPDATA%\swbridge`,
generates the COM stubs, and links this checkout into `~\.dsh\skills\solidworks-bridge`.

## Verify

```powershell
& '<python.exe>' "$env:LOCALAPPDATA\swbridge\sw\swbridge.py" doctor
python tests\selftest.py        # runs all four suites; requires a clean session
```

## Why it is built this way

On the reference install the SOLIDWORKS type libraries are registered under the
**wrong version** (the registry says 21.0/1.0, the `.tlb` files say 33.0), so
every registry-based lookup - `EnsureDispatch`, `EnsureModule`,
`LoadRegTypeLib`, and .NET's `Marshal.GetActiveObject` - fails with
`TYPE_E_LIBNOTREGISTERED`. This tool reads the `.tlb` files directly and attaches
through the Running Object Table instead, which is why it works here and most
Python/SOLIDWORKS tutorials do not.

Everything else that was learned the hard way - late-bound objects that turn
properties into strings, `[in,out]` parameters that pywin32 appends to the return
value, `SaveAs3` returning an error code rather than a bool, and modal dialogs
that block a COM call forever - is written up in
[REFERENCE.md](REFERENCE.md) and enforced by the tests.

## Layout

```
sw/swcore.py       the machine room: attach, cast/getv/call, retry, session, export, diagnostics
sw/swbridge.py     the CLI
sw/swapi.py        offline signature / constant / property lookup
scripts/genstubs.py          generate COM stubs straight from the .tlb files
scripts/install.ps1          deploy + link into the skills directory
tests/             four suites, ~57 checks, all runnable against a clean session
SKILL.md           how an agent should use this
REFERENCE.md       design decisions, measured numbers, full pitfall log
PLAN.md            how it was built, phase by phase
```

## Licence

MIT - see [LICENSE](LICENSE).
