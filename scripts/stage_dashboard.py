#!/usr/bin/env python3
"""Stage the web/terminal refresh on the installed 0.6.0 core without a worker upgrade."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil

import stage_terminal_refresh as terminal


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Unrecognized installation hook: ' + before[:80])
    return text.replace(before, after, 1)


def stage(base, output, report_path):
    source = Path(__file__).resolve().parents[1]
    terminal.VERSION = '0.6.0+dashboard.1'
    report = terminal.stage(base, output, source)
    app_source = (source / 'monkey/app.py').read_text()
    source_lines = app_source.splitlines(keepends=True)
    cls = terminal.definition(app_source, 'App')
    init = terminal.definition(app_source, 'App.__init__')
    header = ''.join(source_lines[cls.lineno - 1:init.lineno - 1])
    app = (output / 'monkey/app.py').read_text()
    app = once(app, 'class App:\n', header)
    app = once(app, 'from .gateway import Gateway, ORIGIN\n', 'from .gateway import Gateway, ORIGIN\nfrom .operator_context import REQUEST_FOCUS, UNBOUND\n')
    app = once(app, '        self.gateway = Gateway(self)\n', '        self.gateway = Gateway(self)\n        self.dashboard = None\n')
    app = app.replace("ORIGIN.get() != 'operator_repl'", "ORIGIN.get() not in {'operator_repl', 'operator_dashboard'}")
    app = app.replace("ORIGIN.get() == 'operator_repl'", "ORIGIN.get() in {'operator_repl', 'operator_dashboard'}")
    start = app_source.index("        if operation == 'dashboard':")
    end = app_source.index("        if operation == 'services':", start)
    app = once(app, "        if operation == 'services':", app_source[start:end] + "        if operation == 'services':")
    app = once(app, '\n            await self.gateway.close()\n', '\n            if self.dashboard:\n                await self.dashboard.close()\n            await self.gateway.close()\n')
    app = once(app, "api start | status | stop    Monkey's own local MCP/API, in this foreground application\n",
               "api start | status | stop    Monkey's own local MCP/API, in this foreground application\ndashboard [start|status|stop] Web workspace and chat for this same foreground session\n")
    (output / 'monkey/app.py').write_text(app)

    cli = (output / 'monkey/cli.py').read_text()
    cli_source = (source / 'monkey/cli.py').read_text()
    start = cli_source.index("    dashboard = sub.add_parser('dashboard'")
    end = cli_source.index('\n', cli_source.index("    dashboard.add_argument('--port'", start))
    cli = once(cli, "    api.add_argument('--port',type=int,default=0)\n", "    api.add_argument('--port',type=int,default=0)\n" + cli_source[start:end] + '\n')
    cli = once(cli, "EXACT |= {'services','disconnect','api'}\n", "EXACT |= {'services','disconnect','api'}\nEXACT |= {'dashboard'}\n")
    start = cli_source.index("        if args.command == 'dashboard' and args.action == 'start':")
    end = cli_source.index('\n', cli_source.index('            return await repl(app, json_mode=args.json)', start))
    cli = once(cli, "        if args.command=='api' and args.action=='start':", cli_source[start:end] + '\n' + "        if args.command=='api' and args.action=='start':")
    (output / 'monkey/cli.py').write_text(cli)
    presentation = (output / 'monkey/presentation.py').read_text()
    new_content = """def content(value, fallback):
    if isinstance(value, dict) and 'command' in value and 'message' in value:
        return value['message'] + ('\\nEvidence: /events ' + value['job_id'] if value.get('job_id') else '')
    if isinstance(value, dict) and 'dashboard_url' in value:
        return value['message'] + ('\\n\\n' + value['dashboard_url'] if value.get('dashboard_url') else '')
"""
    presentation = once(presentation, 'def content(value, fallback):\n', new_content)
    (output / 'monkey/presentation.py').write_text(presentation)
    for name in ('dashboard.py', 'operator_context.py'):
        shutil.copy2(source / 'monkey' / name, output / 'monkey' / name)
    shutil.copytree(source / 'monkey/web', output / 'monkey/web')
    shutil.copy2(source / 'tests/test_dashboard.py', output / 'tests/test_dashboard.py')
    for path in (output / 'monkey').glob('*.py'): compile(path.read_text(), str(path), 'exec')
    manifest = json.loads((output / 'installation.json').read_text())
    manifest.pop('terminal_refresh', None)
    manifest['dashboard_refresh'] = {'base_version': '0.6.0', 'source': str(source),
        'scope': 'Modern terminal and authenticated foreground web dashboard; existing 0.6.0 worker, provider adapters and state schema',
        'operator_channel': 'operator_dashboard; existing MCP/API clients retain their original restricted authority'}
    manifest['files'] = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob('*') if path.is_file() and path.name != 'installation.json' and '__pycache__' not in path.parts}
    (output / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    report['scope'] = manifest['dashboard_refresh']
    report['changed_files'] = [name for name in manifest['files']
        if not (base / name).is_file() or (base / name).read_bytes() != (output / name).read_bytes()]
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'diff'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    stage(args.base, args.output, args.report)
