"""Scripted MCP contracts through the real Monkey admission and signed store.

No VM, native UI, owner key or production provider is created by these tests.
"""
import asyncio
from contextlib import asynccontextmanager
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monkey.app import App
from monkey.audit import verify_file
from monkey.cli import line
from monkey.common import Refused, digest, now
from monkey.gateway import ORIGIN
from monkey.windows import ACTIONS, LEGACY


SESSION = '8b1e9b03-a51a-4a90-9b40-6f58fc7895b7'
RUN = 'a' * 32


class Result:
    def __init__(self, value): self.value = value
    def model_dump(self, **kwargs): return copy.deepcopy(self.value)


class ProtocolFixture:
    def __init__(self):
        self.calls = []
        self.target = 'legacy.win10'
        self.closed = False
        self.allowed = True
        self.override = None
        self.lose = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = None
        self.workflow_stage = 'ready'

    def session(self):
        return {'schema': 'ghostbridge.owner-session-result.v1', 'ok': True,
            'session': {'schema': 'ghostbridge.owner-session-state.v1', 'sessionId': SESSION,
                        'targetId': self.target, 'resourceClass': 'vm', 'mode': 'interactive',
                        'lifecycle': 'COLD' if self.closed else 'HOT', 'slotHeld': not self.closed,
                        'stateDigest': digest([self.closed, len(self.calls)])}}

    def workflow(self):
        return {'schema': 'ghostbridge.vcard-run-state.v1', 'ok': True, 'runId': RUN,
                'workflowDigest': 'b' * 64, 'dryRunDigest': 'c' * 64, 'mode': 'interactive',
                'session': {'sessionId': SESSION, 'systemId': 'windows10', 'profileId': 'windows10-x64-csharp-4.x'},
                'status': self.workflow_stage, 'stateDigest': digest([self.workflow_stage, len(self.calls)]),
                'terminalSuccess': self.workflow_stage == 'completed'}

    async def list_tools(self, **kwargs):
        return Result({'tools': [{'name': name, 'description': 'Scripted contract only',
                                 'inputSchema': {'type': 'object'}} for name in ACTIONS.values()]})

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == self.hold:
            self.started.set()
            await self.release.wait()
        if name == self.lose:
            raise TimeoutError('Fixture dropped the acknowledgement')
        if self.override:
            return Result({'content': [{'type': 'text', 'text': json.dumps(self.override)}]})
        if name == 'legacy_session_catalog':
            value = {'ok': True, 'schema': 'ghostbridge.owner-session-catalog-view.v1', 'catalogDigest': 'd' * 64,
                     'targets': [{'targetId': t, 'resourceClass': 'vm', 'allowedModes': ['interactive']}
                                 for t in ('legacy.win10', 'legacy.win11')]}
        elif name in {'legacy_session_open', 'legacy_session_status', 'legacy_session_close'}:
            if name == 'legacy_session_open': self.target = arguments['targetId']
            if name == 'legacy_session_close': self.closed = True
            value = self.session()
        elif name == 'legacy_accessibility_tree':
            value = {'schema': 'ghostbridge.legacy-accessibility-tree.v1', 'platform': self.target.split('.')[1],
                     'capturedAt': now(), 'root': {'title': 'Synthetic observation; no native desktop'}}
        elif name == 'vcard_status':
            value = {'ok': True, 'schema': 'ghostbridge.vcard-mcp-status.v1', 'allowExecution': self.allowed,
                     'mutatingExecutionReady': self.allowed, 'mutatingExecutionReadinessBlockers': [] if self.allowed else ['fixture disabled']}
        elif name == 'vcard_run_receipt':
            value = {'ok': True, 'receipt': {'runId': RUN, 'workflowDigest': 'b' * 64, 'cleanup': {'fixture': True}}}
        elif name in {'vcard_provider_tick', 'vcard_cleanup_tick'}:
            value = {'ok': True, 'fixture_report': True}
        else:
            value = self.workflow()
        return Result({'content': [{'type': 'text', 'text': json.dumps(value)}]})


