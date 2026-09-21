"""Run a bounded local-model coding case using an installed Monkey runtime.

Invoke with the installed Python and -I -B, or pass an install.py --payload with
the dependency interpreter. The installed files are checked before admitting
work. This retains actual model/tool evidence without signing off the result.
"""
import argparse
import asyncio
import datetime as dt
import json
import hashlib
from pathlib import Path
import runpy
import sys
import time


# These outcome cases are never included in the model's objective or context.
# They run only after its loop settles, inside the same restricted native checker.
CALCULATOR_CHECK = '''import unittest
from calc import add

class CalculatorContract(unittest.TestCase):
    def test_positive_and_negative(self):
        for left, right, expected in [(17, 25, 42), (-19, -6, -25), (-8, 13, 5)]:
            with self.subTest(left=left, right=right):
                self.assertEqual(add(left, right), expected)
                self.assertEqual(add(right, left), expected)

    def test_zero_and_large_integer(self):
        for left, right, expected in [(0, 0, 0), (0, -23, -23), (2**80, 7, 2**80+7)]:
            with self.subTest(left=left, right=right):
                self.assertEqual(add(left, right), expected)

    def test_fractional(self):
        self.assertEqual(add(1.25, -0.5), 0.75)

suite = unittest.defaultTestLoader.loadTestsFromTestCase(CalculatorContract)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun == 3 else 1)
'''


async def check_calculator(files, root):
    from monkey.verification import successful_command
    authored = await files.run(['python', '-m', 'unittest', '-v', 'test_calc'],
        root / 'authored-test-check')
    independent = await files.run(['python', '-c', CALCULATOR_CHECK],
        root / 'independent-contract-check')
    return {'performed': True, 'authored_tests': authored, 'independent_contract': independent,
        'passed': successful_command(authored) and successful_command(independent),
        'scope': 'Authored unittest module and fixed arithmetic cases withheld from model context; not adversarial or independent attestation.'}


