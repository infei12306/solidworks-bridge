#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Look up REAL SOLIDWORKS COM signatures and constants, offline.

The generated early-binding stubs contain the authoritative parameter lists -
makepy's docstrings only carry the one-line description, and the online API help
is organised differently from the type library.  So this module parses the stub
file it ships with, which means it answers questions like "which interface owns
GetBodies2 and what are its arguments?" without launching SOLIDWORKS.

    python sw/swapi.py GetBodies2
    python sw/swapi.py IPartDoc
    python sw/swapi.py --enum Template
    python sw/swapi.py --enum-value swOpenDocOptions_Silent

It backs the skill's `swbridge api` / `swbridge enum` commands.
"""

import argparse
import os
import re
import sys

DEFAULT_STUBS = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "swbridge", "stubs"
)
STUB_NAME = "_sldworks_gen.py"
ENUM_NAME = "swenums.py"

CLASS_RE = re.compile(r"^class (\w+)\(([^)]*)\)")
DEF_RE = re.compile(r"^\tdef (\w+)\((.*)$")


def _stub_path(stubs_dir, name):
    path = os.path.join(stubs_dir, name)
    if not os.path.isfile(path):
        raise SystemExit(
            "missing %s\nRun scripts/genstubs.py first (it writes into %s)." % (path, stubs_dir))
    return path


def iter_classes(stubs_dir=DEFAULT_STUBS):
    """Yield (class_name, bases, [ (method, signature, docstring) ]) lazily.

    makepy wraps long parameter lists over several lines, so a def is only
    complete once the accumulated text ends with '):'."""
    path = _stub_path(stubs_dir, STUB_NAME)
    cls = bases = None
    methods = []
    pending = None
    with open(path, "r", encoding="mbcs", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            m = CLASS_RE.match(line)
            if m:
                if cls:
                    yield cls, bases, methods
                cls, bases, methods = m.group(1), m.group(2), []
                pending = None
                continue
            if cls is None:
                continue
            if pending is not None:
                pending += " " + line.strip()
                if pending.rstrip().endswith("):"):
                    methods.append(_split(pending))
                    pending = None
                continue
            d = DEF_RE.match(line)
            if d:
                text = line.strip()[4:]
                if text.rstrip().endswith("):"):
                    methods.append(_split(text))
                else:
                    pending = text
    if cls:
        yield cls, bases, methods


def _split(text):
    """'Name(self, A=x, B=y):' -> ('Name', 'A=x, B=y')"""
    name, _, rest = text.partition("(")
    params = rest.rstrip()
    if params.endswith(":"):          # strip '):' first, then ')' - order matters
        params = params[:-1]
    if params.endswith(")"):
        params = params[:-1]
    clean = []
    for part in (p.strip() for p in params.split(",")):
        if "=" in part:
            part = part.split("=", 1)[0].strip()
        if part and part != "self":
            clean.append(part)
    return name.strip(), clean


def _load_stub_module(stubs_dir):
    path = _stub_path(stubs_dir, STUB_NAME)
    import importlib.util
    spec = importlib.util.spec_from_file_location("_swstub_lookup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prop_names(iface, stubs_dir=DEFAULT_STUBS):
    """Property-mapped members of an interface.

    pywin32 resolves these to their VALUE on plain attribute access, which is why
    `obj.GetTypeName2` can be the string 'RefPlane' on one code path while the
    same name needs calling on another.  Anything listed here must be read with
    getv() and must NOT be called; everything else is a method.

    (Measured the hard way: using `feature.GetTypeName2()` raised
    TypeError: 'str' object is not callable.)"""
    mod = _load_stub_module(stubs_dir)
    cls = getattr(mod, iface, None)
    if cls is None:
        return None
    return sorted((getattr(cls, "_prop_map_get_", None) or {}).keys())


def member_kind(iface, name, stubs_dir=DEFAULT_STUBS):
    props = prop_names(iface, stubs_dir)
    if props is None:
        return "unknown-interface"
    return "PROPERTY (read with getv, do not call)" if name in props else "method"


def find_method(name, stubs_dir=DEFAULT_STUBS):
    hits = []
    for cls, bases, methods in iter_classes(stubs_dir):
        for meth, params in methods:
            if meth == name:
                hits.append((cls, params))
    return hits


def find_class(name, stubs_dir=DEFAULT_STUBS):
    for cls, bases, methods in iter_classes(stubs_dir):
        if cls == name:
            return bases, methods
    return None


def enum_search(keyword, stubs_dir=DEFAULT_STUBS):
    ns = _load_enums(stubs_dir)
    kw = keyword.lower()
    return sorted(n for n in ns if kw in n.lower())


def enum_value(name, stubs_dir=DEFAULT_STUBS):
    ns = _load_enums(stubs_dir)
    return ns.get(name)


def _load_enums(stubs_dir):
    path = _stub_path(stubs_dir, ENUM_NAME)
    import importlib.util
    spec = importlib.util.spec_from_file_location("_swenums_lookup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ALL


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="method or interface name")
    ap.add_argument("--stubs", default=DEFAULT_STUBS)
    ap.add_argument("--enum", metavar="KEYWORD", help="search constant names")
    ap.add_argument("--enum-value", metavar="CONST", help="print one constant's value")
    ap.add_argument("--props", metavar="INTERFACE",
                    help="list the property-mapped members of an interface "
                         "(read those with getv, never call them)")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    if args.enum:
        names = enum_search(args.enum, args.stubs)
        print("%d constant(s) matching %r" % (len(names), args.enum))
        for n in names[:args.limit]:
            print("  %-58s %s" % (n, enum_value(n, args.stubs)))
        if len(names) > args.limit:
            print("  ... %d more" % (len(names) - args.limit))
        return 0

    if args.enum_value:
        value = enum_value(args.enum_value, args.stubs)
        print("%s = %s" % (args.enum_value, value))
        return 0 if value is not None else 1

    if not args.name and not args.props:
        ap.error("give a method/interface name, or --enum/--enum-value/--props")

    if args.props:
        props = prop_names(args.props, args.stubs)
        if props is None:
            print("no interface named %r" % args.props)
            return 1
        print("%s: %d property member(s) - read these with getv(), never call them"
              % (args.props, len(props)))
        for p in props[:args.limit]:
            print("  %s" % p)
        if len(props) > args.limit:
            print("  ... %d more" % (len(props) - args.limit))
        return 0

    hits = find_method(args.name, args.stubs)
    if hits:
        print("%s  (%d interface(s))" % (args.name, len(hits)))
        for cls, params in hits:
            print("  %-24s [%s]\n      %s(%s)"
                  % (cls, member_kind(cls, args.name, args.stubs), args.name,
                     ", ".join(params)))
        return 0

    info = find_class(args.name, args.stubs)
    if info:
        bases, methods = info
        print("class %s(%s)  - %d method(s)" % (args.name, bases, len(methods)))
        for meth, params in methods[:args.limit]:
            print("  %s(%s)" % (meth, ", ".join(params)))
        if len(methods) > args.limit:
            print("  ... %d more" % (len(methods) - args.limit))
        return 0

    print("nothing named %r (neither a method nor an interface)" % args.name)
    return 1


if __name__ == "__main__":
    sys.exit(main())
