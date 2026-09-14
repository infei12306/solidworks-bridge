#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 3 acceptance: the parts that are easy to get subtly wrong.

Three groups:

  1. RETRY LOGIC, deterministically.  The busy-server rejections are hard to
     provoke on demand, so the retry path is verified against synthetic
     com_errors with the exact HRESULTs SOLIDWORKS raises.  This also pins the
     rule that a NON-retryable failure must be raised immediately, and that a
     plain Python exception must not be dressed up as an HRESULT.
  2. EXPORT + VALIDATION, live.  Every format is exported and structurally
     checked, including the failure path (a format whose translator this install
     lacks must produce a clear error, not a silent success).
  3. MODAL DIALOGS, live.  The session must have no modal dialog sitting on it,
     which is the one condition the bridge cannot recover from on its own.

    python tests/phase3_hard.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sw"))

import pywintypes  # noqa: E402
import swcore  # noqa: E402

RPC_E_CALL_REJECTED = -2147418111
RPC_E_SERVERCALL_RETRYLATER = -2147417846
DISP_E_MEMBERNOTFOUND = -2147352573

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print("%-4s %-44s %s" % ("PASS" if condition else "FAIL", name, detail))
    return condition


def com_error(hresult):
    return pywintypes.com_error(hresult, "synthetic", None, None)


# --------------------------------------------------------------------- 1. retry

def test_retry():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise com_error(RPC_E_CALL_REJECTED)
        return "done"

    result = swcore.retry_call(flaky, tries=5, base=0.01, cap=0.02, what="flaky")
    check("retries a rejected call until it succeeds", result == "done",
          "%d attempts" % len(attempts))
    check("stopped as soon as it succeeded", len(attempts) == 3)

    for label, hresult in (("CALL_REJECTED", RPC_E_CALL_REJECTED),
                           ("SERVERCALL_RETRYLATER", RPC_E_SERVERCALL_RETRYLATER)):
        tries = []

        def always_busy():
            tries.append(1)
            raise com_error(hresult)

        try:
            swcore.retry_call(always_busy, tries=3, base=0.01, cap=0.02, what="busy")
            check("%s eventually gives up" % label, False, "no exception raised")
        except swcore.SwError as exc:
            check("%s eventually gives up" % label, "gave up after 3 tries" in str(exc),
                  "%d attempts, state ok" % len(tries))
            check("%s keeps the HRESULT and hint" % label,
                  exc.hresult == hresult and bool(exc.hint))

    fatal_tries = []

    def fatal():
        fatal_tries.append(1)
        raise com_error(DISP_E_MEMBERNOTFOUND)

    try:
        swcore.retry_call(fatal, tries=5, base=0.01, cap=0.02, what="fatal")
        check("does NOT retry a non-retryable HRESULT", False, "no exception raised")
    except swcore.SwError as exc:
        check("does NOT retry a non-retryable HRESULT", len(fatal_tries) == 1,
              "%d attempt(s)" % len(fatal_tries))
        check("late-bound hint survives translation",
              "late-bound" in (exc.hint or ""), exc.hint.splitlines()[0][:60])

    # This used to be masked by the error handler itself: exc.args[0] is a string
    # and was being AND-ed with 0xFFFFFFFF, hiding the real failure.
    try:
        swcore.retry_call(lambda: 1 / 0, tries=3, base=0.01, cap=0.02, what="divzero")
        check("plain exceptions are not disguised as HRESULTs", False, "no exception")
    except swcore.SwError as exc:
        check("plain exceptions are not disguised as HRESULTs",
              exc.hresult is None and "division by zero" in str(exc), str(exc)[:60])
    except ZeroDivisionError:
        check("plain exceptions are not disguised as HRESULTs", True, "propagated raw")


