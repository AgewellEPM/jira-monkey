"""Actual local source edits and sandbox checks without a Kist dependency."""
import asyncio
import datetime as dt
from pathlib import Path
import sys
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from monkey import admission
from monkey.app import App
from monkey.common import Refused


@unittest.skipUnless(sys.platform=='darwin','Actual native verifier qualification requires macOS')
class ProjectAdmission(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='monkey-own-project-',dir=Path(tempfile.gettempdir()).resolve())
        self.addAsyncCleanup(self.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        (self.workspace/'calc.py').write_text('def add(a, b):\n    return a - b\n')
        (self.workspace/'test_calc.py').write_text('from calc import add\nassert add(2, 3)==5\nassert add(-1, 1)==0\nprint("two actual checks passed")\n')
        self.app=App(self.root/'state',offline=True)
        self.jid=self.app.db.add({'source':'local','instance':'local://monkey','key':'NATIVE-PROJECT',
            'revision':'fixture','title':'Correct addition','body':'Add positive and negative numbers.'},fixture=True)['id']
        self.patches=[patch('monkey.execution.SML.execute',AsyncMock(side_effect=AssertionError('Kist must not run'))),
            patch('monkey.execution.captain.build',AsyncMock(side_effect=AssertionError('Captain must not build'))),
            patch('monkey.execution.captain.judge',AsyncMock(side_effect=AssertionError('Captain must not run')))]
        for value in self.patches: value.start()

    async def cleanup(self):
        try:
            if hasattr(self,'app'): await self.app.close()
        finally:
            for value in getattr(self,'patches',[]): value.stop()
            self.temp.cleanup()

    def job(self): return self.app.db.job(self.jid)

    async def prepare(self):
        await self.app.dispatch('project',self.jid,path=str(self.workspace),objective='Return the sum of the two numbers',
            writes=['calc.py'],reads=[],verify=[str(Path(sys.executable).resolve()),'test_calc.py'],expect=['calc.py=return a + b'])
        current=dt.datetime.now(dt.timezone.utc)
        await self.app.dispatch('schedule',self.jid,start=(current-dt.timedelta(minutes=1)).isoformat(),
            finish=(current+dt.timedelta(hours=1)).isoformat(),timezone='UTC')
        await self.app.dispatch('explore',self.jid);await self.app.execution.task
        self.assertEqual(self.job()['execution_state'],'READY_TO_PLAN',self.job()['reason'])
        await self.app.execution.plan(self.jid,{'understanding':'The captured function subtracts; the existing verifier requires addition.',
            'questions':[],'edits':[{'path':'calc.py','content':'def add(a, b):\n    return a + b\n','reason':'Correct the operator-requested arithmetic'}],
            'learned_rules':[]})
        self.plan=self.app.execution.record(self.job(),'plan_id')
        await self.app.dispatch('authorize',self.jid,exact_hash=self.plan['plan_hash'],note='Inspected the exact change and pinned checks')

    async def execute(self):
        await self.app.dispatch('execute',self.jid);await self.app.execution.task

    async def test_edit_verify_signoff_and_signed_trace_without_kist(self):
        await self.prepare();await self.execute()
        self.assertEqual(self.job()['execution_state'],'AWAITING_SIGNOFF',self.job()['reason'])
        contract,result,effects=self.app.execution.verify_result(self.job())
        self.assertEqual(contract['admission']['kind'],admission.KIND)
        self.assertEqual(len(effects),2)
        self.assertIn('two actual checks passed',effects[-1]['evidence']['result']['test']['output'])
        self.assertTrue(all(e['evidence']['runtime']==admission.KIND for e in effects))
        await self.app.dispatch('signoff',self.jid,exact_hash=result['result_hash'],note='Inspected corrected source and observed checks')
        self.assertEqual(self.job()['execution_state'],'COMPLETED')
        kinds=[r['kind'] for r in self.app.audit.view(self.jid)['trace']]
        self.assertEqual(kinds.count('admission.claimed'),3)
        self.assertNotIn('sml.requested',kinds)
        self.app.audit.verify()

    async def test_stale_source_and_forged_authority_block_before_write(self):
        await self.prepare()
        authorization=self.app.execution.record(self.job(),'plan_authorization_id')
        with self.assertRaises(Refused):
            self.app.execution.check_seal({**authorization,'operator':'model'})
        (self.workspace/'calc.py').write_text('operator changed source\n')
        await self.execute()
        self.assertEqual(self.job()['execution_state'],'NEEDS_INPUT')
        self.assertEqual((self.workspace/'calc.py').read_text(),'operator changed source\n')

    async def test_copied_current_interpreter_is_pinned_without_trusting_siblings(self):
        runtime=self.root/'runtime';runtime.mkdir()
        current=runtime/'python';shutil.copy2(Path(sys.executable).resolve(),current)
        sibling=runtime/'unrelated';shutil.copy2(current,sibling)
        options={'path':str(self.workspace),'objective':'Return the sum','writes':['calc.py'],'reads':[],
            'expect':['calc.py=return a + b']}
        with patch('sys.executable',str(current)):
            with self.assertRaisesRegex(Refused,'Verifier executable must'):
                await self.app.dispatch('project',self.jid,verify=[str(sibling),'test_calc.py'],**options)
            await self.app.dispatch('project',self.jid,verify=[str(current),'test_calc.py'],**options)
            contract=self.app.execution.record(self.job(),'contract_id')
            self.assertIn(str(current),contract['verifier_runtime'])
            self.assertGreater(len(contract['verifier_runtime']),1)
            self.app.execution.stable(self.job(),contract)
        # Historical review retains the recorded runtime after the current
        # interpreter changes; it does not grant another execution.
        self.app.execution.stable(self.job(),contract,for_dispatch=False)
        with current.open('ab') as stream: stream.write(b'fixture modification')
        with self.assertRaisesRegex(Refused,'Verification executable changed'):
            self.app.execution.stable(self.job(),contract,for_dispatch=False)

    async def test_changed_receipt_cannot_be_signed_off(self):
        await self.prepare();await self.execute()
        _,result,effects=self.app.execution.verify_result(self.job())
        Path(effects[0]['evidence']['claim_path']).write_text('{"tampered":true}')
        with self.assertRaises(Refused):
            await self.app.dispatch('signoff',self.jid,exact_hash=result['result_hash'],note='Must not accept tampering')
        self.assertEqual(self.job()['execution_state'],'AWAITING_SIGNOFF')

    async def test_upgrade_preserves_review_of_completed_effects_but_blocks_old_execution(self):
        await self.prepare();await self.execute()
        _,result,_=self.app.execution.verify_result(self.job())
        upgraded={**admission.LOADED_RECIPE,'sources':{**admission.LOADED_RECIPE['sources'],'future.py':'1'*64}}
        with patch.object(admission,'LOADED_RECIPE',upgraded),patch.object(admission,'recipe',return_value=upgraded):
            contract,_,_=self.app.execution.verify_result(self.job())
            with self.assertRaises(Refused):
                self.app.execution.stable(self.job(),contract)
            await self.app.dispatch('signoff',self.jid,exact_hash=result['result_hash'],note='Inspected retained effects after upgrade')
            signed=self.app.execution.record(self.job(),'execution_signoff_id')
            self.assertTrue(signed['admission']['runtime_changed'])
        self.assertEqual(self.job()['execution_state'],'COMPLETED')

    async def test_zero_test_exit_does_not_establish_readiness(self):
        (self.workspace/'test_calc.py').write_text('import unittest\nunittest.main()\n')
        await self.prepare();await self.execute()
        self.assertEqual(self.job()['execution_state'],'NEEDS_INPUT')
        self.assertIn('zero tests',self.job()['reason'])
        self.assertIn('return a + b',(self.workspace/'calc.py').read_text())

    async def test_response_loss_retains_edit_and_restart_never_replays(self):
        await self.prepare()
        original=admission.put
        def lose_receipt(path,value):
            if path.name=='receipt.json' and value['result'].get('kind')=='write':
                raise OSError('Private fixture: process lost after source effect')
            return original(path,value)
        with patch.object(admission,'put',lose_receipt):
            await self.execute()
        self.assertEqual(self.job()['execution_state'],'NEEDS_INPUT')
        self.assertIn('return a + b',(self.workspace/'calc.py').read_text())
        claims=list((self.root/'state'/'executions').glob('execution_*-*/claim.json'))
        self.assertEqual(len(claims),1)
        self.assertFalse(claims[0].with_name('receipt.json').exists())
        before=(self.workspace/'calc.py').stat().st_mtime_ns
        await self.app.close();self.app=App(self.root/'state',offline=True)
        await self.execute()
        self.assertEqual((self.workspace/'calc.py').stat().st_mtime_ns,before)
        self.assertEqual(list((self.root/'state'/'executions').glob('execution_*-*/claim.json')),claims)
        self.app.audit.verify()

    async def test_pause_takes_effect_after_recorded_write_boundary(self):
        await self.prepare()
        done,release=asyncio.Event(),asyncio.Event()
        original=admission.ProjectAdmission.execute
        async def delayed(runtime,*args,**kwargs):
            result=await original(runtime,*args,**kwargs)
            if result['result'].get('kind')=='write':
                done.set();await release.wait()
            return result
        with patch.object(admission.ProjectAdmission,'execute',delayed):
            await self.app.dispatch('execute',self.jid)
            await asyncio.wait_for(done.wait(),4)
            start=time.perf_counter()
            await self.app.dispatch('status')
            self.assertLess(time.perf_counter()-start,.1)
            await self.app.dispatch('pause',self.jid)
            self.assertNotEqual(self.job()['execution_state'],'PAUSED')
            release.set()
            async with asyncio.timeout(3):
                while self.job()['execution_state']!='PAUSED': await asyncio.sleep(.01)
            await self.app.dispatch('resume',self.jid)
            await self.app.execution.task
        self.assertEqual(self.job()['execution_state'],'AWAITING_SIGNOFF',self.job()['reason'])
