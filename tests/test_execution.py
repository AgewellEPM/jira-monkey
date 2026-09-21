"""Real local SML/Captain/file/sandbox integration, confined to private fixtures."""
import asyncio
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from monkey.app import App
from monkey import captain
from monkey.browser import Browser
from monkey.common import Refused
from monkey.project_tools import ProjectFiles, run_test
from monkey.sml import SML


@unittest.skipUnless(Path('/Users/lukekist/bin/kist-current').is_file() and Path('/Volumes/PRO-G40/kist-loops').is_dir(), 'Configured local Kist runtime and Captain source are required')
class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = tempfile.TemporaryDirectory(prefix='monkey-captain-tests-', dir='/private/tmp')
        cls.built = asyncio.run(captain.build(cls.compiler.name, '/Volumes/PRO-G40/kist-loops'))

    @classmethod
    def tearDownClass(cls):
        cls.compiler.cleanup()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='monkey-execution-tests-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'calc.py').write_text('def add(a, b):\n    return a - b\n')
        (self.workspace / 'test_calc.py').write_text('from calc import add\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\nprint("two arithmetic checks passed")\n')
        (self.workspace / 'AGENTS.md').write_text('Keep the arithmetic interface. Never change the verifier.\n')
        self.app = App(self.root / 'state', offline=True)
        self.app.db.configure({'kist_binary':'/Users/lukekist/bin/kist-current','kist_source':'/Volumes/PRO-G40/kist-loops','admission_backend':'kist'})
        self.jid = self.app.db.add({'source':'local','instance':'local://monkey','key':'REQ-101','revision':'fixture-1','title':'Repair arithmetic','body':'Make add return the sum.'}, fixture=True)['id']
        self.compile_patch = patch('monkey.execution.captain.build', AsyncMock(return_value=self.built))
        self.compile_patch.start()

    async def asyncTearDown(self):
        await self.app.close()
        self.compile_patch.stop()
        self.temp.cleanup()

    def job(self):
        return self.app.db.job(self.jid)

    async def configure(self):
        await self.app.dispatch('project', self.jid, path=str(self.workspace), objective='Make add return the sum for positive and negative numbers.',
            writes=['calc.py'], reads=[], verify='/opt/homebrew/bin/python3.11 test_calc.py', expect=['calc.py=return a + b'])
        now = dt.datetime.now(dt.timezone.utc)
        await self.app.dispatch('schedule', self.jid, start=(now-dt.timedelta(minutes=1)).isoformat(), finish=(now+dt.timedelta(hours=1)).isoformat(), timezone='UTC')

    async def ready(self, questions=None, learned=None):
        await self.configure()
        await self.app.dispatch('explore', self.jid)
        await self.app.execution.task
        self.assertEqual(self.job()['execution_state'], 'READY_TO_PLAN', self.job()['reason'])
        self.proposal = {'understanding':'The function must add two numbers; the pinned verifier checks positive and negative inputs.',
            'questions':questions or [], 'edits':[{'path':'calc.py','content':'def add(a, b):\n    return a + b\n','reason':'The captured function subtracts; the operator asked for addition.'}], 'learned_rules':learned or []}
        await self.app.execution.plan(self.jid, self.proposal)
        self.plan = self.app.execution.record(self.job(), 'plan_id')
        if not questions:
            await self.app.dispatch('authorize', self.jid, exact_hash=self.plan['plan_hash'], note='Reviewed this exact arithmetic change and its fixed verifier.')

    async def run_project(self):
        await self.app.dispatch('execute', self.jid)
        await self.app.execution.task

    async def test_real_edit_sml_receipts_captain_and_exact_signoff(self):
        await self.ready()
        await self.run_project()
        self.assertEqual(self.job()['execution_state'], 'AWAITING_SIGNOFF', self.job()['reason'])
        self.assertIn('return a + b', (self.workspace/'calc.py').read_text())
        self.assertIsNone(Browser(self.app).completed_at(self.job()))
        contract, result, effects = self.app.execution.verify_result(self.job())
        self.assertEqual(len(effects), 2)
        self.assertIn('two arithmetic checks passed', effects[-1]['evidence']['result']['test']['output'])
        kinds = [r['kind'] for r in effects[0]['evidence']['receipts']]
        self.assertLess(kinds.index('approved'), kinds.index('capability_claimed'))
        self.assertLess(kinds.index('capability_claimed'), kinds.index('step_recorded'))
        await self.app.dispatch('signoff', self.jid, exact_hash=result['result_hash'], note='Observed the corrected arithmetic and two fixed checks.')
        self.assertEqual(self.job()['execution_state'], 'COMPLETED')
        self.assertIsNotNone(Browser(self.app).completed_at(self.job()))

    async def test_missing_understanding_and_questions_never_write(self):
        answer = await self.app.dispatch('project', self.jid, objective='do the thing')
        self.assertIn('missing', answer)
        self.assertNotIn('contract_id', self.job())
        await self.ready(questions=['Which behavior is intended for non-numeric inputs?'])
        with self.assertRaises(Refused):
            await self.app.dispatch('authorize', self.jid, exact_hash=self.plan['plan_hash'], note='force it')
        self.assertIn('return a - b', (self.workspace/'calc.py').read_text())

    async def test_changed_rules_and_changed_source_block_before_write(self):
        await self.ready()
        (self.workspace/'AGENTS.md').write_text('Stop: operator rules changed.\n')
        await self.run_project()
        self.assertEqual(self.job()['execution_state'], 'NEEDS_INPUT')
        self.assertIn('rules changed', self.job()['reason'])
        self.assertIn('return a - b', (self.workspace/'calc.py').read_text())

    async def test_result_tamper_and_stale_schedule_cannot_signoff(self):
        await self.ready()
        await self.run_project()
        result = self.app.execution.record(self.job(), 'execution_id')
        (self.workspace/'calc.py').write_text('claimed done\n')
        with self.assertRaises(Refused):
            await self.app.dispatch('signoff', self.jid, exact_hash=result['result_hash'], note='review')
        (self.workspace/'calc.py').write_text(result['after']['calc.py']['text'])
        await self.app.dispatch('return', self.jid, note='Need new sprint dates')
        self.assertIsNone(self.job().get('plan_authorization_id'))
        self.assertIsNone(Browser(self.app).completed_at(self.job()))
        with self.assertRaises(Refused):
            await self.app.dispatch('signoff', self.jid, exact_hash=result['result_hash'], note='review')

    async def test_learned_restriction_needs_operator_and_cannot_expand_scope(self):
        await self.ready(learned=[{'path':'calc.py','reason':'Advisory: preserve the original arithmetic module.'}])
        self.assertEqual(self.app.execution.record(self.job(), 'contract_id')['admitted_rules'], [])
        with self.assertRaises(Refused):
            await self.app.dispatch('admit-rule', self.jid, path='../outside', note='bypass')
        await self.app.dispatch('admit-rule', self.jid, path='calc.py', note='Do not modify this module until clarified')
        self.assertIsNone(self.job()['plan_authorization_id'])
        self.assertEqual(self.job()['execution_state'], 'READY_TO_EXPLORE')

    async def test_forged_receipt_and_original_budget_fail_closed(self):
        await self.ready()
        authority = self.app.execution.record(self.job(), 'plan_authorization_id')
        authority['operator'] = 'model-approved'
        with self.assertRaises(Refused):
            self.app.execution.check_seal(authority)
        self.app.execution.save(self.jid, 'fixture.budget', {'tool_count':24}, 'Budget fixture')
        await self.run_project()
        self.assertEqual(self.job()['execution_state'], 'NEEDS_INPUT')
        self.assertEqual(self.job()['tool_count'], 24)
        self.assertIn('return a - b', (self.workspace/'calc.py').read_text())

    async def test_sandbox_denies_host_read_network_write_and_fork(self):
        secret = self.root/'state/operator-private.txt'
        secret.write_text('host-owned secret')
        source = f'''import os, socket
from pathlib import Path
blocked = []
for name, action in [
 ('host-read', lambda: Path({str(secret)!r}).read_text()),
 ('project-write', lambda: Path('calc.py').write_text('tampered')),
 ('network', lambda: socket.create_connection(('127.0.0.1', 11434), timeout=1)),
 ('fork', lambda: os.fork())]:
 try: action()
 except (PermissionError, OSError): blocked.append(name)
assert len(blocked) == 4, blocked
print('four sandbox boundaries enforced')
'''
        (self.workspace/'sandbox_check.py').write_text(source)
        result = await run_test(self.workspace, ['/opt/homebrew/bin/python3.11','sandbox_check.py'], self.root/'scratch')
        self.assertEqual(result['exit_code'], 0, result)
        self.assertIn('four sandbox boundaries enforced', result['output'])
        with self.assertRaises((Refused, OSError)):
            ProjectFiles(self.workspace).read('../state/operator-private.txt')
        (self.workspace/'linked.py').symlink_to(secret)
        with self.assertRaises((Refused, OSError)):
            ProjectFiles(self.workspace).read('linked.py')

    async def test_failed_verifier_and_restart_do_not_replay_effects(self):
        (self.workspace/'test_calc.py').write_text('raise SystemExit(7)\n')
        await self.ready()
        await self.run_project()
        self.assertEqual(self.job()['execution_state'], 'NEEDS_INPUT')
        self.assertIn('Verifier failed', self.job()['reason'])
        count = self.job()['tool_count']
        self.app.execution.save(self.jid, 'fixture.interruption', {'execution_state':'EXECUTING'}, 'Persist interrupted fixture')
        await self.app.close()
        self.app = App(self.root/'state', offline=True)
        self.assertEqual(self.job()['execution_state'], 'INTERRUPTED')
        self.assertEqual(self.job()['tool_count'], count)
        self.assertIn('return a + b', (self.workspace/'calc.py').read_text())

    async def test_status_and_pause_remain_usable_during_real_effect(self):
        await self.ready()
        effect_saved, release = asyncio.Event(), asyncio.Event()
        original = SML.execute
        async def delayed(runtime, *args, **kwargs):
            value = await original(runtime, *args, **kwargs)
            if value['result'].get('kind') == 'write':
                effect_saved.set()
                await release.wait()
            return value
        with patch.object(SML, 'execute', delayed):
            await self.app.dispatch('execute', self.jid)
            await asyncio.wait_for(effect_saved.wait(), 4)
            started = time.perf_counter()
            status = await self.app.dispatch('status')
            self.assertLess(time.perf_counter()-started, .1)
            self.assertEqual(status['active_job'], self.jid)
            pause = await self.app.dispatch('pause', self.jid)
            self.assertIn('requested', pause['message'])
            self.assertNotEqual(self.job()['execution_state'], 'PAUSED')
            release.set()
            async with asyncio.timeout(3):
                while self.job()['execution_state'] != 'PAUSED':
                    await asyncio.sleep(.01)
            await self.app.dispatch('resume', self.jid)
            await self.app.execution.task
        self.assertEqual(self.job()['execution_state'], 'AWAITING_SIGNOFF', self.job()['reason'])
        self.assertEqual(self.job()['tool_count'], 3)


if __name__ == '__main__':
    unittest.main()
