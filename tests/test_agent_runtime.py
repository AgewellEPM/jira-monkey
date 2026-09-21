"""Application upgrades must preserve evidence without silently resuming effects."""
import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monkey import admission
from monkey.browser import Browser
from monkey.common import Refused, digest
from monkey.demo import demo_app
from test_general_agent import ScriptedAgent, PLAN, REFLECT, FINISH, SOURCE, TEST


class AgentRuntime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp')
        self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.app,_=demo_app(self.root/'state',delay=.001)

    async def asyncTearDown(self):
        await self.app.close()
        self.temp.cleanup()

    def changed_runtime(self):
        runtime=copy.deepcopy(admission.LOADED_RECIPE)
        runtime['sources']['future-runtime.py']='1'*64
        return runtime

    async def start(self,actions):
        self.model=ScriptedAgent(actions)
        self.app.models.local=self.model.local
        request=await self.app.dispatch('build',text='Build and check a clamp function',path=str(self.workspace))
        await self.app.execution.task
        return self.app.db.job(request['job_id'])

    async def completed_result(self):
        return await self.start([PLAN,
            ('write_file',{'path':'clamp.py','content':SOURCE.replace('min(value, high)','max(low, min(value, high))')}),
            ('write_file',{'path':'check.py','content':TEST}),
            ('run_command',{'argv':['python','check.py']}),REFLECT,FINISH])

    async def test_runtime_is_captured_and_cannot_be_rewritten(self):
        self.assertEqual(self.app.db.db.execute('PRAGMA user_version').fetchone()[0],2)
        job=await self.start([PLAN,('ask',{'question':'Pause this fixture here'})])
        self.assertEqual(job['agent_scope']['application_runtime'],admission.LOADED_RECIPE)
        self.assertEqual(self.app.db.db.execute('PRAGMA user_version').fetchone()[0],3)
        scope={**job['agent_scope'],'application_runtime':self.changed_runtime()}
        with self.assertRaises(Refused):
            self.app.execution.save(job['id'],'fixture.tamper',{'agent_scope':scope},'Must fail')
        self.assertTrue(self.app.agent.view(job)['runtime']['captured'])

    async def test_failed_capture_does_not_commit_a_compatibility_floor_or_job(self):
        with patch.object(self.app.db,'_insert_job',side_effect=RuntimeError('Fixture insertion failure')):
            with self.assertRaisesRegex(RuntimeError,'Fixture insertion failure'):
                await self.app.dispatch('build',text='Build a file',path=str(self.workspace))
        self.assertEqual(self.app.db.db.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertFalse(self.app.db.jobs())
        self.app.audit.verify()

    async def test_source_change_during_inference_retains_decision_without_dispatch(self):
        ready=asyncio.Event();release=asyncio.Event()
        calls=0
        async def local(c,m,p,instruction,data,shape=None,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1: action,arguments=PLAN
            else:
                ready.set();await release.wait()
                action,arguments='write_file',{'path':'late.txt','content':'Must not be applied'}
            return {'action':action,'arguments':arguments,'reason':'Held model fixture',
                'evidence_refs':data['evidence_refs'][-1:]},{'fixture':True}
        self.app.models.local=local
        request=await self.app.dispatch('build',text='Build a file',path=str(self.workspace))
        await asyncio.wait_for(ready.wait(),2)
        with patch.object(admission,'recipe',return_value=self.changed_runtime()):
            release.set();await self.app.execution.task
            status=await self.app.dispatch('status')
            viewed=await self.app.dispatch('agent',request['job_id'])
            self.assertEqual(viewed['state'],'NEEDS_INPUT')
            self.assertTrue(status['jobs'])
        job=self.app.db.job(request['job_id'])
        self.assertEqual((job['call_count'],job['agent_tool_count']),(2,0))
        self.assertIn('code changed',job['reason'])
        self.assertFalse((self.workspace/'late.txt').exists())
        self.assertFalse(self.app.agent.unresolved(job['id']))
        records=self.app.agent.records(job['id'])
        self.assertEqual(len([r for r in records if r['kind']=='agent.decision']),2)
        self.assertFalse(any(r['kind']=='agent.operation' for r in records))

    async def test_restart_upgrade_blocks_continuation_without_resetting_budget(self):
        job=await self.start([PLAN,('ask',{'question':'Pause this fixture here'})])
        limits=(job['call_count'],job['agent_deadline'],job['agent_limits'])
        await self.app.close()
        upgraded=self.changed_runtime()
        with patch.object(admission,'LOADED_RECIPE',upgraded),patch.object(admission,'recipe',return_value=upgraded):
            self.app,_=demo_app(self.root/'state',delay=.001)
            with self.assertRaisesRegex(Refused,'code changed'):
                await self.app.dispatch('agent-continue',job['id'])
            current=self.app.db.job(job['id'])
            self.assertEqual((current['call_count'],current['agent_deadline'],current['agent_limits']),limits)
            self.assertFalse(self.app.agent.view(current)['runtime']['matches_running_code'])
            await self.app.dispatch('cancel',job['id'])
            self.assertEqual(self.app.db.job(job['id'])['agent_state'],'CANCELLED')

    async def test_finished_effect_is_retained_when_code_changes_before_its_return(self):
        ready=asyncio.Event();release=asyncio.Event()
        original=self.app.agent.tool
        async def held_return(*args):
            result=await original(*args)
            ready.set();await release.wait()
            return result
        self.app.models.local=ScriptedAgent([PLAN,
            ('write_file',{'path':'retained.txt','content':'The actual effect is retained'})]).local
        with patch.object(self.app.agent,'tool',side_effect=held_return):
            request=await self.app.dispatch('build',text='Build a file',path=str(self.workspace))
            await asyncio.wait_for(ready.wait(),2)
            with patch.object(admission,'recipe',return_value=self.changed_runtime()):
                release.set();await self.app.execution.task
        job=self.app.db.job(request['job_id'])
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertEqual((job['call_count'],job['agent_tool_count']),(2,1))
        self.assertEqual((self.workspace/'retained.txt').read_text(),'The actual effect is retained')
        self.assertEqual(len(self.app.agent.observations(job['id'])),1)
        self.assertFalse(self.app.agent.unresolved(job['id']))
        self.app.audit.verify()

    async def test_upgrade_allows_historical_signoff_and_records_both_runtime_hashes(self):
        job=await self.completed_result()
        self.assertEqual(job['agent_state'],'AWAITING_REVIEW',job['reason'])
        result=self.app.execution.record(job,'agent_result_id')
        original=job['agent_scope']['application_runtime']
        self.assertEqual(result['scope_hash'],digest(job['agent_scope']))
        self.assertEqual(result['application_runtime_hash'],digest(original))
        upgraded=self.changed_runtime()
        with patch.object(admission,'LOADED_RECIPE',upgraded),patch.object(admission,'recipe',return_value=upgraded):
            accepted=await self.app.dispatch('agent-accept',job['id'],exact_hash=result['result_hash'],
                note='Inspected retained source and actual fixed checker output after upgrade')
            runtime=accepted['runtime']
            self.assertEqual(runtime['execution_runtime_hash'],digest(original))
            self.assertEqual(runtime['review_runtime_hash'],digest(upgraded))
            self.assertFalse(runtime['matches_running_code'])
            self.assertFalse(runtime['new_execution_authorized'])
            self.assertIsNotNone(Browser(self.app).completed_at(self.app.db.job(job['id'])))
        self.assertEqual(len(self.model.packets),6)
        signoff=self.app.execution.record(self.app.db.job(job['id']),'agent_signoff_id')
        self.assertEqual(signoff['runtime'],runtime)
        self.app.audit.verify()

    async def test_legacy_scope_remains_inspectable_but_cannot_gain_new_runtime(self):
        job=await self.start([PLAN,('ask',{'question':'Pause this fixture here'})])
        scope={key:value for key,value in job['agent_scope'].items() if key!='application_runtime'}
        legacy=self.app.db.add_work('Retained older work','build',scope,self.app.command('build'))
        with self.assertRaisesRegex(Refused,'no captured application runtime'):
            await self.app.dispatch('agent-continue',legacy['id'])
        viewed=await self.app.dispatch('agent',legacy['id'])
        self.assertFalse(viewed['runtime']['captured'])
        self.assertIsNone(viewed['runtime']['execution_runtime_hash'])
        self.assertEqual(self.app.db.job(legacy['id'])['call_count'],0)
        self.assertNotIn('application_runtime',self.app.db.job(legacy['id'])['agent_scope'])
        await self.app.dispatch('cancel',legacy['id'])

    async def test_even_sealed_result_must_match_its_original_scope(self):
        job=await self.completed_result()
        result=self.app.execution.record(job,'agent_result_id')
        changed={key:value for key,value in result.items() if key not in {'id','kind','at','seal','result_hash'}}
        changed['scope_hash']='0'*64
        changed['result_hash']=digest(changed)
        row=self.app.agent.save(job['id'],'result',changed)
        self.app.execution.save(job['id'],'fixture.result',{'agent_result_id':row['id']},'Retain invalid binding fixture')
        with self.assertRaisesRegex(Refused,'captured scope'):
            await self.app.dispatch('agent-accept',job['id'],exact_hash=row['result_hash'],note='Must reject the scope mismatch')
        self.assertFalse(self.app.agent.experience.records())
