#!/usr/bin/env python3
"""Check the actual installed CLI + dashboard in a fresh, private state directory."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import time
import uuid

import httpx


async def check(args):
    if args.state.exists(): raise ValueError('Use a fresh qualification state directory')
    output = []
    process = await asyncio.create_subprocess_exec(str(args.command), '--state', str(args.state), '--json', 'dashboard',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1024 * 1024)
    stderr_task = asyncio.create_task(process.stderr.read(262144))
    report = {'command': str(args.command), 'state': str(args.state), 'pid': process.pid, 'provider_generation': False}

    async def read_until(predicate):
        async with asyncio.timeout(15):
            while True:
                raw = await process.stdout.readline()
                if not raw: raise RuntimeError('Installed CLI ended before the expected response')
                row = json.loads(raw)
                output.append(row)
                if predicate(row): return row

    try:
        ready = await read_until(lambda row: row.get('type') == 'dashboard')
        private = ready['value']['dashboard_url']
        url, key = private.split('#key=')
        report['url'] = url
        async with httpx.AsyncClient(base_url=url, headers={'Authorization': 'Bearer ' + key, 'Origin': url.rstrip('/')}, trust_env=False) as http:
            page = await http.get('/')
            assert page.status_code == 200 and 'Ask Monkey' in page.text
            data = {'id': str(uuid.uuid4()), 'kind': 'message', 'job_id': None, 'expected_version': None,
                    'text': '/task Installed dashboard qualification request'}
            response = await http.post('/api/requests', json=data)
            assert response.status_code == 202, response.text
            async with asyncio.timeout(8):
                while True:
                    snapshot = (await http.get('/api/state')).json()
                    if snapshot['messages'] and snapshot['messages'][-1]['status'] != 'ACCEPTED': break
                    await asyncio.sleep(.03)
            assert snapshot['messages'][-1]['status'] == 'REPLIED', snapshot['messages'][-1]
            assert len(snapshot['jobs']) == 1
            jid = snapshot['jobs'][0]['job_id']
            process.stdin.write(b'/status\n'); await process.stdin.drain()
            terminal = await read_until(lambda row: row.get('type') == 'reply' and isinstance(row.get('value'), dict) and
                row['value'].get('jobs') and row['value']['jobs'][0].get('job_id') == jid)
            assert terminal['value']['jobs'][0]['title'] == snapshot['jobs'][0]['title']
            replay = await http.post('/api/requests', json=data)
            assert replay.status_code == 202 and len((await http.get('/api/state')).json()['jobs']) == 1
            samples = []
            for _ in range(10):
                start = time.perf_counter(); status = await http.get('/api/state'); samples.append((time.perf_counter() - start) * 1000)
                assert status.status_code == 200
            report.update({'job_id': jid, 'shared_cli_web_state': True, 'duplicate_request_replayed_once': True,
                'status_samples_ms': samples, 'status_p95_ms': sorted(samples)[-1], 'version': snapshot['version']})
        process.stdin.write(b'/quit\n'); await process.stdin.drain()
        tail = await asyncio.wait_for(process.stdout.read(262144), 10)
        await asyncio.wait_for(process.wait(), 10)
        assert process.returncode == 0, process.returncode
        output.extend(json.loads(line) for line in tail.splitlines() if line)
        parsed = httpx.URL(url)
        try:
            reader, writer = await asyncio.open_connection(parsed.host, parsed.port)
        except OSError:
            report['listener_closed'] = True
        else:
            writer.close(); await writer.wait_closed()
            raise AssertionError('Dashboard listener remained open after /quit')
        report['passed'] = True
    finally:
        if process.returncode is None:
            process.terminate()
            try: await asyncio.wait_for(process.wait(), 10)
            except TimeoutError: process.kill(); await process.wait()
        error = await stderr_task
        report['exit_code'] = process.returncode
        # The short-lived browser key has expired before this private receipt is
        # written. Retain output for verification, but redact the link fragment.
        for row in output:
            if row.get('type') == 'dashboard':
                row['value']['dashboard_url'] = row['value']['dashboard_url'].split('#')[0] + '#[expired key omitted]'
        args.report.write_text(json.dumps({**report, 'terminal_output': output, 'stderr': error.decode(errors='replace')}, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    asyncio.run(check(args))
