#!/usr/bin/env python3
"""Per-class hook check between two Instagram versions.

compat.py asks whether a name exists *anywhere* in the bundle, so a selector that moved off
the hooked class (447's `_didPressFollowButton`) passes it. This resolves each literal
`NullHookMessage*(<class>, <selector>)` call in Source/ to its class and checks the selector
on *that* class in both versions. Calls whose class is computed at runtime are skipped
(about half), so a clean run is necessary, not sufficient — Hook Activity covers the rest.

Usage (dumps are objcdump.py JSON, one per Mach-O, in a directory per version):
  mkdir -p /tmp/dOld /tmp/dNew
  ./scripts/objcdump.py <old>/Instagram.app/Instagram /tmp/dOld/main.json
  ./scripts/objcdump.py <new>/Instagram.app/Instagram /tmp/dNew/main.json
  ./scripts/hookcheck.py Source /tmp/dOld /tmp/dNew
"""
import json,re,sys,glob,os
src=sys.argv[1]; a=sys.argv[2]; b=sys.argv[3]
def load(d):
    m={}
    for f in glob.glob(d+'/*.json'):
        for c,v in json.load(open(f)).items(): m.setdefault(c,set()).update(v['methods'])
    return m
A=load(a); B=load(b)
pairs=set()
var_re=re.compile(r'Class\s+(\w+)\s*=\s*(?:objc_getClass\("([^"]+)"\)|NSClassFromString\(@"([^"]+)"\)|ThetaFirstClass\(@\[\s*@"([^"]+)")')
hook_re=re.compile(r'NullHookMessage\w*\(\s*(?:objc_getClass\("([^"]+)"\)|NSClassFromString\(@"([^"]+)"\)|(\w+))\s*,\s*(?:@selector\(([^)]+)\)|NSSelectorFromString\(@"([^"]+)"\))')
for f in glob.glob(src+'/**/*.m',recursive=True)+glob.glob(src+'/**/*.x*',recursive=True):
    t=open(f,errors='ignore').read()
    vars={}
    for m in var_re.finditer(t): vars[m.group(1)]=m.group(2) or m.group(3) or m.group(4)
    for m in hook_re.finditer(t):
        cls=m.group(1) or m.group(2) or vars.get(m.group(3))
        sel=m.group(4) or m.group(5)
        if cls and sel: pairs.add((os.path.relpath(f,src),t[:m.start()].count('\n')+1,cls,sel))
for f,l,c,s in sorted(pairs):
    ia = s in A.get(c,set()); ib = s in B.get(c,set())
    if ia and not ib: print(f'BROKE  {f}:{l}  {c} {s}')
print(len(pairs),'resolved pairs checked', file=sys.stderr)
