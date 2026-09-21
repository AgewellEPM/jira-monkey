"""Fresh-process checks: exact control must not depend on UI or service startup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
OPTIONAL = ['mcp', 'httpx2', 'jsonschema', 'referencing', 'uvicorn', 'starlette', 'prompt_toolkit']


class CLIStartup(unittest.TestCase):
    def child(self, body, *, blocked=OPTIONAL):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            code = '''
import importlib.abc, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
root = pathlib.Path(sys.argv[2])
blocked = json.loads(sys.argv[3])
class Unavailable(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('Unneeded dependency loaded: ' + fullname)
sys.meta_path.insert(0, Unavailable())
def no_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('Exact command attempted network activity')
sys.addaudithook(no_network)
''' + body
            env = {k: v for k, v in os.environ.items() if k.upper() in {
                'PATH', 'HOME', 'USER', 'LOGNAME', 'USERNAME', 'LANG', 'SYSTEMROOT',
                'WINDIR', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'TMPDIR', 'TEMP', 'TMP'}}
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT), directory,
                                     json.dumps(blocked)], capture_output=True, text=True,
                                    env=env, timeout=30, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_help_and_version_without_application_or_service_dependencies(self):
        output = self.child('''
from monkey.cli import main
for operation in ('--version', '--help', 'windows-plan --help'):
    try:
        main(['--state', str(root/'unused-state'), *operation.split()])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError('Expected argparse exit')
assert not (root/'unused-state').exists()
''', blocked=[*OPTIONAL, 'monkey.app', 'httpx', 'cryptography'])
        self.assertIn('usage:', output)
        self.assertIn('windows-plan', output)

    def test_capabilities_and_recorded_control_without_sdk_or_ui(self):
        output = self.child('''
import asyncio
from monkey.cli import main
from monkey.demo import demo_app, TICKET
state = root/'state'
async def prepare():
    app, _ = demo_app(state)
    try:
        result = await app.dispatch('fetch', key=TICKET['key'])
        return result['job_id']
    finally:
        await app.close()
jid = asyncio.run(prepare())
for command in (['caps'], ['status'], ['pause', jid], ['status'], ['cancel', jid], ['status']):
    assert main(['--state', str(state), *command]) == 0
''')
        self.assertIn('PAUSED', output)
        self.assertIn('CANCELLED', output)

    def test_signed_history_and_public_verification_without_ui_or_sdk(self):
        output = self.child('''
import asyncio
from monkey.cli import main
from monkey.demo import demo_app
state = root/'state'
async def prepare():
    app, _ = demo_app(state, delay=.001)
    try:
        jid = (await app.dispatch('request', text='Draft a support response asking for reproduction steps'))['job_id']
        async with asyncio.timeout(5):
            while app.db.job(jid)['work_state'] != 'DRAFT_READY':
                await asyncio.sleep(.01)
        await app.dispatch('approve', target=jid, note='Inspected private fixture candidate')
        assert app.status()['needs_you'] == 0
        return await app.dispatch('audit-export', target=jid)
    finally:
        await app.close()
export = asyncio.run(prepare())
assert main(['--state', str(state), 'status']) == 0
assert main(['--state', str(root/'unused-state'), 'audit-verify', export['path'],
             '--fingerprint', export['signer_fingerprint']]) == 0
assert not (root/'unused-state').exists()
''')
        self.assertIn('"valid": true', output.lower())

    def test_gateway_sdk_loading_keeps_recorded_status_responsive(self):
        # An import hook models a slow first SDK load in a genuinely fresh
        # interpreter. No provider, account or model is contacted.
        self.child('''
import asyncio, threading, time
from monkey.demo import demo_app
started, release = threading.Event(), threading.Event()
class SlowSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'mcp':
            started.set()
            if not release.wait(5):
                raise RuntimeError('SDK import held the event loop')
sys.meta_path.insert(0, SlowSDK())
async def check():
    app, _ = demo_app(root/'state')
    opening = asyncio.create_task(app.gateway.start())
    try:
        async with asyncio.timeout(3):
            while not started.is_set():
                await asyncio.sleep(.005)
        assert not opening.done()
        status = await asyncio.wait_for(app.dispatch('status'), timeout=.5)
        assert status['jobs'] == []
        release.set()
        async with asyncio.timeout(10):
            result = await opening
        assert result['running'] is True
    finally:
        release.set()
        if not opening.done():
            opening.cancel()
        await asyncio.gather(opening, return_exceptions=True)
        await app.close()
        assert app.gateway.status()['running'] is False
asyncio.run(check())
''', blocked=['prompt_toolkit'])


if __name__ == '__main__':
    unittest.main()
