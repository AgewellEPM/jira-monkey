#!/usr/bin/env python3
"""Backport the terminal presentation to an unchanged 0.6.0 runtime.

This only creates a separate payload. It does not open user state, change a
launcher, start a model, or control a terminal. The retained base manifest must
match before copying; action handlers and completion rules stay byte-for-byte
unchanged. Qualify the staged payload before selecting it for the next launch.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil


VERSION = '0.6.0+terminal.1'


def definition(text, name):
    nodes = ast.parse(text).body
    for part in name.split('.'):
        found = next(n for n in nodes if getattr(n, 'name', None) == part or
                     isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == part for t in n.targets))
        nodes = getattr(found, 'body', [])
    return found


def extract(text, name):
    node = definition(text, name)
    return ''.join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])


def replace(text, name, replacement):
    node = definition(text, name)
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement + ''.join(lines[node.end_lineno:])


def stage(base, output, source):
    if base.is_symlink() or output.exists():
        raise ValueError('Use an ordinary, verified base and a new output directory')
    manifest = json.loads((base / 'installation.json').read_text())
    if manifest['version'] != '0.6.0':
        raise ValueError('This presentation backport requires the recorded 0.6.0 base')
    for name, expected in manifest['files'].items():
        path = base / name
        if (Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink()
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('Base manifest mismatch: ' + name)
    originals = {name: (base / 'monkey' / name).read_text() for name in
                 ('presentation.py', 'browser.py', 'ui.py', '__init__.py')}
    changed = dict(originals)
    presentation = (source / 'monkey/presentation.py').read_text()
    for name in ('STYLE', 'width', 'banner', 'reply'):
        changed['presentation.py'] = replace(changed['presentation.py'], name, extract(presentation, name))
    changed['presentation.py'] = replace(changed['presentation.py'], 'width',
        extract(presentation, 'width') + '\n\n' + extract(presentation, 'padded'))

    render = extract((source / 'monkey/browser.py').read_text(), 'Browser.render')
    # General build/research commands are absent from the 0.6.0 menu. Do not
    # carry their copy or handlers into this presentation-only installation.
    start = render.index('        elif self.section in {5,6}:')
    end = render.index('        elif self.section == 0:', start)
    render = render[:start] + render[end:]
    render = render.replace("j.get('agent_state') or (j[\"stage\"] if j[\"work_state\"] == \"RUNNING\" else j[\"work_state\"])",
                            'j["stage"] if j["work_state"] == "RUNNING" else j["work_state"]')
    changed['browser.py'] = replace(changed['browser.py'], 'Browser.render', render)
    changed['browser.py'] = changed['browser.py'].replace('from .presentation import delivery, fit, width',
                                                        'from .presentation import delivery, fit, padded, width')
    changed['ui.py'] = changed['ui.py'].replace('🐒 Monkey', '🍌 Monkey')
    changed['__init__.py'] = changed['__init__.py'].replace('VERSION = "0.6.0"', 'VERSION = "' + VERSION + '"')

    # Check the boundary directly: no action, signing, scheduling, input binding,
    # event dispatcher, or existing response-content implementation was changed.
    for node in definition(originals['browser.py'], 'Browser').body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != 'render':
            assert extract(originals['browser.py'], 'Browser.' + node.name) == extract(changed['browser.py'], 'Browser.' + node.name)
    for node in ast.parse(originals['presentation.py']).body:
        if isinstance(node, ast.FunctionDef) and node.name not in {'width', 'banner', 'reply'}:
            assert extract(originals['presentation.py'], node.name) == extract(changed['presentation.py'], node.name)
    assert changed['ui.py'].replace('🍌 Monkey', '🐒 Monkey') == originals['ui.py']
    for name, content in changed.items():
        compile(content, name, 'exec')

    output.mkdir(parents=True, mode=0o700)
    # Copy only manifest-owned regular files; never copy state or caches.
    for name in manifest['files']:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base / name, target)
    for name, content in changed.items():
        (output / 'monkey' / name).write_text(content)
    manifest['version'] = VERSION
    manifest['terminal_refresh'] = {'base_version': '0.6.0', 'source': str(source),
        'scope': 'Presentation only; original commands, worker, providers, state schema and sign-off rules',
        'native_keyboard_verification': 'Blocked by TinkyVision Terminal sensitive-app policy'}
    manifest['files'] = {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                         for name in manifest['files']}
    (output / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    difference = ''.join(''.join(difflib.unified_diff(originals[name].splitlines(keepends=True),
        changed[name].splitlines(keepends=True), fromfile='0.6.0/monkey/' + name,
        tofile=VERSION + '/monkey/' + name)) for name in changed)
    return {'version': VERSION, 'payload': str(output), 'base_files_verified': len(manifest['files']),
            'changed_files': ['monkey/' + name for name in changed], 'diff': difference}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = stage(args.base, args.output, Path(__file__).resolve().parents[1])
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'diff'}))


if __name__ == '__main__':
    main()
