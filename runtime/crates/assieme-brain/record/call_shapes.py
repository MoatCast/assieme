"""Writes tests/data/call_shapes.json (how other sessions' modules call each declared function and constructor) and
tests/shapes.rs (one call per shape, compiled and never run). Run after cross_names.py, from the repo root:
    .venv/bin/python runtime/crates/assieme-brain/record/call_shapes.py

A shape is (positional count, keyword names, which arguments are a literal None). The Rust call passes `any()` for
each argument the caller gives and for each one it leaves at a Python default other than None; one left at a None
default is passed as `None`, so its Rust parameter must be an Option. Exits non-zero when a caller passes an argument
the declaration has no parameter for (test seams such as `run` or `which` are dropped on purpose and allowed).
Methods called on instances are not checked: `ast` cannot tell an instance's class."""
import ast
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cross_names import CRATE, PKG, modules, uses  # noqa: E402

SEAMS = {'run', 'which', 'thread', 'clock', 'popen', 'login_path', 'sampler', 'runner', 'notifier', 'store', 'sleep',
         'python', 'procs', 'install', 'ask', 'codex_cache', 'switches', 'now', 'home', 'sessions', 'herdr_teams',
         'cfg', 'terminals', 'teams', 'file', 'logs', 'start_timeout', 'asm', 'opener', 'notify', 'herdr', 'gh',
         'stdin', 'stdout', 'workdir', 'tz', 'log', 'claude_dir', 'codex_dir', 'me'}
RENAME = {'id_': 'id', 'scripts_': 'scripts', 'ok': 'failed'}
KEYWORDS = {'move', 'type', 'match', 'ref', 'loop', 'use', 'mod', 'impl', 'fn', 'struct'}


def rust_name(name, cls=False):
    name = name.lstrip('_')
    return name.rstrip('_') if cls else name


def rust_params(module, fn, cls=None):
    """The Rust declaration's parameters [(name, type)], or None when it isn't there. With `cls`, its `new`."""
    src = open(os.path.join(CRATE, 'src', module + '.rs')).read()
    start = re.search(rf'(?m)^impl {cls} {{', src).end() if cls and re.search(rf'(?m)^impl {cls} {{', src) else 0
    indent = '    ' if cls else ''
    m = re.compile(rf'(?m)^{indent}pub fn (?:r#)?{fn}(?:<[^>]*>)?\(').search(src, start)
    if not m:
        return None
    depth, i = 1, m.end()
    while depth:
        depth += {'(': 1, ')': -1}.get(src[i], 0)
        i += 1
    params, depth, cur = [], 0, ''
    for ch in src[m.end():i - 1]:
        depth += {'(': 1, '<': 1, '[': 1, ')': -1, '>': -1, ']': -1}.get(ch, 0) if ch != '>' or cur[-1:] != '-' else 0
        if ch == ',' and depth == 0:
            params.append(cur)
            cur = ''
        else:
            cur += ch
    params.append(cur)
    out = []
    for p in (p.strip() for p in params):
        if p and ':' in p and not p.startswith('&'):
            n, t = p.split(':', 1)
            out.append((n.strip(), t.strip()))
    return out


def definition(module, name):
    tree = ast.parse(open(os.path.join(PKG, module + '.py')).read())
    node = next((n for n in tree.body if getattr(n, 'name', None) == name), None)
    if isinstance(node, ast.ClassDef):
        init = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == '__init__'), None)
        return 'class', init
    return 'function', node


