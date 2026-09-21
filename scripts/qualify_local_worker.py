"""Installed local ticket worker: live models, checkpoint control, signed evidence.

Run with the installed Python and -I -B. Keeps a private fixture and every
record; never approves a draft or contacts a business service.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

import monkey
from monkey.app import App
from monkey.audit import verify_file
from monkey.platform_files import private_directory, write_private


async def run(args):
    root = Path(args.output).expanduser().absolute()
    if root.exists():
        raise RuntimeError('Use a new output directory; retained work is not overwritten')
    if not Path(monkey.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError('Use the installed Monkey Python interpreter')
    private_directory(root)
    app = App(root / 'state')
    samples = []
    report = {'schema':'monkey.installed-local-worker-qualification.v1',
              'application':monkey.__file__, 'python':sys.executable,
              'physical_terminal_ui_tested':False, 'business_writes':0,
              'synthetic_input':True, 'live_local_adapters':True,
              'approved':False, 'validation_kind':'One live local ticket fixture'}
    started = time.monotonic()
    try:
        await app.capture_config()
        jid = app.db.add({'source':'local', 'instance':'local://monkey', 'key':'QUAL-WORKER-101',
                          'revision':'private-fixture-1', 'title':'Request login reproduction details',
                          'body':'A user reports that login sometimes fails. Draft a short, useful '
                          'support reply asking for reproduction steps, error text and approximate '
                          'time. No source code, fix, logs or test results are available.'})['id']
        report['job_id'] = jid
        app.focus = jid
        await app.dispatch('run', target=jid)
        print(json.dumps({'job_id':jid, 'application':monkey.__file__}), flush=True)
        seq = 0
        paused_requested = False
        async with asyncio.timeout(480):
            while app.worker.task and not app.worker.task.done():
                tick = time.perf_counter()
                await app.dispatch('status')
                samples.append((time.perf_counter() - tick) * 1000)
                job = app.db.job(jid)
                if job['stage'] == 'DRAFT' and not paused_requested:
                    report['pause_receipt'] = await app.dispatch('pause', target=jid, after='review')
                    paused_requested = True
                for event in app.db.events(seq, job_id=jid):
                    seq = event['seq']
                    if event['data'].get('message'):
                        print(event['kind'] + ': ' + event['data']['message'][:200], flush=True)
                await asyncio.sleep(.25)
            await app.worker.task
        job = app.db.job(jid)
        report['job'] = app.summary(job)
        report['records'] = await app.dispatch('show', target=jid)
        report['passed'] = job['work_state'] == 'PAUSED' and bool(job['review_id']) and bool(job['draft_id'])
        report['provider_calls'] = app.db.records('provider_calls')
        exported = await app.dispatch('audit-export', target=jid)
        report['export'] = exported
        report['verification'] = await asyncio.to_thread(verify_file, exported['path'], exported['signer_fingerprint'])
    except BaseException as error:
        report.update(passed=False, error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        await app.close()
        report['elapsed_seconds'] = time.monotonic() - started
        report['status_samples'] = len(samples)
        report['status_p95_ms'] = sorted(samples)[int(len(samples) * .95)] if samples else None
        write_private(root / 'result.json', (json.dumps(report, indent=2) + '\n').encode())
        print(json.dumps({'passed':report.get('passed',False), 'report':str(root / 'result.json'),
                          'elapsed_seconds':report['elapsed_seconds']}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
