"""Measure a real installed CLI without providers, business calls or UI automation.

Each sample is a new process using private test state and an empty bytecode cache.
This measures process startup plus the complete command, not physical UI paint.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import time

from qualify_cli import checked_process, qualification_environment


def run(monkey, output, samples=5):
    if not 1 <= samples <= 30:
        raise ValueError('Choose between 1 and 30 samples')
    os.umask(0o077)
    root = Path(output).expanduser().absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    console = Path(monkey).expanduser().resolve(strict=True)
    env = qualification_environment()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONPYCACHEPREFIX'] = str(root / 'empty-bytecode-cache')
    report = {
        'schema': 'monkey.startup-benchmark.v1',
        'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'platform': platform.platform(),
        'machine': platform.machine(),
        'console': str(console),
        'console_sha256': hashlib.sha256(console.read_bytes()).hexdigest(),
        'samples_per_command': samples,
        'boundary': 'New process through complete command; OS file caches are not flushed',
        'python_bytecode_cache': 'Empty prefix; writes disabled',
        'physical_terminal_ui_tested': False,
        'model_generation_tested': False,
        'business_writes': 0,
        'commands': [],
    }
    path = root / 'result.json'
    try:
        for operation in ('--version', '--help', 'caps', 'status'):
            elapsed = []
            row = {'operation': operation, 'samples': []}
            report['commands'].append(row)
            for sample in range(samples):
                state = root / ('state-' + operation.lstrip('-') + '-' + str(sample))
                argv = [str(console), '--state', str(state), operation]
                started = time.perf_counter()
                result = checked_process(argv, cwd=root, env=env, timeout=90)
                milliseconds = (time.perf_counter() - started) * 1000
                row['samples'].append({'argv': argv, 'elapsed_ms': milliseconds, **result})
                path.write_text(json.dumps(report, indent=2) + '\n')
                if result['exit_code'] != 0:
                    raise RuntimeError('Startup command failed: ' + operation + '; inspect ' + str(path))
                if operation == '--version':
                    version = result['stdout'].strip()
                    if report.setdefault('version', version) != version:
                        raise RuntimeError('Installed version changed during measurement')
                elif operation in {'caps', 'status'}:
                    value = json.loads(result['stdout'])
                    if not isinstance(value, dict) or 'error' in value:
                        raise RuntimeError('Command returned an invalid result')
                    if operation == 'status' and value.get('jobs') != []:
                        raise RuntimeError('Benchmark must use empty private state')
                elif 'usage:' not in result['stdout']:
                    raise RuntimeError('Expected actual command help')
                elapsed.append(milliseconds)
            row['median_ms'] = statistics.median(elapsed)
            row['max_ms'] = max(elapsed)
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        report['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        path.write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monkey', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--samples', type=int, default=5)
    args = parser.parse_args()
    result = run(args.monkey, args.output, args.samples)
    print(json.dumps({'version': result['version'], 'status': result['status'],
                     'commands': [{k: row[k] for k in ('operation', 'median_ms', 'max_ms')}
                                  for row in result['commands']]}, indent=2))
