#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 2 acceptance: drive swbridge.py the way an agent would, and check it.

This is a real end-to-end pass over the CLI - every step shells out to
swbridge.py exactly as documented, so a regression in argument handling, status
files, async detachment or the shot path shows up here.

    python tests/phase2_driver.py

Requires SOLIDWORKS to be running.  It closes documents it created, and refuses
to run if the session already has documents open (so it can never disturb work
you had in progress).
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SWBRIDGE = os.path.join(ROOT, "sw", "swbridge.py")
PYTHON = sys.executable
BRIDGE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "swbridge")

RESULTS = []


def run(*args, expect=0):
    proc = subprocess.run([PYTHON, SWBRIDGE] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if expect is not None and proc.returncode != expect:
        raise AssertionError(
            "swbridge %s -> exit %d (expected %d)\n%s\n%s"
            % (" ".join(args), proc.returncode, expect, proc.stdout, proc.stderr))
    return proc


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print("%-4s %-34s %s" % ("PASS" if condition else "FAIL", name, detail))
    return condition


def status_of(task_id):
    """Authoritative state, read from the status FILE.

    The `status` CLI is invoked too (so its parser is exercised), but its stdout
    also echoes the job's own log tail - and a job that prints "STATE  : OK" of
    its own would fool any text scraper.  Assert on the file, not on prose.
    """
    proc = run("status", "--id", task_id, expect=None)
    path = os.path.join(BRIDGE, "status", "%s.txt" % task_id)
    state = "NO-STATUS"
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            state = handle.read().strip().splitlines()[0]
    return state, proc.stdout


def main():
    # ---- 0. refuse to disturb existing work --------------------------------
    attach = run("attach")
    docs = [l for l in attach.stdout.splitlines() if l.startswith("documents:")]
    text = docs[0].split(":", 1)[1].strip() if docs else ""
    if text and text != "(none)":
        print("refusing to run: the session already has documents open -> %s" % text)
        print("close them first (swbridge.py close --all) or run this when it is clean.")
        return 3
    check("attach reports a 2025 session", "33." in attach.stdout,
          [l for l in attach.stdout.splitlines() if l.startswith("revision")][0].strip())

    # ---- 1. build / measure / save / render --------------------------------
    job = run("run", "--id", "p2box", "--file", os.path.join(HERE, "jobs", "make_box.py"))
    check("run make_box exits 0", job.returncode == 0)
    state, out = status_of("p2box")
    check("make_box state OK", state == "OK", state)
    check("mass properties decoded", "mass properties: status=0 values=13" in job.stdout)
    check("solid body created", "solid bodies: 1" in job.stdout)

    out_dir = os.path.join(BRIDGE, "out", "p2box")
    part = os.path.join(out_dir, "box.SLDPRT")
    png = os.path.join(out_dir, "box.png")
    check("part written", os.path.isfile(part) and os.path.getsize(part) > 10000,
          "%d bytes" % (os.path.getsize(part) if os.path.isfile(part) else -1))
    check("render written", os.path.isfile(png), png)

    if os.path.isfile(png):
        from PIL import Image
        img = Image.open(png).convert("RGB")
        colors = img.getcolors(maxcolors=1 << 24) or []
        non_black = sum(c for c, (r, g, b) in colors if (r + g + b) > 30)
        check("render is not a black frame",
              non_black / float(img.width * img.height) > 0.01,
              "%.1f%% non-black, %d colours" % (100.0 * non_black / (img.width * img.height),
                                                len(colors)))

    # ---- 2. the volume must match the modelled 50x30x20 mm -----------------
    info = run("info")
    raw = [l for l in info.stdout.splitlines() if "raw =" in l]
    if raw:
        values = json.loads(raw[0].split("raw =", 1)[1].strip())
        volume_expected = 0.05 * 0.03 * 0.02
        check("volume matches an analytic box",
              abs(values[3] - volume_expected) / volume_expected < 1e-6,
              "volume=%.10g m^3 (expected %.10g)" % (values[3], volume_expected))
        check("surface area matches", abs(values[4] - 0.0062) / 0.0062 < 1e-6,
              "area=%.10g m^2" % values[4])
        check("centroid at the box centre",
              abs(values[0] - 0.025) < 1e-9 and abs(values[1] - 0.015) < 1e-9
              and abs(values[2] - 0.010) < 1e-9,
              "com=%s" % values[:3])
    else:
        check("mass properties parsed", False, "no 'raw =' line in info output")

    # ---- 3. close, reopen, inspect -----------------------------------------
    closed = run("close", "--all")
    check("close --all leaves no documents", "(none)" in closed.stdout)

    opened = run("open", part)
    check("open re-opens the saved part", "opened :" in opened.stdout,
          [l for l in opened.stdout.splitlines() if l.startswith("opened")][0].strip())

    info2 = run("info")
    check("info sees 1 body after reopen", "bodies     : 1" in info2.stdout)

    # ---- 4. shot through the CLI -------------------------------------------
    shot = run("shot", "--out", os.path.join(out_dir, "cli_shot.png"), "--width", "800",
               "--height", "600")
    check("shot writes a PNG", "SHOT   :" in shot.stdout and
          os.path.isfile(os.path.join(out_dir, "cli_shot.png")))

    # ---- 5. async: must return at once, then finish ------------------------
    started = time.time()
    run("run", "--id", "p2async", "--async",
        "--code", "import time\nlog('tick')\ntime.sleep(4)\nlog('tock')")
    elapsed = time.time() - started
    check("--async returns immediately", elapsed < 3.0, "%.2fs" % elapsed)
    state, _ = status_of("p2async")
    check("async task is RUNNING right after launch", state == "RUNNING", state)

    deadline = time.time() + 60
    while time.time() < deadline:
        state, _ = status_of("p2async")
        if state != "RUNNING":
            break
        time.sleep(1.0)
    check("async task finishes", state == "OK", state)
    log_file = os.path.join(BRIDGE, "logs", "p2async.log")
    if os.path.isfile(log_file):
        with open(log_file, encoding="utf-8") as handle:
            body = handle.read()
        check("async captured its own output", "tick" in body and "tock" in body)

    # ---- 6. a failing job must be reportable, not silent -------------------
    proc = run("run", "--id", "p2err", "--code", "raise RuntimeError('deliberate boom')",
               expect=1)
    state, out = status_of("p2err")
    check("failing job reports ERR", state == "ERR", state)
    check("failing job keeps the message", "deliberate boom" in out)

    # ---- 7. clean up --------------------------------------------------------
    final = run("close", "--all")
    check("session left clean", "(none)" in final.stdout)
    report = os.path.join(BRIDGE, "out", "phase2_report.json")
    with open(report, "w", encoding="utf-8") as handle:
        json.dump([{"name": n, "pass": p, "detail": d} for n, p, d in RESULTS],
                  handle, indent=2, ensure_ascii=False)
    failed = [n for n, p, _ in RESULTS if not p]
    print()
    print("%d/%d checks passed%s" % (len(RESULTS) - len(failed), len(RESULTS),
                                     "" if not failed else "  FAILED: " + ", ".join(failed)))
    print("report: %s" % report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
