#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run every suite in order and summarise.

Refuses to start unless the SOLIDWORKS session is clean, so it can never touch a
document you had open.  After that baseline check, anything left open is ours
(the `close --all` before each suite is therefore safe), which keeps a failing
suite from blocking the next one.

    python tests/selftest.py [--only phase3]

Writes a JSON report next to the bridge's other outputs.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sw"))

import swcore  # noqa: E402

PYTHON = sys.executable
SWBRIDGE = os.path.join(ROOT, "sw", "swbridge.py")

SUITES = [
    ("phase1", "phase1_smoke.py", "attach / model / render / close"),
    ("phase2", "phase2_driver.py", "22 checks over the CLI, async and errors"),
    ("phase3", "phase3_hard.py", "26 checks: retry, guards, exports, dialogs"),
    ("phase3-modal", "phase3_modal.py", "raises a real modal dialog and recovers"),
]

COUNT_RE = re.compile(r"(\d+)/(\d+) checks passed")


def close_everything():
    subprocess.run([PYTHON, SWBRIDGE, "close", "--all"], capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run only suites whose name contains this")
    args = ap.parse_args()

    session = swcore.Session.attach()
    open_now = session.titles()
    if open_now:
        print("refusing to run: the session has documents open -> %s" % open_now)
        print("Save and close them, or run: swbridge.py close --all")
        return 3
    print("session      : SOLIDWORKS %s (pid %s), no documents open"
          % (session.revision, session.pid))
    print("bridge home  : %s" % swcore.HOME)
    print()

    report = []
    failures = 0
    for name, script, blurb in SUITES:
        if args.only and args.only not in name:
            continue
        print("=" * 72)
        print("== %-12s %s" % (name, blurb))
        print("=" * 72)
        close_everything()
        started = time.time()
        proc = subprocess.run([PYTHON, os.path.join(HERE, script)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        elapsed = time.time() - started
        sys.stdout.write(proc.stdout)
        if proc.stderr.strip():
            sys.stdout.write(proc.stderr)
        matched = COUNT_RE.search(proc.stdout)
        entry = {
            "suite": name,
            "exit": proc.returncode,
            "seconds": round(elapsed, 1),
            "checks_passed": int(matched.group(1)) if matched else None,
            "checks_total": int(matched.group(2)) if matched else None,
            "ok": proc.returncode == 0,
        }
        report.append(entry)
        if proc.returncode != 0:
            failures += 1
        print("-> %s in %.1fs (exit %d)" % ("OK" if entry["ok"] else "FAILED",
                                            elapsed, proc.returncode))
        print()

    close_everything()
    left = swcore.Session.attach().titles()
    print("=" * 72)
    for entry in report:
        counts = ("%s/%s checks" % (entry["checks_passed"], entry["checks_total"])
                  if entry["checks_total"] else "-")
        print("  %-14s %-8s %-12s %5.1fs" % (entry["suite"],
                                             "OK" if entry["ok"] else "FAILED",
                                             counts, entry["seconds"]))
    print("  session left with %d document(s)" % len(left))
    path = os.path.join(swcore.OUT_DIR, "selftest_report.json")
    os.makedirs(swcore.OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "suites": report,
                   "documents_left_open": left}, handle, indent=2, ensure_ascii=False)
    print("  report: %s" % path)
    print()
    total_checks = sum(e["checks_total"] or 0 for e in report)
    print("SELFTEST: %s  (%d suite(s), %d checks)"
          % ("ALL GREEN" if not failures else "%d SUITE(S) FAILED" % failures,
             len(report), total_checks))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
