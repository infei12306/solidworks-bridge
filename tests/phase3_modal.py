#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 3, part two: provoke the two failure modes that cannot be unit-tested.

PHASE 3's first half verified retry_call() against synthetic com_errors.  This
file goes after the real thing:

  * a MODAL DIALOG, raised by SOLIDWORKS itself, and whether it makes concurrent
    COM calls fail (busy rejection) or merely block;
  * the RECOVERY WORKFLOW: notice the dialog, answer it from outside, and get the
    wedged job finished - entirely through swbridge.py, the way an agent would.

Everything here goes through the CLI so the shipped tooling is what gets tested.
The rejection histogram is REPORTED rather than asserted: it is a measurement of
this SOLIDWORKS build, and a run that finds no rejections is still a valid
result (it would mean the wedge is a *block*, not a rejection).

    python tests/phase3_modal.py
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sw"))

import swcore  # noqa: E402

SWBRIDGE = os.path.join(ROOT, "sw", "swbridge.py")
PYTHON = sys.executable

#: Raises a real modal dialog and reports how it was answered.
MODAL_JOB = """
log("raising a modal dialog ...")
icon = getattr(E, "swMbInformation", 1)
buttons = getattr(E, "swMbOkCancel", 2)
answered = call(app, "SendMsgToUser2", "swbridge modal recovery self-test", icon, buttons)
log("dialog answered with %s" % answered)
log("MODAL JOB OK")
"""

#: Hammers a cheap call and records every failure's HRESULT as it goes, so the
#: histogram is readable even if this job is still wedged when we look.
HAMMER_JOB = """
import json, os
counts = {}
path = os.path.join(OUT, "hammer.json")
for i in range(150):
    try:
        call(app, "GetDocumentCount")
    except Exception as exc:
        key = str(getattr(exc, "hresult", None))
        counts[key] = counts.get(key, 0) + 1
    with open(path, "w") as fh:
        json.dump({"iterations": i + 1, "errors": counts}, fh)
log("hammer done: %s" % counts)
"""

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print("%-4s %-46s %s" % ("PASS" if condition else "FAIL", name, detail))
    return condition


