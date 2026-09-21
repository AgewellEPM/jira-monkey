"""Exercise the installed coding qualifier against correct and misleading fixtures."""
import argparse
import asyncio
import json
from pathlib import Path
import runpy
import sys

import monkey
from monkey.app import App
from monkey.audit import verify_file
from monkey.platform_files import private_directory, write_private
from monkey.workspace import Workspace


async def main(args):
    if not Path(monkey.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError('Use an installed Monkey interpreter with -I -B')
    root = Path(args.output).expanduser().absolute()
    if root.exists():
        raise RuntimeError('Use a new evidence directory')
    private_directory(root)
    check = runpy.run_path(str(Path(__file__).with_name('qualify_local_agent.py')))['check_calculator']
    cases = [('correct', 'a + b', True, True),
             ('misleading_tests', 'a - b', True, False),
             ('empty_tests', 'a + b', False, False)]
    app = App(root / 'state', offline=True)
    report = {'application': monkey.__file__, 'version': monkey.VERSION, 'synthetic_fixtures': True,
        'model_generation': False, 'native_ui_tested': False, 'cases': [], 'passed': False}
    try:
        for name, expression, has_test, expected in cases:
            workspace = root / name
            private_directory(workspace)
            files = Workspace(workspace, app.audit, root / 'preimages' / name)
            async with app.audit.run(None, 'qualification-oracle-fixture'):
                files.mutate('calc.py', 'def add(a, b):\n    return ' + expression + '\n', None)
                # Both addition and subtraction pass this intentionally weak check.
                files.mutate('test_calc.py', ('import unittest\nfrom calc import add\n'
                    'class Authored(unittest.TestCase):\n'
                    '    def test_zero_operand(self): self.assertEqual(add(7, 0), 7)\n')
                    if has_test else '# No tests in this fixture\n', None)
                observed = await check(files, root / ('checks-' + name))
            report['cases'].append({'name': name, 'expected_pass': expected, **observed})
            if observed['passed'] != expected:
                raise AssertionError('Qualification oracle misclassified ' + name)
        app.audit.verify()
        report['export'] = await app.dispatch('audit-export')
        report['verification'] = verify_file(report['export']['path'], report['export']['signer_fingerprint'])
        report['passed'] = True
    finally:
        await app.close()
        write_private(root / 'result.json', (json.dumps(report, indent=2) + '\n').encode())
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
        'report': str(root / 'result.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    asyncio.run(main(parser.parse_args()))
