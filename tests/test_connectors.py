import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import sys
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from monkey import captain
from monkey.app import App
from monkey.common import Refused
from monkey.api_tools import from_openapi
from monkey.sml import stop
from monkey.browser import Browser
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.formatted_text import to_formatted_text
from monkey.ui import repl
from monkey.cli import line


@unittest.skipUnless(Path('/Users/lukekist/bin/kist-current').is_file() and Path('/Volumes/PRO-G40/kist-loops').is_dir(), 'Local Kist is required for actual MCP/SML integration')
class ConnectorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.buildroot = tempfile.TemporaryDirectory(prefix='monkey-connector-captain-',dir='/private/tmp')
        cls.built = asyncio.run(captain.build(cls.buildroot.name,'/Volumes/PRO-G40/kist-loops'))

    @classmethod
    def tearDownClass(cls):
        cls.buildroot.cleanup()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='monkey-mcp-test-',dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.ledger = self.root/'ledger.json'
        self.config = self.root/'config.json'
        self.config.write_text(json.dumps({'command':sys.executable,'args':[str(Path(__file__).with_name('mcp_fixture_server.py')),str(self.ledger)],'sandbox':{'write':[str(self.ledger)]}}))
        self.app = App(self.root/'state')
        self.app.db.configure({'kist_binary':'/Users/lukekist/bin/kist-current','kist_source':'/Volumes/PRO-G40/kist-loops','admission_backend':'kist'})
        self.patch = patch('monkey.connectors.captain.build',AsyncMock(return_value=self.built))
        self.patch.start()
        self.jid = self.app.db.add({'source':'local','instance':'local://monkey','key':'REQ-101','revision':'fixture','title':'Create an integration test task','body':'A local fixture only.'},fixture=True)['id']
        self.connection = await self.app.dispatch('connect',name='fixture',path=str(self.config))

    async def asyncTearDown(self):
        await self.app.close()
        self.patch.stop()
        self.temp.cleanup()

    async def plan(self, args):
        path = self.root/'arguments.json'
        path.write_text(json.dumps(args))
        return (await self.app.dispatch('tool',self.jid,server='fixture',name='add_task',args_file=str(path)))['tool_plan']

    async def test_discover_schema_approve_call_and_recall(self):
        self.assertEqual({t['name'] for t in self.connection['tools']},{'lookup_ticket','add_task','list_tasks'})
        self.assertFalse(self.ledger.exists())
        with self.assertRaises(Refused):
            await self.plan({'title':123})
        plan = await self.plan({'title':'A real local task'})
        self.assertFalse(self.ledger.exists())
        await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Approve this exact private fixture write')
        await self.app.execution.task
        job = self.app.db.job(self.jid)
        self.assertEqual(job['tool_delivery'],'RETURNED_UNVERIFIED',job['reason'])
        self.assertEqual(json.loads(self.ledger.read_text()),[{'title':'A real local task'}])
        self.assertIsNone(job.get('execution_signoff_id'))
        records = self.app.connectors.view(job)['records']
        self.assertTrue(any(r['kind']=='mcp.raw_result' for r in records))
        self.assertTrue(any(r['kind']=='mcp.call' and r['evidence']['runtime']=='kist-durable-sml' for r in records))
        await self.app.dispatch('remember',text='Fixture task used the exact MCP schema')
        self.assertTrue((await self.app.dispatch('recall',text='fixture'))['records'])
        await self.app.close()
        self.app = App(self.root/'state')
        self.assertTrue((await self.app.dispatch('recall',text='fixture'))['records'])

    async def test_response_loss_is_unknown_and_never_resent(self):
        plan = await self.plan({'title':'Once only','lose_response':True})
        await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Approve the explicit response-loss fixture')
        await self.app.execution.task
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'UNKNOWN')
        self.assertEqual(len(json.loads(self.ledger.read_text())),1)
        await self.app.close()
        self.app = App(self.root/'state')
        await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Attempt old request again')
        await self.app.execution.task
        self.assertEqual(len(json.loads(self.ledger.read_text())),1)
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'UNKNOWN')

    async def lookup_ledger(self, jid):
        plan = self.app.connectors.tool_plan(self.app.db.job(jid),'fixture','list_tasks',{},self.app.command('tool'))['tool_plan']
        await self.app.dispatch('tool-run',jid,exact_hash=plan['plan_hash'],note='Read back the private fixture ledger')
        await self.app.execution.task
        self.assertEqual(self.app.db.job(jid)['tool_delivery'],'RETURNED_UNVERIFIED',self.app.db.job(jid)['reason'])
        return self.app.db.job(jid)['tool_call_id']

    async def test_exact_readback_signoff_and_uncertain_resolution(self):
        plan = await self.plan({'title':'Signed fixture'})
        await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Exact fixture action')
        await self.app.execution.task
        mutation = self.app.db.job(self.jid)['tool_call_id']
        verification = await self.lookup_ledger(self.jid)
        self.assertIsNone(Browser(self.app).completed_at(self.app.db.job(self.jid)))
        with self.assertRaises(Refused):
            await self.app.dispatch('tool-signoff',self.jid,call_id=mutation,verification_id=verification,pointer='/parsedContent/0/tasks/0/title',expected='"Wrong title"',note='Should fail')
        await self.app.dispatch('tool-signoff',self.jid,call_id=mutation,verification_id=verification,pointer='/parsedContent/0/tasks/0/title',expected='"Signed fixture"',note='Inspected the exact persisted task in a separate read-back call')
        self.assertIsNotNone(Browser(self.app).completed_at(self.app.db.job(self.jid)))
        plan = await self.plan({'title':'Lost fixture','lose_response':True})
        await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Response loss fixture')
        await self.app.execution.task
        lost = self.app.db.job(self.jid)['tool_call_id']
        inspection = self.app.db.add({'source':'local','instance':'local://monkey','key':'CHECK-1','revision':'fixture','title':'Inspect uncertain task','body':'Read the fixture ledger'},fixture=True)['id']
        verification = await self.lookup_ledger(inspection)
        await self.app.dispatch('tool-resolve',self.jid,call_id=lost,verification_id=verification,pointer='/parsedContent/0/tasks/1/title',expected='"Lost fixture"',note='Separate approved inspection confirms this exact task exists')
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'RESOLVED_WITH_EVIDENCE')
        self.assertEqual(len(json.loads(self.ledger.read_text())),2)
        self.assertIsNone(Browser(self.app).completed_at(self.app.db.job(self.jid)))

    async def test_real_prompt_tool_review_requires_note_and_keeps_input_live(self):
        await self.plan({'title':'UI approved fixture'})
        browser = Browser(self.app)
        browser.pane,browser.job_id,browser.tab = 'detail',self.jid,6
        browser.slide(1)
        async def until(predicate):
            async with asyncio.timeout(8):
                while not predicate():
                    await asyncio.sleep(.01)
        with create_pipe_input() as pipe:
            session = PromptSession(input=pipe,output=DummyOutput(),reserve_space_for_menu=0)
            with patch('monkey.ui.Browser',return_value=browser):
                task = asyncio.create_task(repl(self.app,session=session))
                try:
                    await until(lambda:session.app.is_running)
                    pipe.send_text('\r')
                    await until(lambda:'Approve the displayed' in ''.join(t[1] for t in to_formatted_text(session.message)))
                    pipe.send_text('\n')
                    await asyncio.sleep(.06)
                    self.assertFalse(self.ledger.exists())
                    pipe.send_text('Checked the exact private fixture action\n')
                    await until(lambda:self.app.execution.active is not None)
                    pipe.send_text('/status\n')
                    await until(lambda:self.app.db.job(self.jid).get('tool_delivery')=='RETURNED_UNVERIFIED')
                    self.assertEqual(json.loads(self.ledger.read_text()),[{'title':'UI approved fixture'}])
                    pipe.send_text('/quit\n')
                    await asyncio.wait_for(task,3)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
    async def test_catalog_change_invalidates_tool_request(self):
        plan = await self.plan({'title':'Old plan'})
        await self.app.dispatch('connect',name='fixture',path=str(self.config))
        await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Try stale catalog')
        await self.app.execution.task
        self.assertFalse(self.ledger.exists())
        self.assertIn('catalog changed',self.app.db.job(self.jid)['reason'])

    async def test_reviewed_runtime_recipe_survives_global_setting_change(self):
        plan = await self.plan({'title':'Pinned runtime fixture'})
        self.app.db.configure({'kist_binary':'/bin/false','kist_source':'/private/tmp/no-captain-source'})
        await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Use the runtime recipe captured in the inspected request')
        await self.app.execution.task
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'RETURNED_UNVERIFIED',self.app.db.job(self.jid)['reason'])
        self.assertEqual(json.loads(self.ledger.read_text()),[{'title':'Pinned runtime fixture'}])

    async def test_connected_language_captures_task_and_only_proposes(self):
        proposed = {'server':'fixture','tool':'lookup_ticket','arguments_json':'{"key":"REQ-101"}','questions':[],'reason':'Explicit connection and identifier'}
        with patch.object(self.app,'capture_config',AsyncMock()),patch.object(self.app.models,'local',AsyncMock(return_value=(proposed,{'fixture':True}))):
            result = await line(self.app,'Look up REQ-101 in fixture')
        jid = result['captured_task']['job_id']
        self.assertEqual(result['tool_plan']['arguments'],{'key':'REQ-101'})
        self.assertEqual(self.app.db.job(jid)['work_state'],'WAITING_USER')
        self.assertEqual(self.app.db.job(jid)['call_count'],1)
        self.assertIsNone(self.app.worker.active)
        self.assertFalse(self.ledger.exists())

    async def test_evolution_reports_actual_native_blocker(self):
        result = await self.app.dispatch('improve',self.jid,text='Improve task routing after measured failures')
        self.assertFalse(result['promoted'])
        self.assertIsNone(result['capability_delta'])
        self.assertEqual(result['kist_readiness']['execution'],'blocked')
        self.assertIn('hardProcessContainment',result['kist_readiness']['report'])

    async def test_both_mcp_network_transports_call_real_server(self):
        for transport in ('streamable-http','sse'):
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0))
                port = reservation.getsockname()[1]
            process = await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).with_name('mcp_fixture_server.py')),
                str(self.ledger),transport,str(port),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
            try:
                for _ in range(100):
                    try:
                        reader,writer = await asyncio.open_connection('127.0.0.1',port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(.03)
                cfg = {'transport':transport,'url':'http://127.0.0.1:'+str(port)+('/mcp' if transport=='streamable-http' else '/sse')}
                self.config.write_text(json.dumps(cfg))
                await self.app.dispatch('connect',name='network',path=str(self.config))
                plan = self.app.connectors.tool_plan(self.app.db.job(self.jid),'network','lookup_ticket',{'key':'REQ-101'},self.app.command('tool'))['tool_plan']
                await self.app.dispatch('tool-run',self.jid,exact_hash=plan['plan_hash'],note='Read the explicit local '+transport+' fixture')
                await self.app.execution.task
                self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'RETURNED_UNVERIFIED',self.app.db.job(self.jid)['reason'])
            finally:
                await stop(process)
            self.assertIsNotNone(process.returncode)

    async def test_openapi_exact_route_response_loss_and_redirect(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
                requests.append((self.path,body,self.headers.get('Authorization')))
                if body['title']=='lost':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if body['title']=='redirect':
                    self.send_response(307)
                    self.send_header('Location','/should-never-be-called')
                    self.end_headers()
                    return
                self.send_response(201)
                self.end_headers()
                self.wfile.write(json.dumps({'id':'actual-local-'+str(len(requests))}).encode())
            def log_message(self,*_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            document = {'openapi':'3.1.0','paths':{'/projects/{project}/tasks':{'post':{'operationId':'create_task',
                'parameters':[{'in':'path','name':'project','required':True,'schema':{'type':'string'}}],
                'requestBody':{'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/Task'}}}}}}},
                'components':{'schemas':{'Task':{'type':'object','properties':{'title':{'type':'string'}},'required':['title'],'additionalProperties':False}}}}
            spec = self.root/'openapi.json'
            spec.write_text(json.dumps(document))
            self.config.write_text(json.dumps({'transport':'api','base_url':'http://127.0.0.1:'+str(server.server_port)+'/v1',
                'headers':{'Authorization':'Bearer fixture-only'},'openapi':{'file':str(spec),'operations':['create_task']}}))
            connection = await self.app.dispatch('connect',name='api-fixture',path=str(self.config))
            self.assertNotIn('fixture-only',json.dumps(connection))
            self.assertEqual(requests,[])
            with self.assertRaises(Refused):
                self.app.connectors.tool_plan(self.app.db.job(self.jid),'api-fixture','create_task',{'path':{'project':'APP'},'body':{'title':12}},self.app.command('tool'))
            for title in ('success','lost','redirect'):
                jid = self.app.db.add({'source':'local','instance':'local://monkey','key':'API-'+str(len(requests)+1),'revision':'fixture','title':title,'body':'Private API fixture'},fixture=True)['id']
                plan = self.app.connectors.tool_plan(self.app.db.job(jid),'api-fixture','create_task',{'path':{'project':'space project'},'body':{'title':title}},self.app.command('tool'))['tool_plan']
                self.assertEqual(plan['route']['operation']['method'],'POST')
                await self.app.dispatch('tool-run',jid,exact_hash=plan['plan_hash'],note='Approve one private '+title+' fixture action')
                await self.app.execution.task
                self.assertEqual(self.app.db.job(jid)['tool_delivery'],'RETURNED_UNVERIFIED' if title=='success' else 'UNKNOWN',self.app.db.job(jid)['reason'])
            self.assertEqual(len(requests),3)
            self.assertEqual(requests[0],('/v1/projects/space%20project/tasks',{'title':'success'},'Bearer fixture-only'))
            document['components']['schemas']['Task'] = {'$ref':'https://should-never-be-fetched.invalid/schema'}
            with self.assertRaises(Refused):
                from_openapi(document,'https://example.invalid',['create_task'])
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())


if __name__=='__main__':
    unittest.main()
