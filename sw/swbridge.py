#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""swbridge - drive the user's live SOLIDWORKS session from the command line.

Nothing is clicked.  Commands go in over COM, data comes back through files, and
the console only ever receives a summary - the same contract as mlbridge for
MATLAB.

    swbridge.py doctor                     environment + attach diagnostics
    swbridge.py attach                     report the running session
    swbridge.py launch [--wait 240]        start SOLIDWORKS and wait for COM
    swbridge.py run --file job.py          run a Python job against the session
    swbridge.py run --code "..." [--async] same, inline; --async polls
    swbridge.py status --id <id>           read a task's state and log tail
    swbridge.py shot [--out x.png]         render the active view to PNG
    swbridge.py open <file>                open a document silently
    swbridge.py info                       summarise the active document
    swbridge.py close [--all]              close documents without modal dialogs
    swbridge.py quit [--force]             exit SOLIDWORKS
    swbridge.py api <Name>                 offline: real COM signature lookup
    swbridge.py enum <keyword>             offline: search the 8199 constants

Job scripts get these names pre-injected: app, doc, session, L (interface
classes), E (constants), cast, getv, call, retry, OUT, HOME, log.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import swcore  # noqa: E402
from swcore import SwError  # noqa: E402


def show(result):
    """Print a finished task result the way mlbridge does."""
    print("ID     : %s" % result["id"])
    print("STATE  : %s" % result["state"])
    if result["detail"]:
        print("DETAIL : %s" % result["detail"].splitlines()[0])
    print("LOG    : %s" % result["log"])
    print("---- log tail ----")
    print(result["tail"] or "(no output)")
    print("------------------")


def cmd_doctor(args):
    report = swcore.environment_report()
    print("== environment ==")
    print("python          : %s (%d-bit)  %s"
          % (report["python"], report["python_bits"], report["python_exe"]))
    print("pywin32         : %s" % report.get("pywin32"))
    print("numpy / pillow  : %s / %s" % (report.get("numpy"), report.get("PIL")))
    print("bridge home     : %s" % report["bridge_home"])
    stubs = report.get("stubs")
    if stubs:
        print("stubs           : SOLIDWORKS %s generated %s, %s enum constants"
              % (stubs["sw_version"], stubs["generated_utc"], stubs["enums"]))
    else:
        print("stubs           : MISSING -> run scripts/genstubs.py")
    print()
    print("== SOLIDWORKS ==")
    print("exe             : %s" % report.get("exe"))
    print("exe version     : %s" % report.get("exe_version"))
    typelib = report.get("typelib") or {}
    print("typelib         : registry says %s, the .tlb file says %s%s"
          % (typelib.get("registered"), typelib.get("file_version"),
             "  <-- MISMATCH, registry lookups will fail" if typelib.get("mismatch") else ""))
    print("progid          : %s" % report["progid"])
    print("running         : %s" % report["sw_running"])
    if report.get("revision"):
        print("revision / pid  : %s / %s" % (report["revision"], report["pid"]))
        print("attach          : %s ms" % report.get("attach_ms"))
        print("busy            : %s" % report.get("busy"))
        print("documents       : %s" % (report.get("documents") or "(none)"))
    if report.get("attach_error"):
        print("attach error    : %s" % report["attach_error"])
    print()
    print("== running object table ==")
    for entry in report["rot"]:
        print("  %s" % entry)
    return 0


def cmd_attach(args):
    session = swcore.Session.attach()
    print("revision : %s" % session.revision)
    print("pid      : %s" % session.pid)
    print("attach   : %s ms" % session.attach_ms)
    print("busy     : %s" % session.busy)
    titles = session.titles()
    print("documents: %s" % (titles or "(none)"))
    if args.verbose:
        for title, doc in zip(titles, session.documents()):
            print("  %-24s type=%s path=%s"
                  % (title, swcore.call(doc, "GetType"), swcore.call(doc, "GetPathName")))
        print("rot      : %s" % swcore.rot_entries())
    return 0


def cmd_launch(args):
    session = swcore.launch(exe=args.exe, wait=args.wait, log=print)
    print("pid      : %s" % session.pid)
    print("documents: %s" % (session.titles() or "(none)"))
    return 0