def run(*args, expect=None, timeout=120):
    return subprocess.run([PYTHON, SWBRIDGE, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def state_of(task_id):
    path = os.path.join(swcore.STATUS_DIR, "%s.txt" % task_id)
    if not os.path.isfile(path):
        return "NO-STATUS"
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip().splitlines()[0]


def wait_for(task_id, wanted, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = state_of(task_id)
        if state in wanted:
            return state
        time.sleep(0.5)
    return state_of(task_id)


def main():
    session = swcore.Session.attach()
    pid = session.pid
    if session.titles():
        print("refusing to run: documents are open -> %s" % session.titles())
        return 3

    for stale in ("hammer.json",):
        path = os.path.join(swcore.OUT_DIR, "p3hammer", stale)
        if os.path.isfile(path):
            os.remove(path)

    print("== raising a modal dialog from inside SOLIDWORKS ==")
    run("run", "--id", "p3modal", "--async", "--code", MODAL_JOB)

    modal, windows = None, []
    deadline = time.time() + 30
    while time.time() < deadline:
        windows = swcore.dialogs(pid)
        modal = [w for w in windows if w["modal"]]
        if modal:
            break
        time.sleep(0.5)
    check("the modal dialog was detected",
          bool(modal), modal[0]["title"] if modal else "none found")
    if not modal:
        print("cannot continue without the dialog; dismiss it by hand if one is on screen")
        return 1
    message = " | ".join(modal[0]["child_text"])
    check("the dialog is identifiable from outside",
          ("取消" in message) or ("Cancel" in message),
          "title=%r controls=%s" % (modal[0]["title"], message))
    # MEASURED LIMIT: the message body is NOT readable.  GetWindowText does not
    # reach into another process's controls (the buttons happen to be cached, the
    # Static holding the sentence is not).  The blocking SendMessage(WM_GETTEXT)
    # fallback would risk wedging us on the very dialog we are trying to unblock,
    # so it is deliberately not used.  The buttons are enough to identify and
    # drive the dialog, and the job's own log records what it asked.
    print("   NOTE: only the buttons are readable cross-process; the message body is")
    print("         not, and reading it could block. The job log has the request.")

    # The wedge itself, and the reason this whole feature exists: the job that
    # raised the dialog is still RUNNING, because SendMsgToUser2 does not return
    # until somebody answers it.  There is no timeout and no exception.
    check("the dialog's owner is wedged (its job is still RUNNING)",
          state_of("p3modal") == "RUNNING", state_of("p3modal"))

    print()
    print("== hammering cheap COM calls while it is up ==")
    run("run", "--id", "p3hammer", "--async", "--code", HAMMER_JOB)
    hammer_json = os.path.join(swcore.OUT_DIR, "p3hammer", "hammer.json")
    iterations = 0
    deadline = time.time() + 12
    while time.time() < deadline:
        if os.path.isfile(hammer_json):
            with open(hammer_json, encoding="utf-8") as handle:
                data = json.load(handle)
            iterations = data.get("iterations", 0)
            if iterations >= 150:
                break
        time.sleep(0.5)

    histogram = {}
    if os.path.isfile(hammer_json):
        with open(hammer_json, encoding="utf-8") as handle:
            histogram = json.load(handle).get("errors", {})
    total_errors = sum(histogram.values())
    print("   iterations completed : %d/150" % iterations)
    print("   error histogram      : %s" % (histogram or "(none)"))
    # MEASURED, not asserted: on this build (2025 SP5 / 33.5.0.0053) a modal
    # dialog does NOT make concurrent calls fail - SOLIDWORKS keeps servicing
    # them re-entrantly while the modal loop runs.  So the modal hazard here is a
    # BLOCK (the caller never returns), not a busy rejection, and dismissing the
    # dialog is the fix.  The rejection path is covered by the synthetic tests in
    # phase3_hard.py.  Reported rather than checked, because it is a property of
    # this SOLIDWORKS build and not of the bridge.
    if total_errors:
        print("   NOTE: calls WERE rejected here - if this ever changes, revisit the")
        print("         blocking-vs-rejection assumption in REFERENCE.md")
    else:
        print("   NOTE: no rejections. The dialog blocks its own caller while other")
        print("         clients keep being served - re-entrantly, not rejected.")

    print()
    print("== recovering: answer the dialog through swbridge ==")
    dismiss = run("dismiss", "--button", "cancel")
    check("dismiss reported success", "posted WM_COMMAND" in dismiss.stdout,
          dismiss.stdout.strip().splitlines()[-1] if dismiss.stdout.strip() else dismiss.stderr)

    final = wait_for("p3modal", ("OK", "ERR"), 30)
    check("the wedged job finished", final == "OK", final)
    hammer_state = wait_for("p3hammer", ("OK", "ERR"), 30)
    check("the hammer finished too", hammer_state == "OK", hammer_state)

    time.sleep(1.0)
    check("no dialog is left behind", not [w for w in swcore.dialogs(pid) if w["modal"]])

    fresh = swcore.Session.attach()
    check("the session is responsive again", fresh.document_count() == 0,
          "documents=%s" % fresh.document_count())

    log_file = os.path.join(swcore.LOGS_DIR, "p3modal.log")
    if os.path.isfile(log_file):
        with open(log_file, encoding="utf-8") as handle:
            body = handle.read()
        check("the job saw its dialog answered", "dialog answered with" in body,
              [l for l in body.splitlines() if "answered with" in l][:1])

    failed = [n for n, p, _ in RESULTS if not p]
    print()
    print("%d/%d checks passed%s" % (len(RESULTS) - len(failed), len(RESULTS),
                                     "" if not failed else "  FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
