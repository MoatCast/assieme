"""Writes tests/data/cross_names.json (every name one session's module uses from another session's module) and
tests/data/methods.json (every socket method). Run from the repo root with the repo's .venv Python:
    .venv/bin/python runtime/crates/assieme-brain/record/cross_names.py"""
import ast
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
PKG = os.path.join(ROOT, 'assieme')
CRATE = os.path.join(ROOT, 'runtime/crates/assieme-brain')


def owners():
    """Module → owner, and module.name → owner for names the foundation ported inside other modules."""
    mods, names = {}, {}
    for line in open(os.path.join(CRATE, 'OWNERS.md')):
        m = re.match(r'\| ([\w.]+) \| (\w+) \|', line)
        if m and m.group(1) not in ('Module', 'Name'):
            (names if '.' in m.group(1) else mods)[m.group(1)] = m.group(2)
    return mods, names


def modules():
    return sorted(f[:-3] for f in os.listdir(PKG) if f.endswith('.py') and f not in ('__init__.py', '__main__.py'))


def uses(caller):
    """Every (module, name) `caller` uses from a sibling module."""
    tree = ast.parse(open(os.path.join(PKG, caller + '.py')).read())
    aliases, found = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            for a in node.names:
                if node.module is None:
                    aliases[a.asname or a.name] = a.name
                else:
                    found.add((node.module, a.name))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            found.add((aliases[node.value.id], node.attr))
    return found


def kind(module, name):
    """What `name` is in `module`, or None when the module never defines it (a local variable that shadows the
    module's name at the call site, like a dict called `tasks`)."""
    tree = ast.parse(open(os.path.join(PKG, module + '.py')).read())
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return 'function'
        if isinstance(node, ast.ClassDef) and node.name == name:
            return 'class'
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            names = t.elts if isinstance(t, ast.Tuple) else [t]
            if any(isinstance(n, ast.Name) and n.id == name for n in names):
                return 'constant'
        if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == name for a in node.names):
            return f'reexport of {node.module}.{name}'
    return None


def main():
    mods, named = owners()
    missing = [m for m in modules() if m not in mods]
    if missing:
        sys.exit(f'OWNERS.md has no owner for {missing}')
    table = {}
    for caller in modules():
        for module, name in uses(caller):
            if module not in mods:
                continue
            if named.get(f'{module}.{name}', mods[module]) != mods[caller]:
                table.setdefault((module, name), set()).add(caller)
    out = [{'module': m, 'name': n, 'kind': kind(m, n), 'callers': sorted(c)} for (m, n), c in sorted(table.items())]
    out = [e for e in out if e['kind']]
    with open(os.path.join(CRATE, 'tests/data/cross_names.json'), 'w') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    sys.path.insert(0, ROOT)
    from assieme import cli
    with open(os.path.join(CRATE, 'tests/data/methods.json'), 'w') as f:
        json.dump(cli.handler_names(), f, indent=1)
        f.write('\n')
    with open(os.path.join(CRATE, 'src/method_names.rs'), 'w') as f:
        f.write('//! Every socket method, written by `record/cross_names.py` from `cli.handler_names()`.\n\n')
        f.write('pub const METHOD_NAMES: &[&str] = &[\n' + ''.join(f'    "{n}",\n' for n in cli.handler_names()) + '];\n')
    subprocess.run(['rustfmt', '--edition', '2024', '--config-path', os.path.join(CRATE, '../..'), os.path.join(CRATE, 'src/method_names.rs')], check=True)
    print(f'{len(out)} cross-session names, {len(cli.handler_names())} methods')


if __name__ == '__main__':
    main()
