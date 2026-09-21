"""Actual asynchronous stream parsing, cancellation and signed evidence gates."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from monkey.adapters import HTTP, RequestError
from monkey.app import App
from monkey.common import Refused, object_schema
from monkey.ollama_stream import IncompleteStream
from monkey.config import recipe
from monkey.ui import rail


MODEL = 'jira-monkey-worker:latest'


def frame(content='', done=False, thinking='', **fields):
    return (json.dumps({'model': MODEL, 'done': done,
        'message': {'role': 'assistant', 'content': content, 'thinking': thinking}, **fields},
        ensure_ascii=False) + '\n').encode()


class Stream(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts, self.closed = parts, False

    async def __aiter__(self):
        for item in self.parts:
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                await item()
            else:
                yield item

    async def aclose(self):
        self.closed = True


class OllamaStreaming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.root = Path(self.temp.name)
        self.posts = []
        self.streams = []
        self.stream = Stream([])
        self.status_code = 200

        async def respond(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'models': [{'name': MODEL, 'digest': 'a' * 64,
                    'size': 1, 'details': {'format': 'gguf'}}]})
            self.posts.append(json.loads(request.content))
            stream = self.streams.pop(0) if self.streams else self.stream
            return httpx.Response(self.status_code, headers={'content-type': 'application/x-ndjson'}, stream=stream)

        self.http = HTTP(httpx.MockTransport(respond))
        self.app = App(self.root / 'state', http=self.http, offline=True)
        self.config = recipe({'ollama_model': MODEL, 'model': MODEL})
        self.shape = object_schema({'answer': {'type': 'string'}})

    async def asyncTearDown(self):
        await self.app.close()
        self.temp.cleanup()

    async def generate(self, config=None):
        return await self.app.models.local(config or self.config, MODEL, 'a' * 64,
            'Return the requested answer.', {}, self.shape, label='worker STREAM-1')

    def settled(self):
        return [e['data'] for e in self.app.audit.entries() if e['kind'] == 'model.stream_settled'][-1]

    async def test_fragmented_unicode_completes_once_with_exact_usage_and_hashes(self):
        text = json.dumps({'answer': 'banana 🍌'}, ensure_ascii=False)
        raw = frame(text[:12]) + frame(text[12:]) + frame(done=True, done_reason='stop',
            prompt_eval_count=81, eval_count=9)
        self.stream = Stream([raw[i:i+1] for i in range(len(raw))])
        result, usage = await self.generate()
        self.assertEqual(result, {'answer': 'banana 🍌'})
        self.assertEqual((usage['input_tokens'], usage['output_tokens'], usage['stream_frames']), (81, 9, 3))
        self.assertGreaterEqual(usage['first_output_ms'], 0)
        self.assertTrue(self.posts[0]['stream'])
        receipt = self.settled()
        self.assertTrue(receipt['complete_response_usable'])
        self.assertEqual(receipt['content_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(receipt['wire_prefix_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(self.stream.closed)
        self.assertFalse(self.http.activity)
        self.app.audit.verify()

    async def test_progress_is_committed_before_status_and_cancel_cannot_execute_partial_action(self):
        from test_general_agent import PLAN
        plan = json.dumps({'action': PLAN[0], 'arguments': PLAN[1], 'reason': 'Plan fixture', 'evidence_refs': []})
        action = json.dumps({'action': 'write_file', 'arguments': {'path': 'partial.txt',
            'content': 'PRIVATE-PROVISIONAL-FIXTURE'}, 'reason': 'Incomplete action', 'evidence_refs': []})
        ready, release = asyncio.Event(), asyncio.Event()

        async def held():
            ready.set()
            await release.wait()

        pending = Stream([frame(action), held, frame(done=True, done_reason='stop')])
        self.streams = [Stream([frame(plan, True, done_reason='stop')]), pending]
        workspace = self.root / 'workspace'
        workspace.mkdir()
        request = await self.app.dispatch('build', text='Write a fixture file', path=str(workspace))
        await asyncio.wait_for(ready.wait(), 3)
        try:
            status = await asyncio.wait_for(self.app.dispatch('status'), .2)
            progress = status['provider_activity']['127.0.0.1:11434']
            self.assertEqual(progress['state'], 'receiving provisional output')
            journal = self.app.audit.entries()
            entry = next(e for e in journal if e['seq'] == progress['evidence_seq'])
            self.assertEqual(entry['kind'], 'model.output_progress')
            self.assertEqual(entry['data']['content_bytes'], len(action.encode()))
            self.assertIn('provisional', rail(self.app, 160))
            self.assertFalse((workspace / 'partial.txt').exists())
            self.assertEqual(self.app.db.job(request['job_id'])['agent_tool_count'], 0)
            await self.app.dispatch('cancel', request['job_id'])
        finally:
            release.set()
            await self.app.execution.task
        self.assertFalse((workspace / 'partial.txt').exists())
        self.assertTrue(pending.closed)
        self.assertFalse(self.settled()['complete_response_usable'])
        self.assertEqual(self.settled()['outcome'], 'interrupted')
        self.assertNotIn('PRIVATE-PROVISIONAL-FIXTURE', json.dumps(self.app.audit.entries()))
        self.assertFalse(self.http.activity)
        self.app.audit.verify()

    async def test_disconnect_retains_partial_hash_and_no_unknown_usage_becomes_zero(self):
        raw = frame('{"answer":"part')
        self.stream = Stream([raw, httpx.ReadError('Synthetic lost response')])
        with self.assertRaises(RequestError):
            await self.generate()
        receipt = self.settled()
        self.assertEqual(receipt['wire_prefix_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertGreater(receipt['content_bytes'], 0)
        self.assertEqual(receipt['outcome'], 'interrupted')
        self.assertIsNone(receipt['usage']['eval_count'])
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(self.stream.closed)
        self.assertFalse(self.http.activity)

    async def test_missing_final_is_interrupted_without_model_fallback(self):
        config = {**self.config, 'model_routes': {'execution': [
            {'provider': 'ollama', 'model': MODEL, 'digest': 'a' * 64},
            {'provider': 'ollama', 'model': 'unused-fallback', 'digest': 'b' * 64}]}}
        self.stream = Stream([frame('{"answer":"plausibly complete"}')])
        with self.assertRaises(IncompleteStream):
            await self.generate(config)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.settled()['outcome'], 'interrupted')
        self.assertFalse(self.settled()['complete_response_usable'])
        self.assertIsNone(self.settled()['usage']['eval_count'])
        self.assertTrue(self.stream.closed)
        self.assertTrue(any(e['kind'] == 'model.stream_interrupted' for e in self.app.audit.entries()))

    async def test_general_worker_retries_eof_without_executing_provisional_action(self):
        def action(name, arguments):
            return json.dumps({'action': name, 'arguments': arguments, 'reason': 'EOF fixture', 'evidence_refs': []})
        from test_general_agent import PLAN
        partial = action('write_file', {'path': 'result.txt', 'content': 'must never be applied'})
        complete = action('write_file', {'path': 'result.txt', 'content': 'completed response only'})
        self.streams = [Stream([frame(action(*PLAN), True, done_reason='stop')]),
            Stream([frame(partial)]), Stream([frame(complete, True, done_reason='stop')]),
            Stream([frame(action('ask', {'question': 'Inspect the fixture'}), True, done_reason='stop')])]
        retained = list(self.streams)
        workspace = self.root / 'workspace'; workspace.mkdir()
        with patch('monkey.adapters.retry_delay', return_value=.001):
            request = await self.app.dispatch('build', text='Write the fixture', path=str(workspace))
            await self.app.execution.task
        job = self.app.db.job(request['job_id'])
        self.assertEqual((job['call_count'], job['retry_count'], job['agent_tool_count']), (4, 1, 1))
        self.assertEqual((workspace / 'result.txt').read_text(), 'completed response only')
        observations = self.app.agent.observations(job['id'])
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]['action'], 'write_file')
        retries = [r for r in self.app.agent.records(job['id']) if r['kind'] == 'agent.transport_retry']
        self.assertEqual(retries[0]['error_type'], 'IncompleteStream')
        self.assertTrue(all(s.closed for s in retained))
        self.app.audit.verify()

    async def test_repeated_eof_exhausts_original_job_budget_without_effects(self):
        self.stream = Stream([frame('{"action":"plan"')])
        workspace = self.root / 'workspace'; workspace.mkdir()
        with patch('monkey.adapters.retry_delay', return_value=.001):
            request = await self.app.dispatch('build', text='No partial effects', path=str(workspace))
            await self.app.execution.task
        job = self.app.db.job(request['job_id'])
        self.assertEqual((job['call_count'], job['retry_count'], job['agent_tool_count']), (3, 2, 0))
        self.assertEqual(job['agent_state'], 'NEEDS_INPUT')
        self.assertEqual(list(workspace.iterdir()), [])
        self.assertEqual(len(self.posts), 3)
        self.assertEqual(self.app.agent.unresolved(job['id']), [])

    async def test_length_invalid_json_and_late_tool_call_never_switch(self):
        candidates = [
            [frame('{"answer":"x"}', True, done_reason='length')],
            [b'{malformed}\n'],
            [frame('{"answer":"x"}'), frame(done=True, done_reason='stop',
                message={'tool_calls': [{'function': {'name': 'publish'}}]})],
            [frame('{"answer":"x"}', True, done_reason='stop'), frame('trailing')],
            [frame('{"answer":"x","approved":true}', True, done_reason='stop')],
            [frame('{"answer":"x","answer":"y"}', True, done_reason='stop')],
            [frame('{"answer":"x"}'), frame(done=True, done_reason='stop', model='different-model')],
        ]
        config = {**self.config, 'model_routes': {'execution': [
            {'provider': 'ollama', 'model': MODEL, 'digest': 'a' * 64},
            {'provider': 'ollama', 'model': 'unused-fallback', 'digest': 'b' * 64}]}}
        for parts in candidates:
            with self.subTest(parts=parts):
                self.posts.clear()
                self.stream = Stream(parts)
                with self.assertRaises(Refused):
                    await self.generate(config)
                self.assertEqual(len(self.posts), 1)
                self.assertEqual(self.settled()['outcome'], 'rejected')
                self.assertTrue(self.stream.closed)

    async def test_flood_and_oversized_content_close_with_bounded_receipts(self):
        for bounds, parts in (
            ({'MAX_WIRE_BYTES': 100}, [b'x' * 101]),
            ({'MAX_FRAME_BYTES': 100}, [b'x' * 101]),
            ({'MAX_CONTENT_CHARS': 3}, [frame('four')]),
            ({'MAX_FRAMES': 1}, [frame('a'), frame('b')]),
        ):
            with self.subTest(bounds=bounds):
                self.stream = Stream(parts)
                with patch.multiple('monkey.ollama_stream', **bounds), self.assertRaises(Refused):
                    await self.generate()
                self.assertFalse(self.settled()['complete_response_usable'])
                self.assertTrue(self.stream.closed)
                self.assertFalse(self.http.activity)

    async def test_absolute_timeout_survives_regular_output(self):
        async def small_wait():
            await asyncio.sleep(.01)
        self.stream = Stream([item for _ in range(80) for item in (frame('x'), small_wait)])
        # HTTP's total request deadline remains independent of socket activity.
        with self.assertRaises(RequestError):
            await self.generate({**self.config, 'request_timeout': .05})
        self.assertGreater(self.settled()['frames'], 0)
        self.assertLess(self.settled()['elapsed_ms'], 500)
        self.assertFalse(self.settled()['done_received'])
        self.assertTrue(self.stream.closed)

    async def test_thinking_is_counted_without_retaining_or_displaying_it(self):
        thought = 'Private intermediate text fixture'
        self.stream = Stream([frame(thinking=thought), frame('{"answer":"done"}', True, done_reason='stop')])
        result, _ = await self.generate()
        self.assertEqual(result, {'answer': 'done'})
        self.assertEqual(self.settled()['thinking_bytes'], len(thought.encode()))
        self.assertNotIn(thought, json.dumps(self.app.audit.entries()))

    async def test_pre_response_failure_has_a_settled_zero_byte_trace_without_replay(self):
        self.status_code = 503
        self.stream = Stream([b'Service error text is withheld'])
        with self.assertRaises(RequestError):
            await self.generate()
        self.assertEqual(self.settled()['wire_bytes'], 0)
        self.assertIsNone(self.settled()['first_output_ms'])
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(self.stream.closed)
        self.assertFalse(self.http.activity)

    async def test_real_loopback_socket_closes_before_returning_completed_output(self):
        async def serve(reader, writer):
            try:
                head = await reader.readuntil(b'\r\n\r\n')
                size = int(next(line.split(b':', 1)[1] for line in head.split(b'\r\n')
                    if line.lower().startswith(b'content-length:')))
                await reader.readexactly(size)
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nConnection: close\r\n\r\n')
                writer.write(frame('{"answer":"socket'))
                await writer.drain()
                await asyncio.sleep(.01)
                writer.write(frame('"}', True, done_reason='stop'))
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        from monkey.ollama_stream import ChatStream
        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        http = HTTP()
        try:
            parser = ChatStream(self.app.audit, MODEL)
            port = server.sockets[0].getsockname()[1]
            response, status = await http.request(f'http://127.0.0.1:{port}/api/chat',
                {'model': MODEL, 'stream': True}, reader=parser, timeout=2)
            self.assertEqual(status, 200)
            self.assertTrue(parser.eof)
            self.assertEqual(json.loads(response['message']['content']), {'answer': 'socket'})
            parser.settle('validated')
        finally:
            await http.close()
            server.close()
            await server.wait_closed()