def shapes():
    """{(module, name): {shape: [callers]}} for calls of cross-session functions and constructors."""
    found = {}
    for caller in modules():
        tree = ast.parse(open(os.path.join(PKG, caller + '.py')).read())
        aliases = {a.asname or a.name: a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 1
                   and n.module is None for a in n.names}
        direct = {a.asname or a.name: (n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                  and n.level == 1 and n.module for a in n.names}
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            f = call.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in aliases:
                target = (aliases[f.value.id], f.attr)
            elif isinstance(f, ast.Name) and f.id in direct:
                target = direct[f.id]
            else:
                continue
            if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
                shape = ('*', (), ())
            else:
                nones = tuple(sorted([str(i) for i, a in enumerate(call.args) if _none(a)] +
                                     [k.arg for k in call.keywords if _none(k.value)]))
                shape = (len(call.args), tuple(sorted(k.arg for k in call.keywords)), nones)
            found.setdefault(target, {}).setdefault(shape, set()).add(caller)
    return found


def _none(node):
    return isinstance(node, ast.Constant) and node.value is None


def main():
    cross = {(e['module'], e['name']): e for e in json.load(open(os.path.join(CRATE, 'tests/data/cross_names.json')))}
    from cross_names import owners
    mods, _ = owners()
    problems, data, calls = [], [], []
    for (module, name), by_shape in sorted(shapes().items()):
        e = cross.get((module, name))
        if not e or e['kind'] not in ('function', 'class'):
            continue
        kind, node = definition(module, name)
        if kind == 'class' and (node is None or 'Error' in name):
            continue  # exceptions are struct literals in Rust
        for shape, callers in sorted(by_shape.items(), key=str):
            callers = sorted(c for c in callers if c in e['callers'])
            if not callers:
                continue
            data.append({'module': module, 'name': name, 'positional': shape[0], 'keywords': list(shape[1]),
                         'none': list(shape[2]), 'callers': callers})
            fn = 'new' if kind == 'class' else rust_name(name)
            call = rust_call(module, name, fn, kind, node, shape, problems)
            if call:
                calls.append((f'{module}_{fn}_{len(calls)}', call, callers))
    with open(os.path.join(CRATE, 'tests/data/call_shapes.json'), 'w') as f:
        json.dump(data, f, indent=1)
        f.write('\n')
    with open(os.path.join(CRATE, 'tests/shapes.rs'), 'w') as f:
        f.write('//! Generated by `record/call_shapes.py`: one call per way another session\'s Python calls a declared\n'
                '//! function, compiled and never run. A failure to compile is a declaration that does not fit its callers.\n'
                '#![cfg(unix)]\n#![allow(dead_code, unused_must_use, clippy::let_underscore_future, clippy::let_unit_value)]\nuse assieme_brain::*;\n\n'
                'fn any<T>() -> T {\n    unreachable!()\n}\n')
        for fname, call, callers in calls:
            f.write(f'\n/// From {", ".join(callers)}.\nfn {fname}() {{\n    let _ = {call};\n}}\n')
    subprocess.run(['rustfmt', '--edition', '2024', '--config-path', os.path.join(CRATE, '../..'), os.path.join(CRATE, 'tests/shapes.rs')], check=True)
    print(f'{len(data)} call shapes, {len(calls)} calls written')
    if problems:
        sys.exit('\n'.join(problems))


def rust_call(module, name, fn, kind, node, shape, problems):
    args = node.args if node else None
    py = [a.arg for a in args.args] if args else []
    if kind == 'class' or (py and py[0] == 'self'):
        py = py[1:]
    defaults = dict(zip(py[len(py) - len(args.defaults):], args.defaults)) if args else {}
    py += [a.arg for a in args.kwonlyargs] if args else []
    if args:
        defaults.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
    params = rust_params(module, fn, rust_name(name, True) if kind == 'class' else None)
    path = f'{module}::{rust_name(name, True)}::new' if kind == 'class' else \
        f'{module}::{"r#" + fn if fn in KEYWORDS else fn}'
    if params is None:
        problems.append(f'{module}.{name}: no Rust declaration `pub fn {fn}`')
        return None
    if shape[0] == '*':
        return f'{path}({", ".join("any()" for _ in params)})'
    pos, kws, nones = shape
    given = {py[i] for i in range(min(pos, len(py)))} | set(kws)
    if pos > len(py) and not (args and args.vararg):
        problems.append(f'{module}.{name}: called with {pos} positional arguments, Python takes {len(py)}')
    names = {p for p, _ in params}
    record = next((t for _, t in params if t.startswith('Record')), None)
    for g in given:
        r = RENAME.get(g, g)
        if r not in names and g not in SEAMS and not (args and args.kwarg) and not record:
            problems.append(f'{module}.{name}: callers pass `{g}`, the Rust declaration has no such parameter')
    none_names = {py[int(n)] if n.isdigit() and int(n) < len(py) else n for n in nones}
    if record:
        fields = [f'{RENAME.get(g, g)}: any()' for g in sorted(given) if g in py]
        return f'{path}({module}::Record {{ {", ".join(fields + ["..Default::default()"])} }})'
    out = []
    by_rust = {RENAME.get(p, p): p for p in py}
    for pname, ty in params:
        p = by_rust.get(pname, pname)
        if ty.lstrip('&') == 'Value':
            out.append('any()')  # Python's None is JSON null there
        elif p in none_names or (p not in given and p in defaults and _none(defaults[p])):
            out.append('None')
        else:
            out.append('any()')
    return f'{path}({", ".join(out)})'


if __name__ == '__main__':
    main()
