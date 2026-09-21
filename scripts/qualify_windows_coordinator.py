"""Run an installed CLI through a real loopback MCP fixture, without native UI.

All Windows responses are synthetic. This checks transport, durable coordination,
restart, version floors and signed evidence, not Windows/PowerShell execution.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import time


def run(monkey, python, output, old_monkey=None):
    os.umask(0o077)
    root = Path(output).expanduser().absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    monkey, python = str(Path(monkey).absolute()), str(Path(python).absolute())
    script = Path(__file__).resolve().parents[1] / 'tests/windows_mcp_fixture_server.py'
    env = {k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR', 'TEMP', 'TMP', 'SYSTEMROOT', 'WINDIR'}}
    cache = root / 'empty-bytecode-prefix'; cache.mkdir()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPYCACHEPREFIX=str(cache), PYTHONNOUSERSITE='1')
    records = []
    result = {'schema': 'monkey.windows-coordinator-qualification.v1', 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'platform': platform.platform(), 'monkey': monkey, 'python': python,
              'transport': 'real loopback streamable HTTP MCP', 'responses': 'scripted Windows protocol fixture',
              'native_windows_execution': False, 'physical_terminal_ui_tested': False, 'business_writes': 0,
              'source_fixture_sha256': hashlib.sha256(script.read_bytes()).hexdigest()}
    process = None
    def command(*args, success=True, plain=False, executable=None):
        argv = [executable or monkey, '--state', str(root / 'state'), *args]
        started = time.perf_counter()
        value = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=90)
        row = {'argv': argv, 'exit_code': value.returncode, 'elapsed_ms': (time.perf_counter()-started)*1000,
               'stdout': value.stdout.decode(errors='replace'), 'stderr': value.stderr.decode(errors='replace')}
        records.append(row)
        (root / 'commands.json').write_text(json.dumps(records, indent=2)+'\n')
        if (value.returncode == 0) != success:
            raise AssertionError('Unexpected CLI exit; inspect commands.json')
        return row['stdout'].strip() if plain else json.loads(row['stdout'])
    log = (root / 'fixture-server.log').open('wb')
    try:
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
        process = subprocess.Popen([python, '-B', str(script), str(root/'fixture-ledger.json'), str(port)],
                                   cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise AssertionError('Fixture server exited before readiness')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.3): break
            except OSError:
                if time.monotonic() >= deadline: raise TimeoutError('Fixture readiness deadline')
                time.sleep(.05)
        result['fixture_pid'] = process.pid
        result['version'] = command('--version', plain=True)
        config = root/'fixture-connection.json'
        config.write_text(json.dumps({'transport': 'streamable-http', 'url': 'http://127.0.0.1:'+str(port)+'/mcp'}))
        for name in ('owner', 'vcards'): command('connect', name, '--file', str(config))
        snapshot = root/'snapshot.json'
        snapshot.write_text(json.dumps({'source': 'local', 'instance': 'local://monkey', 'key': 'WINQUAL-101',
            'revision': 'synthetic', 'title': 'Qualify Windows protocol coordination', 'body': 'No native actions or provider enrollment.'}))
        jid = command('import', str(snapshot))['job_id']; result['job_id'] = jid
        command('windows-bind', jid, '--server', 'owner', '--workflow-server', 'vcards', '--target-id', 'legacy.win10',
                '--profile-id', 'windows10-x64-csharp-4.x', '--note', 'Local protocol fixture only')
        if old_monkey:
            refused = command('status', executable=old_monkey, success=False)
            assert 'newer than this application' in refused['error']
            result['older_runtime_refused'] = True
        supplied = root/'start-input.json'
        supplied.write_text(json.dumps({'workflowDigest': 'b'*64, 'dryRunDigest': 'c'*64,
                                       'inputEnvelopePath': '/fixture/input.json', 'inputExpectedSHA256': 'e'*64}))
        actions = ['catalog', 'open', 'observe', 'status', 'readiness', 'workflow-start', 'workflow-step',
                   'provider-tick', 'workflow-status', 'workflow-step', 'cleanup-tick', 'workflow-status',
                   'workflow-step', 'workflow-receipt', 'status', 'close']
        for action in actions:
            extra = ['--args-file', str(supplied)] if action == 'workflow-start' else []
            planned = command('windows-plan', jid, action, *extra)['tool_plan']
            returned = command('tool-run', jid, '--hash', planned['plan_hash'], '--note', 'Inspected exact synthetic '+action)
            assert returned['delivery'] == 'RETURNED_UNVERIFIED', returned['delivery']
        observed = command('windows', jid)
        assert observed['session_closed_reported'] and observed['workflow_completed_reported']
        assert observed['terminal_receipt'] and len(observed['receipts']) == len(actions)
        result['reported_fixture_outcome'] = {'session': observed['session']['lifecycle'], 'workflow': observed['workflow']['status'],
                                              'receipts': len(observed['receipts']), 'native_qualification': observed['native_qualification']}
        ledger = json.loads((root/'fixture-ledger.json').read_text())
        assert ledger['native_actions'] == 0 and len(ledger['calls']) == len(actions)
        assert sum(n == 'legacy_session_open' for n, _ in ledger['calls']) == 1
        assert sum(n == 'legacy_session_close' for n, _ in ledger['calls']) == 1
        export = command('audit-export', jid)
        verified = command('audit-verify', export['path'], '--fingerprint', export['signer_fingerprint'])
        assert verified['valid']
        result.update(export=export, verification=verified, passed=True)
    except BaseException as exc:
        result.update(passed=False, error=type(exc).__name__+': '+str(exc))
        raise
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            result['fixture_stopped'] = process.poll() is not None
            result['fixture_exit_code'] = process.returncode
        log.close()
        cache.rmdir()
        result['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (root/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps({'passed': result.get('passed', False), 'report': str(root/'result.json')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('monkey', 'python', 'output'): parser.add_argument('--'+name, required=True)
    parser.add_argument('--old-monkey')
    args = parser.parse_args()
    run(args.monkey, args.python, args.output, args.old_monkey)
