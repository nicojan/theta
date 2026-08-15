#!/usr/bin/env python3
"""Report which ObjC symbols Theta depends on have disappeared between two Instagram builds.

    ./scripts/compat.py <old-Instagram.app> <new-Instagram.app>

Example:
    ./scripts/compat.py /tmp/ig441/Payload/Instagram.app input/Payload/Instagram.app

Scrapes the class names, selectors and ivars that Theta references out of Source/ and
Include/, then checks each one against the ObjC metadata of every Mach-O in both bundles.
Anything present in the old build and missing from the new one is a hook that will
silently stop firing.

Two limits worth knowing. Selector and ivar presence is checked globally — across all
classes, not per class — so a member that MOVED between classes is not flagged. And a
class that still resolves may still have changed its internals. This catches renames and
deletions, which is the common breakage; it is not a substitute for running the build.
"""
import collections
import os
import re
import sys
import pathlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from objcdump import Image  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
MACHO_MAGIC = b"\xcf\xfa\xed\xfe"


def theta_identifiers():
    """Pull the ObjC names Theta references out of its own source."""
    src = []
    for sub in ("Source", "Include"):
        for root, _dirs, files in os.walk(REPO / sub):
            for f in files:
                if f.endswith((".m", ".h", ".x", ".xm")):
                    src.append(pathlib.Path(root) / f)
    blob = "\n".join(p.read_text(errors="replace") for p in src)

    classes = set()
    classes |= set(re.findall(r'objc_getClass\("([^"]+)"\)', blob))
    classes |= set(re.findall(r'NSClassFromString\(@"([^"]+)"\)', blob))
    classes |= set(re.findall(r"%c\(([A-Za-z_][A-Za-z0-9_]*)\)", blob))
    # ThetaFirstClass(@[ @"A", @"B" ]) fallback lists and other bare class literals
    classes |= {s for s in re.findall(r'@"([A-Za-z_][A-Za-z0-9_.]{3,})"', blob)
                if s.startswith(("IG", "_TtC", "FB", "UICKeyChainStore"))}

    sels = set(re.findall(r"@selector\(([^)]+)\)", blob))
    ivars = set(re.findall(r'@"(_[A-Za-z0-9_]+)"', blob))
    return sorted(classes), sorted(sels), sorted(ivars)


def index_bundle(app):
    """Merge the ObjC metadata of every Mach-O in an .app bundle."""
    merged = {}
    for root, _dirs, files in os.walk(app):
        for f in files:
            p = os.path.join(root, f)
            try:
                if open(p, "rb").read(4) != MACHO_MAGIC:
                    continue
                merged.update({k: v for k, v in Image(p).classes().items()
                               if k not in merged})
            except Exception:
                continue
    if not merged:
        sys.exit(f"error: no ObjC metadata found under {app}")

    alias = collections.defaultdict(set)
    for k in merged:
        # _TtC<len><module><len><class>  ->  remember the trailing plain class name
        m = re.match(r"^_TtC(\d+)(.*)$", k)
        if not m:
            continue
        rest = m.group(2)[int(m.group(1)):]
        m2 = re.match(r"^(\d+)(.*)$", rest)
        if m2:
            alias[m2.group(2)[:int(m2.group(1))]].add(k)

    sels, ivars = set(), set()
    for v in merged.values():
        sels.update(v["methods"])
        ivars.update(v["ivars"])
    return {"classes": set(merged), "alias": alias, "sels": sels, "ivars": ivars}


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    old, new = sys.argv[1], sys.argv[2]

    print(f"indexing {old} ...")
    A = index_bundle(old)
    print(f"indexing {new} ...")
    B = index_bundle(new)
    print(f"  old: {len(A['classes'])} classes, {len(A['ivars'])} ivars, {len(A['sels'])} selectors")
    print(f"  new: {len(B['classes'])} classes, {len(B['ivars'])} ivars, {len(B['sels'])} selectors")

    classes, sels, ivars = theta_identifiers()

    def has_class(idx, n):
        return n in idx["classes"] or bool(idx["alias"].get(n))

    broke, dead, ok = [], [], 0
    checks = ([("class", c, has_class) for c in classes] +
              [("selector", s, lambda i, n: n in i["sels"]) for s in sels] +
              [("ivar", v, lambda i, n: n in i["ivars"]) for v in ivars])

    for kind, name, test in checks:
        in_old, in_new = test(A, name), test(B, name)
        if in_old and not in_new:
            broke.append((kind, name))
        elif not in_old:
            dead.append((kind, name))
        else:
            ok += 1

    print()
    print("=" * 70)
    print(f"BROKE ({len(broke)}) — present in old, gone in new. These hooks stop firing.")
    print("=" * 70)
    for kind, n in broke:
        print(f"  [{kind}] {n}")
    if not broke:
        print("  (none)")

    print()
    print(f"Already absent in the OLD build ({len(dead)}) — pre-existing dead refs, plus")
    print("Theta's own classes and UIKit/Foundation symbols that live outside the bundle.")
    print("Re-run with -v to list them.")
    if "-v" in os.environ.get("COMPAT_OPTS", ""):
        for kind, n in dead:
            print(f"  [{kind}] {n}")

    print()
    print(f"OK on both: {ok}")
    return 1 if broke else 0


if __name__ == "__main__":
    sys.exit(main())