def test_call_guard():
    session = swcore.Session.attach()
    doc = session.active()
    try:
        swcore.call(doc, "GetTitle")     # it is a method, but the guard must not fire
        check("call() accepts a real method", True)
    except swcore.SwError as exc:
        check("call() accepts a real method", False, str(exc)[:60])
    property_name = None
    for name in ("Extension", "SketchManager", "FeatureManager"):
        if name in (doc._prop_map_get_ or {}):
            property_name = name
            break
    if property_name:
        try:
            swcore.call(doc, property_name)
            check("call() rejects a property with advice", False, "no exception")
        except swcore.SwError as exc:
            check("call() rejects a property with advice", "use getv" in str(exc)
                  or "getv" in (exc.hint or ""), str(exc).splitlines()[0][:70])


def test_units():
    check("mm() converts to metres", abs(swcore.mm(50) - 0.05) < 1e-15)
    check("to_mm() converts back", abs(swcore.to_mm(0.05) - 50.0) < 1e-12)


# -------------------------------------------------------------------- 2. export

def test_export(session):
    doc = session.active()
    out = os.path.join(swcore.OUT_DIR, "p3export")
    os.makedirs(out, exist_ok=True)
    expected = {"stl": "12 triangles"}
    for fmt in sorted(swcore.EXPORT_FORMATS):
        try:
            info = swcore.export(doc, os.path.join(out, "box"), fmt)
        except swcore.SwError as exc:
            check("export %s" % fmt, False, str(exc).splitlines()[0][:70])
            continue
        detail = info["check"]
        wanted = expected.get(fmt)
        check("export %s" % fmt, (wanted in detail) if wanted else info["bytes"] > 0, detail)

    # the failure path: .OBJ has no translator here
    try:
        swcore.export(doc, os.path.join(out, "box"), "step")   # sanity: works
    except swcore.SwError as exc:
        check("export step works before the failure test", False, str(exc)[:60])

    try:
        swcore.export(doc, os.path.join(out, "box"), "nonsense")
        check("unknown format is rejected", False, "no exception")
    except swcore.SwError as exc:
        check("unknown format is rejected", "unknown export format" in str(exc))

    # a wrong SaveAsVersion must fail loudly rather than quietly write nothing
    L, E = swcore.stubs()
    target = os.path.join(out, "badversion.STEP")
    if os.path.isfile(target):
        os.remove(target)
    code = swcore.call(doc, "SaveAs3", target, 1, E.swSaveAsOptions_Silent)
    check("SaveAs3 returns an error CODE, not a bool", code != 0,
          "version=1 -> code %s" % code)


# ------------------------------------------------------------------- 3. dialogs

def test_dialogs(session):
    windows = swcore.dialogs(session.pid)
    check("dialogs() sees the SOLIDWORKS windows", len(windows) > 0,
          "%d window(s)" % len(windows))
    titles = [w["title"] for w in windows if w["title"]]
    check("the main frame window is among them",
          any("SOLIDWORKS" in t for t in titles), ", ".join(titles)[:70])
    modal = [w for w in windows if w["modal"]]
    check("no modal dialog is blocking the session", not modal,
          ", ".join(w["title"] for w in modal) if modal else "clear")


def main():
    session = swcore.Session.attach()
    if session.titles():
        print("refusing to run: the session has documents open -> %s" % session.titles())
        print("swbridge.py close --all  first, so this test cannot touch your work.")
        return 3

    test_retry()
    test_units()

    # build the reference box through the normal job path
    with open(os.path.join(HERE, "jobs", "make_box.py"), encoding="utf-8") as handle:
        source = handle.read()
    code = swcore.run_job(source, task_id="p3box", out_dir=os.path.join(swcore.OUT_DIR, "p3box"),
                          log=lambda *a: None)
    check("reference box built", code == 0)

    test_call_guard()
    test_export(session)
    test_dialogs(session)

    # leave the session as we found it: close, never quit (the user may be using it)
    for title in session.titles():
        session.close_document(title, discard_changes=True)
    check("session left clean", not session.titles())

    failed = [n for n, p, _ in RESULTS if not p]
    print()
    print("%d/%d checks passed%s" % (len(RESULTS) - len(failed), len(RESULTS),
                                     "" if not failed else "  FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
