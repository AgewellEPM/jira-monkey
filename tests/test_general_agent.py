import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from monkey.app import App
from monkey.browser import Browser
from monkey.cli import line, parser
from monkey.common import Refused
from monkey.demo import demo_app
from monkey.ui import rail, render
from monkey.adapters import RequestError


class ScriptedAgent:
    """Only decisions are scripted; files, commands, persistence and audit are real."""
    def __init__(self,actions):
        self.actions=iter(actions)
        self.packets=[]
        self.histories=[]

    async def local(self,c,model,pin,instruction,data,shape=None,**kwargs):
        self.packets.append(data)
        self.histories.append(kwargs.get('history'))
        action,arguments=next(self.actions)
        if callable(arguments): arguments=arguments(data)
        return {'action':action,'arguments':arguments,'reason':'Scripted decision for real tool integration',
            'evidence_refs':data['evidence_refs'][-3:]}, {'provider':'fixture','model':'scripted-decisions','fixture':True}


PLAN=('plan',{'steps':['Implement clamp.','Check boundaries.','Repair any observed failure.'],
    'checks':['Negative, inside-range and above-range values clamp correctly.']})
REFLECT=('reflect',{'findings':['The recorded command supplies the actual result.'],
    'adjustments':['Use the observed boundary cases for the next step.'],
    'procedure':['For clamp functions, test values below, within and above the interval.','Run the checks again after any source change.']})
FINISH=('finish',{'summary':'Created and checked clamp.py.','checks':['The recorded verifier checked lower, inner and upper values.'],'source_ids':[]})
SOURCE='def clamp(value, low, high):\n    return min(value, high)\n'
TEST='from clamp import clamp\nassert clamp(-5, 0, 10) == 0\nassert clamp(7, 0, 10) == 7\nassert clamp(50, 0, 10) == 10\nprint("three boundary cases passed")\n'


class RecordedActionFormat(unittest.TestCase):
    def test_history_preserves_decision_values_in_generation_property_order(self):
        from monkey.agent import recorded_history, wire_schema
        original = {'action': 'write_file', 'arguments': {'path': 'Exact Name.py', 'content': 'def f():\n    return "🍌"\n'},
                    'reason': 'Recorded action', 'evidence_refs': ['step_Exact-ID']}
        # Recreate the sorted property order used by signed durable storage.
        stored = json.loads(json.dumps(original, sort_keys=True))
        packet = {'recent_records': [{'applied_decision': stored, 'result': {'changed': True}}],
                  'next_requirement': 'Inspect the result', 'tools': {'read_file': {}}, 'objective': 'Fixture'}
        history = recorded_history(packet)
        assistant = next(m['content'] for m in history if m['role'] == 'assistant')
        decoded = json.loads(assistant)
        shape = wire_schema(['write_file'])['oneOf'][0]
        self.assertEqual(decoded, original)
        self.assertEqual(list(decoded), list(shape['properties']))
        self.assertEqual(list(decoded['arguments']), list(shape['properties']['arguments']['properties']))
        self.assertEqual(list(stored), sorted(stored))
        self.assertIn('committed_record', history[2]['content'])