def installed_runtime(module, expected=None):
    """Accept a verified install.py payload or an installed wheel, never cwd source."""
    runtime = Path(module.__file__).resolve().parents[1]
    if expected is not None and runtime != Path(expected).expanduser().resolve():
        raise RuntimeError('Imported Monkey does not match the selected installed payload')
    manifest = runtime / 'installation.json'
    if manifest.is_file():
        receipt = json.loads(manifest.read_text())
        for name, sha256 in receipt['files'].items():
            path = runtime / name
            if not path.resolve().is_relative_to(runtime) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
                raise RuntimeError('Installed file differs from its manifest: ' + name)
        if receipt['version'] != module.VERSION:
            raise RuntimeError('Installed version differs from its manifest')
        return {'root': str(runtime), 'kind': 'install.py', 'files_verified': len(receipt['files']),
                'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    from importlib.metadata import distribution
    distribution = distribution('monkey-workdesk')
    if (not runtime.is_relative_to(Path(sys.prefix).resolve()) or
            distribution.locate_file('monkey/__init__.py').resolve() != Path(module.__file__).resolve() or
            distribution.version != module.VERSION):
        raise RuntimeError('Qualification must import an installed Monkey application, not source')
    return {'root': str(runtime), 'kind': 'installed wheel', 'version': distribution.version}


async def run(args):
    import monkey
    from monkey.app import App
    from monkey.platform_files import private_directory, write_private
    installed = installed_runtime(monkey, getattr(args, 'payload', None))
    cases = runpy.run_path(str(Path(__file__).with_name('local_workflow_cases.py')))
    case = getattr(args, 'case', 'calculator')
    objective = cases['CASES'][case]['objective']
    root = Path(args.output).expanduser().absolute()
    if root.exists():
        raise RuntimeError('Use a new output directory; previous budgets and evidence must be retained')
    private_directory(root)
    workspace = root / 'workspace'
    private_directory(workspace)
    app = App(root / 'state')
    if getattr(args, 'capture_wire', False):
        observer = runpy.run_path(str(Path(__file__).with_name('capture_model_fixture.py')))
        observer['attach'](app.http.client, root / 'provider-wire')
    samples = []
    report = {'schema': 'monkey.installed-local-agent-qualification.v1',
              'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'python': sys.executable, 'application': monkey.__file__, 'version': monkey.VERSION,
              'installed_runtime': installed, 'case': case, 'objective': objective,
              'model': args.model, 'request_timeout_seconds': args.request_timeout,
              'diagnostic_wire_capture': bool(getattr(args, 'capture_wire', False)),
              'physical_terminal_ui_tested': False, 'operator_signoff': False,
              'qualification': 'One private coding case with an outcome oracle; not a general model accuracy benchmark'}
    started = time.monotonic()
    try:
        app.db.configure({'agent_max_calls':16, 'agent_max_tools':32,
                          'agent_max_seconds':args.agent_seconds, 'request_timeout':args.request_timeout,
                          'model_routes': {role:[{'provider':'ollama', 'model':args.model, 'digest':''}]
                                           for role in ('planning','execution','reflection')}})
        request = await app.dispatch('build', text=objective, path=str(workspace))
        jid = request['job_id']
        report['job_id'] = jid
        print(json.dumps({'job_id':jid, 'application':monkey.__file__, 'model':args.model}), flush=True)
        seq = 0
        while app.execution.task and not app.execution.task.done():
            tick = time.perf_counter()
            await app.dispatch('status')
            samples.append((time.perf_counter() - tick) * 1000)
            for event in app.db.events(seq, job_id=jid):
                seq = event['seq']
                if event['data'].get('message'):
                    print(event['kind'] + ': ' + event['data']['message'][:200], flush=True)
            await asyncio.sleep(.25)
        await app.execution.task
        result = app.agent.view(app.db.job(jid))
        report['result'] = result
        report['passed'] = False
        report['outcome_checks'] = {'performed': False,
            'reason': 'The work loop did not produce a result ready for inspection.'}
        if result['state'] == 'AWAITING_REVIEW':
            async with app.audit.run(jid, 'qualification-check'):
                files = app.agent.workspace(app.db.job(jid))
                report['outcome_checks'] = await (check_calculator(files, root) if case == 'calculator'
                    else cases['check_sprint_summary'](files, root))
            report['passed'] = report['outcome_checks']['passed']
        app.audit.verify()
        exported = await app.dispatch('audit-export', target=jid)
        report['export'] = exported
        from monkey.audit import verify_file
        report['verification'] = await asyncio.to_thread(verify_file, exported['path'], exported['signer_fingerprint'])
        report['provider_calls'] = app.db.records('provider_calls')
    except BaseException as error:
        report.update(passed=False, error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        report['status_samples'] = len(samples)
        report['status_p95_ms'] = sorted(samples)[int(len(samples) * .95)] if samples else None
        await app.close()
        write_private(root / 'result.json', (json.dumps(report, indent=2) + '\n').encode())
        print(json.dumps({'passed':report.get('passed',False), 'report':str(root / 'result.json'),
                          'elapsed_seconds':report['elapsed_seconds']}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, help='Exact already-installed local Ollama tag')
    parser.add_argument('--output', required=True)
    parser.add_argument('--payload', type=Path, help='Exact installed install.py payload; verified against its manifest')
    parser.add_argument('--case', choices=['calculator', 'sprint_summary'], default='calculator')
    parser.add_argument('--capture-wire', action='store_true', help='Retain bounded private fixture requests and provisional streams; never execute the captured text')
    parser.add_argument('--request-timeout', type=int, choices=range(5, 181), default=60,
                        metavar='SECONDS', help='Original per-request deadline, 5–180 seconds (default: 60)')
    parser.add_argument('--agent-seconds', type=int, choices=range(60, 1801), default=600,
                        metavar='SECONDS', help='Original end-to-end agent budget, 60–1800 seconds (default: 600)')
    args = parser.parse_args()
    if args.payload:
        sys.path.insert(0, str(args.payload.expanduser().resolve()))
    raise SystemExit(asyncio.run(run(args)))
