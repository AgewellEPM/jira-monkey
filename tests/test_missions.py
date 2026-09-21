"""Real SML/MCP effects; deterministic agent assessments for fault injection."""
import asyncio
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import copy
from unittest.mock import AsyncMock,patch

from monkey import captain
from monkey.app import App
from monkey.browser import Browser
from monkey.common import Refused
from monkey.ui import repl
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.formatted_text import to_formatted_text


class AgentModel:
    def __init__(self):
        self.questions = []
        self.entered,self.release = asyncio.Event(),asyncio.Event()
        self.release.set()
        self.calls = []
        self.next_plan = None

    async def catalog(self,c):
        return []

    async def local(self,c,model,pin,instruction,data,shape,**options):
        self.calls.append(data)
        self.entered.set()
        await self.release.wait()
        if 'step' not in data:
            return self.next_plan,{'fixture':True,'model':'deterministic planning fixture'}
        return {'decision':'NEEDS_INPUT' if self.questions else 'PROCEED','reason':'Assess the exact approved fixture step.',
            'questions':self.questions,'evidence_refs':[data['evidence_refs'][0]]},{'fixture':True,'model':'deterministic test assessment'}


@unittest.skipUnless(Path('/Users/lukekist/bin/kist-current').is_file() and Path('/Volumes/PRO-G40/kist-loops').is_dir(),'Actual local Kist is required')
class MissionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = tempfile.TemporaryDirectory(prefix='monkey-mission-captain-',dir='/private/tmp')
        cls.build = asyncio.run(captain.build(cls.compiler.name,'/Volumes/PRO-G40/kist-loops'))

    @classmethod
    def tearDownClass(cls):
        cls.compiler.cleanup()

    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='monkey-mission-test-',dir='/private/tmp')
        self.root = Path(self.folder.name)
        self.ledger = self.root/'ledger.json'
        self.models = AgentModel()
        self.app = App(self.root/'state',models=self.models)
        self.app.db.configure({'kist_binary':'/Users/lukekist/bin/kist-current','kist_source':'/Volumes/PRO-G40/kist-loops','admission_backend':'kist'})
        self.mockbuild = patch('monkey.captain.build',AsyncMock(return_value=self.build))
        self.mockbuild.start()
        config = self.root/'mcp.json'
        config.write_text(json.dumps({'command':sys.executable,'args':[str(Path(__file__).with_name('mcp_fixture_server.py')),str(self.ledger)],'sandbox':{'write':[str(self.ledger)]}}))
        await self.app.dispatch('connect',name='fixture',path=str(config))
        self.jid = self.app.db.add({'source':'local','instance':'local://monkey','key':'MISSION-1','revision':'fixture',
            'title':'Create and verify Alpha','body':'Use the local fixture to create one task titled Alpha and read it back.'},fixture=True)['id']
        clock = dt.datetime.now(dt.timezone.utc)
        await self.app.dispatch('schedule',self.jid,start=(clock-dt.timedelta(minutes=1)).isoformat(),finish=(clock+dt.timedelta(hours=1)).isoformat(),timezone='UTC')

    async def asyncTearDown(self):
        await self.app.close()
        self.mockbuild.stop()
        self.folder.cleanup()

    def job(self):
        return self.app.db.job(self.jid)

    async def prepare(self, *, lost=False, expected='Alpha', questions=None, authorize=True):
        proposal = {'understanding':'Create exactly one fixture task, then inspect its persisted title.','questions':questions or [],
            'steps':[{'agent':'worker','purpose':'Create the requested fixture task','server':'fixture','tool':'add_task',
                'arguments_json':json.dumps({'title':'Alpha','lose_response':lost})},
                {'agent':'reviewer','purpose':'Read the ledger to check the actual task','server':'fixture','tool':'list_tasks','arguments_json':'{}'}],
            'checks':[{'step':2,'pointer':'/parsedContent/0/tasks/0/title','equals_json':json.dumps(expected)}]}
        await self.app.missions.plan(self.jid,'Create and verify Alpha',proposal)
        self.proposal = proposal
        self.plan = self.app.execution.record(self.job(),'mission_plan_id')
        if not questions and authorize:
            await self.app.dispatch('mission-authorize',self.jid,exact_hash=self.plan['plan_hash'],note='Fixture harness reviewed both exact calls and the expected persisted title')

    async def run_mission(self):
        await self.app.dispatch('mission-run',self.jid)
        await self.app.execution.task

    async def test_agents_execute_only_authorized_steps_and_exact_signoff(self):
        await self.prepare()
        self.assertFalse(self.ledger.exists())
        await self.run_mission()
        self.assertEqual(self.job()['mission_state'],'AWAITING_SIGNOFF',self.job()['reason'])
        self.assertEqual(json.loads(self.ledger.read_text()),[{'title':'Alpha'}])
        self.assertEqual(self.job()['agent_count'],2)
        self.assertEqual(self.job()['call_count'],2)
        self.assertEqual(self.job()['tool_count'],2)
        self.assertEqual(len(self.models.calls),2)
        self.assertEqual(self.app.missions.workers,{})
        plan,result = self.app.missions.verify(self.job())
        for row in result['calls']:
            call,raw = self.app.connectors.observed(row['call_id'])
            self.assertEqual(call['delegation']['mission_id'],plan['id'])
            self.assertIn(plan['id'],call['evidence']['task_id'])
            self.assertEqual(call['evidence']['runtime'],'kist-durable-sml')
        self.assertIsNone(Browser(self.app).completed_at(self.job()))
        await self.app.dispatch('mission-signoff',self.jid,exact_hash=result['result_hash'],note='Observed the exact Alpha action and ledger verification')
        self.assertIsNotNone(Browser(self.app).completed_at(self.job()))
        await self.app.close()
        self.app = App(self.root/'state',models=self.models)
        self.assertIsNotNone(Browser(self.app).completed_at(self.job()))
        with self.assertRaises(Refused):
            await self.run_mission()
        self.assertEqual(len(json.loads(self.ledger.read_text())),1)
        self.assertIsNotNone(Browser(self.app).completed_at(self.job()))

    async def test_vision_cannot_invent_an_unobserved_screen_or_click(self):
        await self.prepare(authorize=False)
        proposal=copy.deepcopy(self.proposal)
        proposal['steps'][1]['agent']='vision'
        await self.app.missions.plan(self.jid,'Create and verify Alpha',proposal)
        plan=self.app.execution.record(self.job(),'mission_plan_id')
        await self.app.dispatch('mission-authorize',self.jid,exact_hash=plan['plan_hash'],note='Fixture: review the exact steps')
        await self.run_mission()
        self.assertEqual(self.job()['mission_state'],'NEEDS_INPUT')
        self.assertIn('Vision needs an image',self.job()['reason'])
        self.assertEqual(json.loads(self.ledger.read_text()),[{'title':'Alpha'}])
        self.assertEqual(len([c for c in self.models.calls if 'step' in c]),1)

    async def test_questions_and_budget_never_start_tools(self):
        await self.prepare(questions=['Which project should contain the task?'])
        with self.assertRaises(Refused):
            await self.app.dispatch('mission-authorize',self.jid,exact_hash=self.plan['plan_hash'],note='Force it')
        self.assertFalse(self.ledger.exists())
        await self.prepare()
        self.models.questions = ['The task objective conflicts with the supplied evidence. Which is intended?']
        await self.run_mission()
        self.assertEqual(self.job()['mission_state'],'NEEDS_INPUT')
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.app.missions.workers,{})
        with self.assertRaises(Refused):
            await self.app.dispatch('mission-authorize',self.jid,exact_hash=self.plan['plan_hash'],note='Retry consumed mission')

    async def test_lost_response_and_restart_do_not_replay_or_advance(self):
        await self.prepare(lost=True)
        await self.run_mission()
        self.assertEqual(self.job()['tool_delivery'],'UNKNOWN')
        self.assertEqual(self.job()['call_count'],1)
        self.assertEqual(self.job()['tool_count'],1)
        self.assertEqual(len(json.loads(self.ledger.read_text())),1)
        await self.app.close()
        self.app = App(self.root/'state',models=self.models)
        with self.assertRaises(Refused):
            await self.run_mission()
        self.assertEqual(self.job()['tool_count'],1)
        self.assertEqual(len(json.loads(self.ledger.read_text())),1)
        self.assertIsNone(Browser(self.app).completed_at(self.job()))

    async def test_failed_expected_outcome_keeps_effects_without_signoff(self):
        await self.prepare(expected='Different title')
        await self.run_mission()
        self.assertEqual(self.job()['mission_state'],'NEEDS_INPUT')
        self.assertEqual(self.job()['tool_count'],2)
        self.assertIn('does not match',self.job()['reason'])
        self.assertIsNone(self.job().get('mission_result_id'))
        with self.assertRaises(Refused):
            await self.app.dispatch('mission-signoff',self.jid,exact_hash='anything',note='Try to force completion')
        self.assertEqual(json.loads(self.ledger.read_text()),[{'title':'Alpha'}])

    async def test_pause_ack_then_boundary_and_status_during_agent_inference(self):
        await self.prepare()
        self.models.release.clear()
        await self.app.dispatch('mission-run',self.jid)
        await asyncio.wait_for(self.models.entered.wait(),3)
        started = time.perf_counter()
        await self.app.dispatch('status')
        self.assertLess(time.perf_counter()-started,.1)
        await self.app.dispatch('pause',self.jid)
        self.assertNotEqual(self.job()['execution_state'],'PAUSED')
        self.models.release.set()
        async with asyncio.timeout(3):
            while self.job()['execution_state']!='PAUSED':
                await asyncio.sleep(.01)
        self.assertFalse(self.ledger.exists())
        await self.app.dispatch('resume',self.jid)
        await asyncio.wait_for(self.app.execution.task,10)
        self.assertEqual(self.job()['mission_state'],'AWAITING_SIGNOFF',self.job()['reason'])

    async def test_changed_dates_invalidate_authority_and_completion(self):
        await self.prepare()
        await self.app.dispatch('return',self.jid,note='Schedule no longer applies')
        with self.assertRaises(Refused):
            await self.app.dispatch('mission-run',self.jid)
        self.assertFalse(self.ledger.exists())
        self.assertIsNone(Browser(self.app).completed_at(self.job()))

    async def test_clarification_keeps_objective_and_original_budget(self):
        await self.prepare(questions=['What exact title should be used?'])
        original = self.plan
        self.models.next_plan = {**copy.deepcopy(self.proposal),'questions':[]}
        await self.app.dispatch('mission-answer',self.jid,text='Use exactly Alpha, as in the original request.')
        await self.app.execution.task
        revised = self.app.execution.record(self.job(),'mission_plan_id')
        self.assertNotEqual(revised['id'],original['id'])
        self.assertEqual(revised['objective'],original['objective'])
        answer = self.app.db.record('artifacts',revised['clarification_ref'])
        self.assertEqual(answer['questions'],original['questions'])
        self.assertEqual(self.job()['call_count'],1)
        self.assertEqual(self.job()['execution_state'],'MISSION_PLAN_READY')
        self.assertIsNone(self.job().get('mission_authorization_id'))
        self.assertFalse(self.ledger.exists())
        await self.app.close()
        self.app = App(self.root/'state',models=self.models)
        self.assertEqual(self.job()['execution_state'],'MISSION_PLAN_READY')
        self.assertEqual(self.job()['mission_state'],'PLAN_READY')

    async def test_original_agent_model_and_tool_budgets_block_authorization(self):
        for field,value in [('agent_count',2),('call_count',11),('tool_count',23)]:
            with self.subTest(field=field):
                ticket = self.app.db.record('snapshots',self.job()['snapshot_id'])['ticket']
                self.jid = self.app.db.add({**ticket,'key':'BUDGET-'+field.upper()},fixture=True)['id']
                await self.prepare(authorize=False)
                self.app.execution.save(self.jid,'test.budget',{field:value},'Previously consumed fixture budget')
                with self.assertRaises(Refused):
                    await self.app.dispatch('mission-authorize',self.jid,exact_hash=self.plan['plan_hash'],note='Reviewed fixture')
                self.assertFalse(self.ledger.exists())
                self.assertIsNone(self.job().get('mission_authorization_id'))
                with self.assertRaises(Refused):
                    self.app.execution.save(self.jid,'test.reset',{field:value-1},'Attempted budget reset')

    async def test_pending_agent_cannot_overwrite_an_operator_tool_proposal(self):
        await self.prepare()
        self.models.release.clear()
        await self.app.dispatch('mission-run',self.jid)
        await asyncio.wait_for(self.models.entered.wait(),3)
        with self.assertRaises(Refused):
            self.app.connectors.tool_plan(self.job(),'fixture','list_tasks',{},self.app.command('tool',self.job()))
        self.assertFalse(self.ledger.exists())
        await self.app.dispatch('cancel',self.jid)
        await self.app.execution.task
        self.assertEqual(self.app.missions.workers,{})
        self.assertFalse(self.ledger.exists())

    async def test_real_prompt_mission_review_run_and_signoff(self):
        await self.prepare(authorize=False)
        browser = Browser(self.app)
        browser.pane,browser.job_id,browser.tab = 'detail',self.jid,7
        browser.slide(1)
        async def until(predicate):
            async with asyncio.timeout(10):
                while not predicate():
                    await asyncio.sleep(.01)
        with create_pipe_input() as pipe:
            session = PromptSession(input=pipe,output=DummyOutput(),reserve_space_for_menu=0)
            with patch('monkey.ui.Browser',return_value=browser):
                task = asyncio.create_task(repl(self.app,session=session))
                try:
                    await until(lambda:session.app.is_running)
                    pipe.send_text('\r')
                    await until(lambda:'Authorize the displayed' in ''.join(t[1] for t in to_formatted_text(session.message)))
                    pipe.send_text('\n')
                    await asyncio.sleep(.05)
                    self.assertIsNone(self.job().get('mission_authorization_id'))
                    pipe.send_text('Approve the exact fixture creation and verification\n')
                    await until(lambda:self.job()['mission_state']=='AUTHORIZED')
                    self.assertFalse(self.ledger.exists())
                    pipe.send_text('\r')
                    await until(lambda:self.job()['mission_state']=='AWAITING_SIGNOFF')
                    self.assertIsNone(Browser(self.app).completed_at(self.job()))
                    pipe.send_text('\x1b[D\x1b[C\r')
                    await until(lambda:'Review the exact observed mission' in ''.join(t[1] for t in to_formatted_text(session.message)))
                    pipe.send_text('Inspected all recorded private fixture calls and title check\n')
                    await until(lambda:self.job()['mission_state']=='COMPLETED')
                    self.assertIsNotNone(Browser(self.app).completed_at(self.job()))
                    pipe.send_text('/quit\n')
                    await asyncio.wait_for(task,3)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