class GeneralAgent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp')
        self.root=Path(self.temp.name)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.app,_=demo_app(self.root/'state',delay=.001)

    async def asyncTearDown(self):
        await self.app.close()
        self.temp.cleanup()

    async def start(self,actions,objective='Build a clamp function with boundary checks'):
        model=ScriptedAgent(actions);self.app.models.local=model.local
        value=await self.app.dispatch('build',text=objective,path=str(self.workspace))
        await self.app.execution.task
        return self.app.db.job(value['job_id']),model

    async def complete_build(self):
        return await self.start([PLAN,('write_file',{'path':'clamp.py','content':SOURCE}),
            ('write_file',{'path':'check.py','content':TEST}),('run_command',{'argv':['python','check.py']}),REFLECT,
            ('patch_file',{'path':'clamp.py','old':'min(value, high)','new':'max(low, min(value, high))'}),
            ('run_command',{'argv':['python','check.py']}),REFLECT,FINISH])

    async def test_new_project_failure_reflection_repair_accept_and_recall(self):
        job,model=await self.complete_build()
        self.assertEqual(job['agent_state'],'AWAITING_REVIEW',job['reason'])
        observed=self.app.agent.observations(job['id'])
        commands=[r['result'] for r in observed if r['action']=='run_command']
        self.assertNotEqual(commands[0]['exit_code'],0)
        self.assertEqual(commands[1]['exit_code'],0)
        self.assertIn('three boundary cases passed',commands[1]['output'])
        scope={};exec((self.workspace/'clamp.py').read_text(),scope)
        # Held-out values differ from the agent's own verifier.
        self.assertEqual([scope['clamp'](v,-9,3) for v in (-11,-4,8)],[-9,-4,3])
        self.assertEqual(job['call_count'],9)
        self.assertEqual(job['agent_tool_count'],5)
        self.assertIn('plan',model.packets[0]['tools'])
        self.assertTrue(all('plan' not in p['tools'] for p in model.packets[1:]))
        result=self.app.execution.record(job,'agent_result_id')
        self.assertIsNone(Browser(self.app).completed_at(job))
        accepted=await self.app.dispatch('agent-accept',job['id'],exact_hash=result['result_hash'],note='Reviewed actual output and independent cases')
        self.assertTrue(accepted['accepted'])
        self.assertIsNotNone(Browser(self.app).completed_at(self.app.db.job(job['id'])))
        lessons=self.app.agent.experience.recommend('Build another clamp function with boundary checks','build')
        self.assertEqual(lessons[0]['source_jobs'],[job['id']])
        self.assertTrue(any(p['phase']=='REFLECTING' for p in model.packets))
        self.assertIn('boundary cases',render(self.app.agent.view(self.app.db.job(job['id']))))
        self.app.audit.verify()

    async def test_unchanged_original_budgets_on_steer_and_continue(self):
        job,_=await self.start([PLAN,('ask',{'question':'Which output format?'})])
        old=(job['call_count'],job['agent_deadline'],job['agent_limits'])
        await self.app.dispatch('steer',job['id'],text='Use JSON output')
        self.app.models.local=ScriptedAgent([REFLECT,('ask',{'question':'Which fields?'})]).local
        await self.app.dispatch('agent-continue',job['id'])
        await self.app.execution.task
        after=self.app.db.job(job['id'])
        self.assertEqual(after['call_count'],old[0]+2)
        self.assertEqual((after['agent_deadline'],after['agent_limits']),old[1:])
        for field,value in [('work_type',None),('agent_deadline',time.time()+99999),('agent_limits',{'calls':1000,'tools':1000,'seconds':1000})]:
            with self.assertRaises(Refused): self.app.execution.save(job['id'],'fixture.tamper',{field:value},'Must fail')

    async def test_status_and_steering_during_delayed_inference_discard_stale_write(self):
        ready=asyncio.Event();release=asyncio.Event()
        count=0
        async def local(c,m,p,instruction,data,shape=None,**kwargs):
            nonlocal count
            count+=1
            if count==1: action,arguments=PLAN
            elif count==2:
                ready.set();await release.wait()
                action,arguments='write_file',{'path':'stale.txt','content':'must never be executed'}
            elif count==3: action,arguments=REFLECT
            else: action,arguments='ask',{'question':'Direction has been updated.'}
            return {'action':action,'arguments':arguments,'reason':'Delayed fixture','evidence_refs':data['evidence_refs'][-1:]},{'fixture':True}
        self.app.models.local=local
        value=await self.app.dispatch('build',text='Build a small file',path=str(self.workspace));jid=value['job_id']
        await asyncio.wait_for(ready.wait(),2)
        started=time.perf_counter();status=await self.app.dispatch('status')
        self.assertLess(time.perf_counter()-started,.1)
        self.assertEqual(status['active_job'],jid)
        self.assertIn('ACTING',rail(self.app,120))
        await self.app.dispatch('steer',jid,text='Only investigate; do not write the stale file')
        release.set();await self.app.execution.task
        self.assertFalse((self.workspace/'stale.txt').exists())
        self.assertEqual(self.app.db.job(jid)['agent_tool_count'],0)

    async def test_exact_source_change_blocks_acceptance(self):
        job,_=await self.complete_build()
        result=self.app.execution.record(job,'agent_result_id')
        (self.workspace/'clamp.py').write_text('unexpected external edit\n')
        with self.assertRaises(Refused):
            await self.app.dispatch('agent-accept',job['id'],exact_hash=result['result_hash'],note='Stale review')
        self.assertFalse(self.app.agent.experience.records())

    async def test_history_projects_only_bound_completed_actions_and_real_results(self):
        job,model=await self.complete_build()
        history=model.histories[3]
        decisions=[json.loads(row['content']) for row in history if row['role']=='assistant']
        self.assertEqual([row['action'] for row in decisions],['write_file','write_file'])
        self.assertEqual([row['arguments']['content'] for row in decisions],[SOURCE,TEST])
        records=[json.loads(row['content'])['committed_record'] for row in history
            if row['role']=='user' and 'committed_record' in json.loads(row['content'])]
        self.assertEqual([row['result']['path'] for row in records],['clamp.py','check.py'])
        self.assertEqual(records[0]['result']['after_sha256'],hashlib.sha256(SOURCE.encode()).hexdigest())
        self.assertTrue(all(row['role'] in {'user','assistant'} for row in history))
        self.assertTrue(all(len(history)<=14 for history in model.histories))
        for row in self.app.agent.records(job['id']):
            if row['kind']=='agent.operation': self.assertTrue(row.get('decision_id'))

    async def test_scope_violation_stops_without_changing_host(self):
        job,model=await self.start([PLAN,('write_file',{'path':'../outside.txt','content':'no'})])
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertFalse((self.root/'outside.txt').exists())
        self.assertEqual(len(model.packets),2)
        self.assertEqual(len(self.app.agent.unresolved(job['id'])),1)
        with self.assertRaises(Refused): await self.app.dispatch('agent-continue',job['id'])

    async def test_no_fake_success_without_observed_check(self):
        job,_=await self.start([PLAN,REFLECT,FINISH])
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertIsNone(job['agent_result_id'])

    async def test_transport_retry_preserves_scope_and_charges_original_budget(self):
        scripted=ScriptedAgent([PLAN,('ask',{'question':'Fixture reached the next valid step'})])
        count=0
        async def local(*args,**kwargs):
            nonlocal count
            count+=1
            if count==1: raise RequestError('Private fixture timeout')
            return await scripted.local(*args,**kwargs)
        self.app.models.local=local
        with patch('monkey.adapters.retry_delay',return_value=.02):
            value=await self.app.dispatch('build',text='Build a fixture',path=str(self.workspace))
            await self.app.execution.task
        job=self.app.db.job(value['job_id'])
        self.assertEqual(count,3)
        self.assertEqual(job['call_count'],3)
        self.assertEqual(job['retry_count'],1)
        self.assertEqual(job['agent_tool_count'],0)
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertTrue(any(r['kind']=='agent.transport_retry' for r in self.app.agent.records(job['id'])))

    async def test_repeated_outage_exhausts_original_retries_without_tool_actions(self):
        async def down(*args,**kwargs): raise RequestError('Private fixture outage',503)
        self.app.models.local=down
        with patch('monkey.adapters.retry_delay',return_value=.001):
            value=await self.app.dispatch('build',text='Build a fixture',path=str(self.workspace))
            await self.app.execution.task
        job=self.app.db.job(value['job_id'])
        self.assertEqual((job['call_count'],job['retry_count'],job['agent_tool_count']),(3,2,0))
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertFalse(list(self.workspace.iterdir()))

    async def test_refusal_is_not_retried_or_reinterpreted(self):
        async def refused(*args,**kwargs): raise Refused('Provider refused the request')
        self.app.models.local=refused
        value=await self.app.dispatch('build',text='Build a fixture',path=str(self.workspace))
        await self.app.execution.task
        job=self.app.db.job(value['job_id'])
        self.assertEqual((job['call_count'],job['retry_count'],job['agent_tool_count']),(1,0,0))
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')

    async def test_inner_tool_timeout_does_not_claim_original_deadline_exhaustion(self):
        from unittest.mock import AsyncMock
        with patch.object(self.app.agent,'tool',AsyncMock(side_effect=TimeoutError)):
            job,_=await self.start([PLAN,('list_files',{})])
        self.assertGreater(job['agent_deadline'],time.time())
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertIn('TimeoutError',job['reason'])
        self.assertFalse(any(row['kind']=='agent.budget_exhausted' for row in self.app.agent.records(job['id'])))

    async def test_repeated_unchanged_writes_stop_with_all_observations_retained(self):
        write=('write_file',{'path':'clamp.py','content':SOURCE})
        job,_=await self.start([PLAN,write,write,REFLECT,write,REFLECT,write])
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertIn('Three consecutive source operations made no change',job['reason'])
        self.assertEqual(job['call_count'],7)
        self.assertEqual(job['agent_no_progress_count'],3)
        self.assertEqual(len(self.app.agent.observations(job['id'])),4)
        self.assertIsNone(job['agent_result_id'])

    async def test_zero_discovered_tests_cannot_establish_readiness(self):
        job,model=await self.start([PLAN,('run_command',{'argv':['python','-m','unittest','discover']}),REFLECT,
            ('ask',{'question':'The checker found no tests.'})])
        command=next(r for r in self.app.agent.observations(job['id']) if r['action']=='run_command')
        self.assertEqual(command['result']['exit_code'],0)
        self.assertFalse(command['result']['check_assessment']['usable'])
        self.assertEqual(model.packets[2]['phase'],'REFLECTING')
        self.assertNotIn('finish',model.packets[-1]['tools'])
        self.assertIsNone(job.get('agent_checked_generation'))

    async def test_repeated_existing_directory_stops_with_exact_results(self):
        directory=('make_directory',{'path':'package'})
        job,model=await self.start([PLAN,directory,directory,REFLECT,directory,REFLECT,directory])
        self.assertEqual(job['agent_state'],'NEEDS_INPUT')
        self.assertEqual(job['agent_no_progress_count'],3)
        observed=self.app.agent.observations(job['id'])
        self.assertEqual([row['result']['changed'] for row in observed],[True,False,False,False])
        self.assertEqual(observed[0]['result']['created'],['package'])
        self.assertIn('Use write_file',model.packets[2]['next_requirement'])
        self.assertEqual(list((self.workspace/'package').iterdir()),[])
        self.assertIsNone(job['agent_result_id'])

    async def test_crash_claim_is_inspectable_and_never_replayed(self):
        job,_=await self.start([PLAN,('ask',{'question':'Stop here'})])
        operation=self.app.agent.save(job['id'],'operation',{'action':'write_file','arguments':{'path':'interrupted.txt','content':'x'},'status':'STARTED'},
            {'agent_state':'ACTING','agent_tool_count':1})
        (self.workspace/'interrupted.txt').write_text('x')
        await self.app.close()
        self.app,_=demo_app(self.root/'state',delay=.001)
        current=self.app.db.job(job['id'])
        self.assertEqual(current['agent_state'],'INTERRUPTED')
        self.assertEqual(self.app.agent.unresolved(job['id']),[operation['id']])
        with self.assertRaises(Refused): await self.app.dispatch('agent-continue',job['id'])
        await self.app.dispatch('agent-resolve',job['id'],note='Read interrupted.txt; its x content exists, no resend')
        self.assertEqual(self.app.agent.unresolved(job['id']),[])
        self.assertEqual((self.workspace/'interrupted.txt').read_text(),'x')

    async def test_command_parser_and_banana_work_menu(self):
        parsed=parser().parse_args(['build','Small','program','--path',str(self.workspace)])
        self.assertEqual(parsed.text,['Small','program'])
        browser=Browser(self.app);browser.section=5
        self.assertEqual((await browser.activate())['prefill'],'/build ')
        browser.section=6
        self.assertEqual((await browser.activate())['prefill'],'/research ')
        browser.slide(1)
        self.assertEqual(browser.pane,'menu')
