#!/usr/bin/env python3
"""Retained offline dashboard demonstration; all work is explicitly fixture data."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys


async def main(args):
    from monkey.demo import TICKET, demo_app
    from monkey.cli import line
    if args.state.exists(): raise ValueError('Choose a new, private fixture state directory')
    app, jira = demo_app(args.state, 'pass', delay=.02)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stop.set)
    try:
        for text in ('Draft a sprint ticket for a smoother account setup',
                     'Write a support reply asking for reproduction steps',
                     'Draft a bug ticket for the checkout error'):
            result = await line(app, '/request ' + text)
            jid = result['job_id']
            async with asyncio.timeout(10):
                while app.db.job(jid)['work_state'] in {'QUEUED', 'RUNNING'}: await asyncio.sleep(.02)
        ready = app.db.jobs()
        await app.dispatch('approve', ready[0]['id'], note='Reviewed scripted local candidate for dashboard qualification')
        await app.dispatch('pause', ready[1]['id'])
        await app.dispatch('task', text='Plan the next sprint with the product team')
        await app.dispatch('task', text='Prepare a customer onboarding checklist')
        await app.dispatch('schedule', ready[2]['id'], start='2026-09-15T09:00', finish='2026-09-18T17:00', timezone='America/New_York', note='Explicit fixture sprint dates')
        app.focus = None
        started = await app.dispatch('dashboard', action='start')
        args.ready.write_text(json.dumps({**started, 'pid': os.getpid(), 'state': str(args.state),
            'payload': str(Path(sys.modules['monkey'].__file__).parent), 'fixture': True}, indent=2) + '\n')
        args.ready.chmod(0o600)
        print('Offline dashboard ready; private connection details retained in ' + str(args.ready), flush=True)
        await stop.wait()
    finally:
        await app.close()
        print('Foreground demo closed; dashboard listener and commands stopped.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--ready', type=Path, required=True)
    parser.add_argument('--payload', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    sys.path.insert(0, str(args.payload))
    os.umask(0o077)
    asyncio.run(main(args))