class WindowsCoordinator(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='monkey-windows-', dir=Path(tempfile.gettempdir()).resolve())
        self.root = Path(self.temp.name)
        self.fixture = ProtocolFixture()
        self.app = App(self.root / 'state')
        self.install_fixture()
        for name in ('owner', 'vcards'):
            await self.app.connectors.connect_config(name, {'transport': 'streamable-http', 'url': 'http://127.0.0.1:12345/mcp'})
        self.jid = self.new_job('WINDOWS-1')

    def install_fixture(self):
        fixture = self.fixture
        @asynccontextmanager
        async def client(config):
            yield fixture
        self.app.connectors._client = client

    def new_job(self, key):
        return self.app.db.add({'source': 'local', 'instance': 'local://monkey', 'key': key, 'revision': 'fixture',
            'title': 'Windows protocol fixture', 'body': 'No native execution claimed.'}, fixture=True)['id']

    async def bind(self, target='legacy.win10', jid=None):
        return await line(self.app, '/windows-bind '+(jid or self.jid)+' --server owner --workflow-server vcards --target-id '+target+
                          ' --profile-id windows10-x64-csharp-4.x --note "Synthetic contract test"')

    async def run_action(self, action, supplied=None, jid=None):
        jid = jid or self.jid
        path = self.root / 'arguments.json'
        path.write_text(json.dumps(supplied or {}))
        proposed = await line(self.app, '/windows-plan '+jid+' '+action+' --args-file '+str(path))
        plan = proposed['tool_plan']
        await self.app.dispatch('tool-run', jid, exact_hash=plan['plan_hash'], note='Reviewed this exact synthetic request')
        await self.app.execution.task
        return self.app.db.job(jid), plan

    async def open(self):
        await self.bind()
        await self.run_action('catalog')
        job, _ = await self.run_action('open')
        self.assertEqual(job['tool_delivery'], 'RETURNED_UNVERIFIED', job['reason'])

    async def start_workflow(self):
        await self.open()
        await self.run_action('readiness')
        return await self.run_action('workflow-start', {'workflowDigest': 'b' * 64, 'dryRunDigest': 'c' * 64,
                              'inputEnvelopePath': '/fixture/sealed.json', 'inputExpectedSHA256': 'e' * 64})

    async def asyncTearDown(self):
        self.fixture.release.set()
        await self.app.close()
        self.temp.cleanup()

    async def test_exact_lifecycle_and_signed_recovery_history(self):
        await self.open()
        await self.run_action('observe')
        await self.run_action('status')
        viewed = await line(self.app, '/windows '+self.jid)
        self.assertEqual(viewed['session']['targetId'], 'legacy.win10')
        self.assertFalse(viewed['session_closed_reported'])
        self.assertFalse(viewed['workflow_completed_reported'])
        before = self.fixture.calls[-1][1]['sessionId']
        job, _ = await self.run_action('close')
        self.assertEqual(self.fixture.calls[-1][1]['sessionId'], before)
        self.assertEqual(job['tool_delivery'], 'RETURNED_UNVERIFIED', job['reason'])
        count = len(self.fixture.calls)
        await self.app.close()
        self.app = App(self.root / 'state'); self.install_fixture()
        viewed = await self.app.dispatch('windows', self.jid)
        self.assertTrue(viewed['session_closed_reported'])
        self.assertFalse(viewed['workflow_completed_reported'])
        self.assertEqual(len(self.fixture.calls), count)
        self.assertEqual(len(viewed['receipts']), 5)
        exported = self.app.audit.export(self.jid)
        self.assertTrue(verify_file(exported['path'], self.app.audit.fingerprint)['valid'])

    async def test_two_queued_targets_do_not_deadlock_but_only_one_can_open(self):
        await self.bind()
        second = self.new_job('WINDOWS-2')
        await self.bind('legacy.win11', second)
        await self.run_action('catalog')
        await self.run_action('catalog', jid=second)
        await self.run_action('open')
        with self.assertRaisesRegex(Refused, 'not proven'):
            await self.run_action('open', jid=second)
        self.assertEqual(sum(name == 'legacy_session_open' for name, _ in self.fixture.calls), 1)

    async def test_wrong_target_and_unsupported_generation_are_not_guessed(self):
        with self.assertRaises(Refused): await self.bind('legacy.win1O')
        await self.open()
        bad = self.fixture.session(); bad['session']['targetId'] = 'legacy.win11'
        self.fixture.override = bad
        job, _ = await self.run_action('status')
        self.assertEqual(job['tool_delivery'], 'UNKNOWN')
        viewed = self.app.windows.view(job)
        self.assertEqual(viewed['session']['targetId'], 'legacy.win10')
        self.assertIn('different target', viewed['last_error'])
        self.assertIsNone(self.app.windows.state(job)['session_at'])

    async def test_dropped_open_response_is_never_reissued_on_restart(self):
        await self.bind(); await self.run_action('catalog')
        self.fixture.lose = 'legacy_session_open'
        job, plan = await self.run_action('open')
        self.assertEqual(job['tool_delivery'], 'UNKNOWN')
        count = len(self.fixture.calls)
        await self.app.close(); self.app = App(self.root / 'state'); self.install_fixture()
        await self.app.dispatch('tool-run', self.jid, exact_hash=plan['plan_hash'], note='Attempt fixture replay')
        await self.app.execution.task
        with self.assertRaises(Refused): await self.run_action('open')
        self.assertEqual(len(self.fixture.calls), count)

    async def test_disabled_provider_blocks_workflow_but_allows_cleanup(self):
        await self.open()
        self.fixture.allowed = False
        await self.run_action('readiness')
        with self.assertRaisesRegex(Refused, 'enrollment'):
            await self.run_action('workflow-start', {'workflowDigest': 'b'*64, 'dryRunDigest': 'c'*64,
                                  'inputEnvelopePath': '/fixture/input.json', 'inputExpectedSHA256': 'e'*64})
        job, _ = await self.run_action('close')
        self.assertTrue(self.app.windows.view(job)['session_closed_reported'])

    async def test_workflow_completion_keeps_cleanup_and_session_separate(self):
        job, _ = await self.start_workflow()
        self.assertEqual(job['tool_delivery'], 'RETURNED_UNVERIFIED', job['reason'])
        self.fixture.workflow_stage = 'completed'
        job, _ = await self.run_action('workflow-step')
        viewed = self.app.windows.view(job)
        self.assertTrue(viewed['workflow_completed_reported'])
        self.assertFalse(viewed['session_closed_reported'])
        self.assertIsNone(viewed['terminal_receipt'])
        job, _ = await self.run_action('workflow-receipt')
        self.assertIsNotNone(self.app.windows.view(job)['terminal_receipt'])
        self.assertFalse(self.app.windows.view(job)['session_closed_reported'])

    async def test_workflow_profile_and_session_must_match_owner(self):
        await self.open(); await self.run_action('readiness')
        bad = self.fixture.workflow(); bad['session']['sessionId'] = 'some-other-session'
        self.fixture.override = bad
        job, _ = await self.run_action('workflow-start', {'workflowDigest': 'b'*64, 'dryRunDigest': 'c'*64,
                              'inputEnvelopePath': '/fixture/input.json', 'inputExpectedSHA256': 'e'*64})
        self.assertEqual(job['tool_delivery'], 'UNKNOWN')
        self.assertIsNone(self.app.windows.view(job)['workflow'])

    async def test_status_is_responsive_while_open_is_waiting(self):
        await self.bind(); await self.run_action('catalog')
        self.fixture.hold = 'legacy_session_open'
        task = asyncio.create_task(self.run_action('open'))
        try:
            await asyncio.wait_for(self.fixture.started.wait(), 2)
            status = await asyncio.wait_for(self.app.dispatch('windows', self.jid), .2)
            self.assertEqual(status['delivery'], 'CALLING')
            self.assertIsNone(status['session'])
        finally:
            self.fixture.release.set(); await task

    async def test_generic_calls_and_client_approval_cannot_escape_binding(self):
        await self.open()
        job = self.app.db.job(self.jid)
        with self.assertRaisesRegex(Refused, 'windows-plan'):
            self.app.connectors.tool_plan(job, 'owner', 'legacy_session_close', {'sessionId': SESSION}, self.app.command('tool', job))
        marker = ORIGIN.set('monkey_api_client')
        try:
            self.assertEqual((await self.app.gateway.invoke('windows_result', {'job_id': self.jid}))['target_id'], 'legacy.win10')
            with self.assertRaises(Refused): await self.app.dispatch('windows-plan', self.jid, action='close')
        finally: ORIGIN.reset(marker)
        with self.assertRaises(Refused):
            self.app.execution.save(self.jid, 'fixture.redirect', {'windows_binding_id': 'other'}, 'Must fail')

    async def test_binding_floor_is_atomic_and_not_lowered_by_general_capture(self):
        self.assertEqual(self.app.db.db.execute('PRAGMA user_version').fetchone()[0], 2)
        with patch.object(self.app.db, '_record', side_effect=RuntimeError('fixture storage failure')):
            with self.assertRaisesRegex(RuntimeError, 'storage failure'): await self.bind()
        self.assertEqual(self.app.db.db.execute('PRAGMA user_version').fetchone()[0], 2)
        self.assertFalse(self.app.db.job(self.jid).get('windows_binding_id'))
        await self.bind()
        self.assertEqual(self.app.db.db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.app.db.add_work('Fixture scope only', 'build', {'application_runtime': {}}, self.app.command('fixture'))
        self.assertEqual(self.app.db.db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.app.audit.verify()

    async def test_stale_status_and_changed_catalog_block_before_dispatch(self):
        await self.open()
        original = dt.datetime
        later = original.now(dt.timezone.utc) + dt.timedelta(seconds=60)
        with patch('monkey.windows.dt.datetime') as clock:
            clock.now.return_value = later
            clock.fromisoformat.side_effect = original.fromisoformat
            with self.assertRaises(Refused): await self.run_action('close')
        count = len(self.fixture.calls)
        await self.app.connectors.connect_config('owner', {'transport': 'streamable-http', 'url': 'http://127.0.0.1:12345/mcp'})
        with self.assertRaisesRegex(Refused, 'provider changed'): await self.run_action('status')
        self.assertEqual(len(self.fixture.calls), count)

    async def test_denied_close_is_retained_and_never_claimed_cold(self):
        await self.open()
        self.fixture.override = {'ok': False, 'error': 'User denied consent for legacy_session_close.'}
        job, plan = await self.run_action('close')
        viewed = self.app.windows.view(job)
        self.assertEqual(job['tool_delivery'], 'UNKNOWN')
        self.assertFalse(viewed['session_closed_reported'])
        self.assertEqual(viewed['receipts'][-1]['classification'], 'INVALID_OR_ERROR')
        count = len(self.fixture.calls)
        await self.app.dispatch('tool-run', self.jid, exact_hash=plan['plan_hash'], note='Old exact approval cannot replay')
        await self.app.execution.task
        self.assertEqual(len(self.fixture.calls), count)

    async def test_separate_inspection_links_without_resolving_or_replaying_unknown_open(self):
        await self.bind(); await self.run_action('catalog')
        self.fixture.lose = 'legacy_session_open'
        job, _ = await self.run_action('open')
        original = job['tool_call_id']
        self.fixture.lose = None
        inspection = self.new_job('INSPECT-1')
        job = self.app.db.job(inspection)
        plan = self.app.connectors.tool_plan(job, 'owner', 'legacy_session_status', {'sessionId': SESSION},
                                            self.app.command('tool', job))['tool_plan']
        await self.app.dispatch('tool-run', inspection, exact_hash=plan['plan_hash'], note='Separate explicit status inspection')
        await self.app.execution.task
        call = self.app.db.job(inspection)['tool_call_id']
        observed = self.app.connectors.observed(call)[1]
        viewed = await line(self.app, '/windows-sync '+self.jid+' --call '+call)
        self.assertEqual(viewed['delivery'], 'UNKNOWN')
        self.assertEqual(viewed['session']['sessionId'], SESSION)
        self.assertEqual(viewed['receipts'][-1]['at'], observed['at'])
        self.assertEqual(self.app.db.job(self.jid)['tool_call_id'], original)
        with self.assertRaises(Refused): await self.app.dispatch('windows-sync', self.jid, call_id=call)
        self.assertEqual(sum(n == 'legacy_session_open' for n, _ in self.fixture.calls), 1)
        self.app.audit.verify()

    async def test_provider_tick_requires_new_workflow_status(self):
        await self.start_workflow()
        self.fixture.workflow_stage = 'awaiting-provider'
        await self.run_action('workflow-step')
        job, _ = await self.run_action('provider-tick')
        self.assertEqual(job['tool_delivery'], 'RETURNED_UNVERIFIED', job['reason'])
        with self.assertRaisesRegex(Refused, 'fresh workflow'):
            await self.run_action('provider-tick')
        self.assertFalse(self.app.windows.view(job)['workflow_completed_reported'])

    async def test_cleanup_advance_survives_disabled_actuation_policy(self):
        await self.start_workflow()
        self.fixture.workflow_stage = 'cleanup-required'
        await self.run_action('workflow-step')
        self.fixture.allowed = False
        await self.run_action('readiness')
        await self.run_action('cleanup-tick')
        await self.run_action('workflow-status')
        self.fixture.workflow_stage = 'completed'
        job, _ = await self.run_action('workflow-step')
        self.assertEqual(job['tool_delivery'], 'RETURNED_UNVERIFIED', job['reason'])

    async def test_input_cannot_inject_action_arguments_or_mutate_bound_task_mode(self):
        await self.open()
        count = len(self.fixture.calls)
        with self.assertRaises(Refused): await self.run_action('close', {'sessionId': 'different-session', 'approved': True})
        with self.assertRaises(Refused): self.app.worker.start(self.jid)
        with self.assertRaises(Refused):
            self.app.execution.save(self.jid, 'fixture.project', {'contract_id': 'arbitrary'}, 'Must fail')
        self.assertEqual(len(self.fixture.calls), count)