def cmd_run(args):
    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            source = handle.read()
        label = args.file
    elif args.code:
        source = args.code
        label = "<inline>"
    else:
        raise SwError("run needs --file or --code")

    task_id = args.id or swcore.new_id()
    swcore.ensure_dirs()
    out_dir = os.path.join(swcore.OUT_DIR, task_id)

    if args.async_:
        # Detach: the child writes the log and the final status itself, so the
        # caller can poll with `status` even while SOLIDWORKS is busy.
        # RUNNING must be written BEFORE the spawn - otherwise a job that
        # finishes instantly has its OK status overwritten by the parent.
        swcore.write_status(task_id, "RUNNING", "async job from %s" % label)
        log_file = swcore.log_path(task_id)
        child = [sys.executable, os.path.abspath(__file__), "run", "--id", task_id]
        if args.file:
            child += ["--file", os.path.abspath(args.file)]
        else:
            child += ["--code", source]
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write("# async job %s from %s\n" % (task_id, label))
            handle.flush()
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            proc = subprocess.Popen(child, stdout=handle, stderr=subprocess.STDOUT,
                                    env=env, cwd=os.getcwd(), close_fds=True)
        print("ASYNC  : started, id = %s (pid %d)" % (task_id, proc.pid))
        print("POLL   : swbridge.py status --id %s" % task_id)
        if args.wait:
            import time
            deadline = time.time() + args.wait
            while time.time() < deadline:
                time.sleep(1.0)
                if swcore.read_status(task_id)["state"] != "RUNNING":
                    break
            show(swcore.read_status(task_id, args.tail))
        return 0

    swcore.write_status(task_id, "RUNNING", "inline run from %s" % label)
    original = sys.stdout

    class Tee:
        def __init__(self, path):
            self.handle = open(path, "w", encoding="utf-8")

        def write(self, text):
            original.write(text)
            self.handle.write(text)
            self.handle.flush()

        def flush(self):
            original.flush()
            self.handle.flush()

    sys.stdout = Tee(swcore.log_path(task_id))
    try:
        code = swcore.run_job(source, task_id=task_id, out_dir=out_dir, log=print)
    finally:
        sys.stdout.flush()
        sys.stdout = original
    show(swcore.read_status(task_id, args.tail))
    return code


def cmd_status(args):
    result = swcore.read_status(args.id, args.tail)
    show(result)
    return 0 if result["state"] in ("OK", "RUNNING") else 1


def cmd_shot(args):
    session = swcore.Session.attach()
    path = swcore.shot(session, out=args.out, width=args.width, height=args.height)
    print("SHOT   : %s" % path)
    return 0


def cmd_open(args):
    session = swcore.Session.attach()
    doc = session.open_document(os.path.abspath(args.path))
    print("opened : %s" % swcore.call(doc, "GetTitle"))
    print("path   : %s" % swcore.call(doc, "GetPathName"))
    print("type   : %s" % swcore.call(doc, "GetType"))
    return 0


def cmd_info(args):
    session = swcore.Session.attach()
    doc = session.active()
    doc_type = swcore.call(doc, "GetType")
    names = {0: "none", 1: "part", 2: "assembly", 3: "drawing"}
    print("title      : %s" % swcore.call(doc, "GetTitle"))
    print("path       : %s" % swcore.call(doc, "GetPathName") or "(unsaved)")
    print("type       : %s (%s)" % (doc_type, names.get(doc_type, "?")))
    features = swcore.call(doc, "GetFeatureCount")
    print("features   : %s" % features)
    if doc_type == 1:
        bodies = swcore.call(swcore.cast(doc, "IPartDoc"), "GetBodies2", 0, False)
        print("bodies     : %s" % (0 if bodies is None else len(bodies)))
    ext = swcore.cast(swcore.getv(doc, "Extension"), "IModelDocExtension")
    try:
        props, (status,) = swcore.call_out(ext, "GetMassProperties2", 1, swcore.OUTARG, False)
    except SwError as exc:
        print("mass props : unavailable (%s)" % str(exc).splitlines()[0])
        return 0
    if props:
        print("mass props : %d values, status=%s" % (len(props), status))
        print("             raw = %s" % (list(props)[:13],))
    return 0


def cmd_save(args):
    session = swcore.Session.attach()
    doc = session.active()
    fmt = args.as_ or os.path.splitext(args.path)[1].lstrip(".").lower()
    info = swcore.export(doc, os.path.abspath(args.path), fmt)
    print("SAVED  : %s" % info["path"])
    print("format : %s" % info["format"])
    print("check  : %s" % info["check"])
    return 0


def cmd_dialogs(args):
    session = swcore.Session.attach()
    windows = swcore.dialogs(session.pid)
    modal = [w for w in windows if w["modal"]]
    print("windows of pid %s: %d" % (session.pid, len(windows)))
    for window in windows:
        print("  %-24s %-6s %s" % (window["class"], "MODAL" if window["modal"] else "",
                                   window["title"]))
        for text in window["child_text"]:
            print("        | %s" % text)
    if modal:
        print()
        print("%d MODAL dialog(s) present - that is what a wedged COM call looks like."
              % len(modal))
        print("Recover with:  swbridge.py dismiss --handle %d" % modal[0]["handle"])
    return 0


