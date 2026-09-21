"""Actual macOS checker startup, confinement, timeout evidence and cleanup."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from monkey.project_tools import run_test
from monkey.verification import successful_command


@unittest.skipUnless(sys.platform=='darwin','Native macOS verifier qualification')
class NativeVerifier(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='monkey-native-verifier-',dir='/private/tmp')
        self.base=Path(self.temp.name)
        self.root=self.base/'project';self.root.mkdir()
        self.executable=str(Path(sys.executable).resolve())

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_runtime_starts_in_utc_and_retains_all_four_boundaries(self):
        secret=self.base/'outside.txt';secret.write_text('private fixture outside selected project')
        source='''import os, socket, time
assert time.tzname == ('UTC', 'UTC'), time.tzname
def denied(operation):
    try: operation()
    except PermissionError: return
    raise AssertionError('Confinement did not reject operation')
denied(lambda: open(OUTSIDE).read())
denied(lambda: open('unexpected-write.txt', 'w'))
with socket.socket() as connection:
    denied(lambda: connection.connect(('127.0.0.1', 1)))
denied(os.fork)
print('UTC startup and four confinement checks passed', flush=True)
'''.replace('OUTSIDE',repr(str(secret)))
        (self.root/'check.py').write_text(source)
        result=await run_test(self.root,[self.executable,'check.py'],self.base/'scratch',timeout=5)
        self.assertTrue(successful_command(result),json.dumps(result))
        self.assertIn('four confinement checks passed',result['output'])
        self.assertFalse((self.root/'unexpected-write.txt').exists())

    async def test_timeout_retains_partial_output_and_stops_process(self):
        (self.root/'slow.py').write_text('import time\nprint("partial checker evidence",flush=True)\ntime.sleep(30)\n')
        result=await run_test(self.root,[self.executable,'slow.py'],self.base/'scratch',timeout=1)
        self.assertTrue(result.get('timed_out'),json.dumps(result))
        self.assertIn('partial checker evidence',result['output'])
        self.assertEqual(result['output_sha256'],hashlib.sha256(result['output'].encode()).hexdigest())
        self.assertFalse(successful_command(result))
        self.assertFalse(successful_command({**result,'exit_code':0}))
        with self.assertRaises(ProcessLookupError): os.kill(result['pid'],0)
