"""Diagnostic observation must forward the real stream without completing it."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest

import httpx

ATTACH = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/capture_model_fixture.py'))['attach']


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    async def __aiter__(self):
        for part in self.chunks:
            if isinstance(part, Exception): raise part
            yield part

    async def aclose(self):
        self.closed = True


class FixtureCapture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'wire'
        self.stream = Chunks([b'first', b'second'])
        async def respond(request): return httpx.Response(200, stream=self.stream)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        ATTACH(self.client, self.root)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_exact_bytes_and_unique_sequential_request_records(self):
        for number in range(1, 4):
            response = await self.client.post('http://127.0.0.1:11434/api/chat', json={'fixture': number})
            self.assertEqual(response.content, b'firstsecond')
            prefix = self.root / ('request-%03d' % number)
            self.assertEqual(json.loads(prefix.with_suffix('.json').read_text()), {'fixture': number})
            self.assertEqual(prefix.with_suffix('.ndjson').read_bytes(), response.content)
        self.assertTrue(self.stream.closed)

    async def test_observer_limit_never_truncates_provider_stream(self):
        payload = b'x' * (600 * 1024)
        self.stream = Chunks([payload])
        response = await self.client.post('http://127.0.0.1:11434/api/chat', json={})
        self.assertEqual(response.content, payload)
        receipt = json.loads((self.root / 'request-001.response-receipt.json').read_text())
        self.assertEqual(receipt['retained_bytes'], 512 * 1024)
        self.assertTrue(receipt['truncated'])
        self.assertEqual(receipt['observed_prefix_sha256'], hashlib.sha256(payload).hexdigest())

    async def test_transport_failure_stays_incomplete(self):
        self.stream = Chunks([b'partial', httpx.ReadError('Fixture disconnect')])
        with self.assertRaises(httpx.ReadError):
            await self.client.post('http://127.0.0.1:11434/api/chat', json={})
        self.assertTrue(self.stream.closed)
        receipt = json.loads((self.root / 'request-001.response-receipt.json').read_text())
        self.assertFalse(receipt['eof'])
        self.assertEqual((self.root / 'request-001.ndjson').read_bytes(), b'partial')