def cmd_dismiss(args):
    session = swcore.Session.attach()
    handle = args.handle
    if not handle:
        modal = [w for w in swcore.dialogs(session.pid) if w["modal"]]
        if not modal:
            print("no modal dialog found")
            return 1
        handle = modal[0]["handle"]
        print("dismissing %r (handle %d)" % (modal[0]["title"], handle))
    command = swcore.dismiss_dialog(handle, args.button)
    print("posted WM_COMMAND %d (%s)" % (command, args.button))
    print("check with:  swbridge.py dialogs")
    return 0


def cmd_close(args):
    session = swcore.Session.attach()
    if args.all:
        for title in session.titles():
            session.close_document(title, discard_changes=not args.keep_changes)
            print("closed : %s" % title)
    elif args.title:
        if session.close_document(args.title, discard_changes=not args.keep_changes):
            print("closed : %s" % args.title)
        else:
            print("no document titled %r" % args.title)
            return 1
    else:
        raise SwError("close needs a title or --all")
    print("remaining: %s" % (session.titles() or "(none)"))
    return 0


def cmd_quit(args):
    session = swcore.Session.attach()
    session.quit(force=args.force)
    print("SOLIDWORKS exited")
    return 0


def cmd_api(args):
    import swapi
    keyword = getattr(args, "enum", None) or getattr(args, "keyword", None)
    argv = ["swapi", "--stubs", swcore.STUBS_DIR]
    if keyword:
        argv += ["--enum", keyword]
    elif args.name:
        argv += [args.name]
    else:
        raise SwError("api needs a name, or `enum <keyword>`")
    saved = sys.argv
    sys.argv = argv
    try:
        return swapi.main()
    finally:
        sys.argv = saved


def build_parser():
    parser = argparse.ArgumentParser(
        prog="swbridge.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="environment and attach diagnostics").set_defaults(
        func=cmd_doctor)

    p = sub.add_parser("attach", help="report the running session")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_attach)

    p = sub.add_parser("launch", help="start SOLIDWORKS and wait for COM")
    p.add_argument("--wait", type=int, default=240)
    p.add_argument("--exe")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("run", help="run a Python job against the live session")
    p.add_argument("--file")
    p.add_argument("--code")
    p.add_argument("--id")
    p.add_argument("--async", dest="async_", action="store_true",
                   help="detach; poll with `status`")
    p.add_argument("--wait", type=int, default=0,
                   help="with --async: block up to N seconds for the result")
    p.add_argument("--tail", type=int, default=40)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="read a task's state and log tail")
    p.add_argument("--id", required=True)
    p.add_argument("--tail", type=int, default=40)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("shot", help="render the active view to PNG")
    p.add_argument("--out")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("open", help="open a document silently")
    p.add_argument("path")
    p.set_defaults(func=cmd_open)

    sub.add_parser("info", help="summarise the active document").set_defaults(func=cmd_info)

    p = sub.add_parser("save", help="export the active document and verify the file")
    p.add_argument("path", help="target path (the extension decides the format)")
    p.add_argument("--as", dest="as_", choices=sorted(swcore.EXPORT_FORMATS),
                   help="override the format instead of taking it from the extension")
    p.set_defaults(func=cmd_save)

    sub.add_parser("dialogs",
                   help="list the SOLIDWORKS windows; finds a modal dialog").set_defaults(
        func=cmd_dialogs)

    p = sub.add_parser("dismiss", help="answer a modal dialog so a wedged call recovers")
    p.add_argument("--handle", type=int, help="window handle from `dialogs`")
    p.add_argument("--button", choices=["cancel", "ok"], default="cancel",
                   help="cancel (default, safe) or ok (may save/overwrite)")
    p.set_defaults(func=cmd_dismiss)

    p = sub.add_parser("close", help="close documents without modal dialogs")
    p.add_argument("title", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--keep-changes", action="store_true",
                   help="do not discard unsaved changes (may raise a modal dialog)")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("quit", help="exit SOLIDWORKS")
    p.add_argument("--force", action="store_true",
                   help="discard unsaved changes and exit")
    p.set_defaults(func=cmd_quit)

    p = sub.add_parser("api", help="offline COM signature lookup")
    p.add_argument("name", nargs="?")
    p.add_argument("--enum")
    p.set_defaults(func=cmd_api)

    p = sub.add_parser("enum", help="offline constant lookup")
    p.add_argument("keyword")
    p.set_defaults(func=cmd_api)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except SwError as exc:
        print("ERROR  : %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
