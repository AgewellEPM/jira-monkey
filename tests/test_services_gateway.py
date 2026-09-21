"""Owned service setup, credential privacy, and a real loopback MCP/API server."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import datetime as dt
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import httpx
from jsonschema import validators
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from monkey.app import App
from monkey import captain
from monkey.api_tools import APIClient
from monkey.browser import Browser
from monkey.common import Refused
from monkey.connectors import schema_check
from monkey.services import native_config
from monkey.ui import History,repl


class Output(DummyOutput):
    def __init__(self):
        self.fragments=[]
    def write(self,text):
        self.fragments.append(text)
    def write_raw(self,text):
        self.fragments.append(text)


class OwnedServices(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='monkey-owned-services-',dir='/private/tmp')
        self.root=Path(self.folder.name)
        self.app=App(self.root/'state')
        self.app.capture_config=AsyncMock()
        self.app.doctor=AsyncMock(return_value={})

    async def asyncTearDown(self):
        await self.app.close()
        self.folder.cleanup()

    async def until(self,condition):
        async with asyncio.timeout(6):
            while not condition():
                await asyncio.sleep(.01)

    async def test_all_native_adapters_created_without_files_or_network(self):
        token='synthetic-secret-never-in-history'
        with patch('httpx.AsyncClient.send',side_effect=AssertionError('Setup must not contact a business account')):
            for service in ('asana','teams','monday','salesforce','quickbooks'):
                answer=await self.app.dispatch('service-save',service=service,secret=token,
                    endpoint='https://fixture.my.salesforce.com',company_id='123')
                self.assertFalse(answer['access_verified'])
                self.assertGreaterEqual(len(answer['tools']),4)
                row=self.app.connectors.connections()[service]
                config=Path(row['config_path'])
                self.assertEqual(config.stat().st_mode&0o777,0o600)
                self.assertIn(token,config.read_text())
                for tool in answer['tools']:
                    validators.validator_for(tool['inputSchema']).check_schema(tool['inputSchema'])
        public=json.dumps({'catalog':await self.app.dispatch('tools'),'events':self.app.db.events(),
            'records':self.app.db.records('artifacts'),'commands':[json.loads(row[0]) for row in self.app.db.db.execute('SELECT data FROM commands')]})
        self.assertNotIn(token,public)
        self.assertEqual(len(self.app.connectors.connections()),5)
        await self.app.dispatch('disconnect',name='asana')
        self.assertNotIn('asana',self.app.connectors.connections())
        await self.app.close()
        self.app=App(self.root/'state')
        self.assertNotIn('asana',self.app.connectors.connections())
        self.assertEqual(len(self.app.connectors.connections()),4)

    async def test_native_routes_schema_and_http_error_semantics(self):
        cases=[('asana','create_task',{'body':{'data':{'name':'Reviewed','workspace':'123'}}},'/api/1.0/tasks'),
            ('teams','update_task',{'path':{'task_id':'task-1'},'headers':{'If-Match':'W/"known-version"'},'body':{'title':'Reviewed'}},'/v1.0/planner/tasks/task-1'),
            ('salesforce','create_task',{'body':{'Subject':'Reviewed'}},'/services/data/v64.0/sobjects/Task'),
            ('quickbooks','create_invoice',{'body':{'CustomerRef':{'value':'12'},'Line':[{'Amount':10,'DetailType':'SalesItemLineDetail','SalesItemLineDetail':{'ItemRef':{'value':'9'}}}]}},'/v3/company/123/invoice')]
        real_client=httpx.AsyncClient
        for service,name,args,path in cases:
            config=native_config(service,'opaque-token',{'endpoint':'https://fixture.my.salesforce.com','company_id':'123'})
            op=next(o for o in config['operations'] if o['name']==name)
            schema_check(args,op['inputSchema'])
            calls=[]
            def handle(request):
                calls.append(request)
                return httpx.Response(200,json={'id':'recorded'},headers={'ETag':'retained-version'})
            with patch('monkey.api_tools.httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
                response=(await APIClient(config).call_tool(name,args)).value
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0].url.path,path)
            self.assertEqual(calls[0].headers['Authorization'],'Bearer opaque-token')
            if service=='teams':
                self.assertEqual(calls[0].headers['If-Match'],'W/"known-version"')
            self.assertEqual(response['structuredContent']['response_headers']['etag'],'retained-version')
            self.assertFalse(response['isError'])
        config=native_config('monday','opaque-token',{})
        op=next(o for o in config['operations'] if o['name']=='create_item')
        args={'body':{'query':op['inputSchema']['properties']['body']['properties']['query']['const'],
            'variables':{'board':'123','group':'new_group','name':'Reviewed'}}}
        schema_check(args,op['inputSchema'])
        calls=[]
        def denied(request):
            calls.append(request)
            return httpx.Response(200,json={'errors':[{'message':'Fixture permission denial'}]})
        with patch('monkey.api_tools.httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(denied),**kw)):
            response=(await APIClient(config).call_tool('create_item',args)).value
        self.assertTrue(response['isError'])
        self.assertEqual(calls[0].headers['Authorization'],'opaque-token')
        self.assertEqual(calls[0].headers['API-Version'],'2026-07')
        args['body']['query']='mutation { delete_everything }'
        with self.assertRaises(Refused):
            schema_check(args,op['inputSchema'])
        with self.assertRaises(Refused):
            await APIClient(config).call_tool('get_user',{'headers':{'Authorization':'stolen'}})

    async def test_connections_menu_and_hidden_prompt_leave_no_secret_history(self):
        token='synthetic-token-plain-no-special-prefix'
        output=Output()
        history=History(str(self.root/'history'))
        browser=Browser(self.app)
        browser.section=4
        self.assertIn('Asana',''.join(t[1] for t in to_formatted_text(browser.render(''))))
        with create_pipe_input() as pipe:
            session=PromptSession(input=pipe,output=output,history=history,reserve_space_for_menu=0)
            with patch('monkey.ui.Browser',return_value=browser):
                task=asyncio.create_task(repl(self.app,session=session))
                try:
                    await self.until(lambda:session.app.is_running)
                    pipe.send_text('\r')
                    await self.until(lambda:browser.pane=='services')
                    pipe.send_text('\r')
                    await self.until(lambda:'access token' in ''.join(t[1] for t in to_formatted_text(session.message)))
                    self.assertTrue(session.is_password)
                    pipe.send_text(token+'\n')
                    await self.until(lambda:'asana' in self.app.connectors.connections())
                    pipe.send_text('/status\n')
                    await asyncio.sleep(.05)
                    pipe.send_text('/quit\n')
                    await asyncio.wait_for(task,3)
                finally:
                    if not task.done(): task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
        self.assertNotIn(token,'\n'.join(session.history.get_strings()))
        self.assertNotIn(token,(self.root/'history').read_text())
        self.assertNotIn(token,''.join(output.fragments))
        self.assertNotIn(token,json.dumps(self.app.db.events()))

    async def test_real_mcp_and_http_share_state_without_approval_authority(self):
        started=await self.app.dispatch('api',action='start')
        path=Path(started['client_config'])
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        config=json.loads(path.read_text())
        token=config['api']['headers']['Authorization']
        async with httpx.AsyncClient(trust_env=False) as http:
            status_url=started['api_url']+'/status'
            self.assertEqual((await http.get(status_url)).status_code,401)
            self.assertEqual((await http.get(status_url,headers={'Authorization':token,'Origin':'http://evil.example'})).status_code,403)
            self.assertEqual((await http.get(status_url,headers={'Authorization':token,'Host':'evil.example'})).status_code,403)
            self.assertEqual((await http.get(status_url,headers={'Authorization':token})).json()['worker'],'idle')
            refused=await http.post(started['api_url']+'/rpc',headers={'Authorization':token},json={'operation':'mission-authorize','arguments':{'approved':True}})
            self.assertEqual(refused.status_code,400)
            refused=await http.post(started['api_url']+'/rpc',headers={'Authorization':token},content=b'x'*66000)
            self.assertEqual(refused.status_code,400)
        async with self.app.connectors.client(config['mcpServers']['monkey']) as client:
            names={t['name'] for t in await self.app.connectors.catalog(client)}
            self.assertIn('monkey_task',names)
            self.assertIn('monkey_propose_tool',names)
            self.assertIn('monkey_remember',names)
            self.assertFalse(any('approve' in name or 'authorize' in name for name in names))
            result=await client.call_tool('monkey_task',{'text':'A private gateway task'})
            raw=result.model_dump(mode='json',by_alias=True,exclude_none=True)
            self.assertFalse(raw.get('isError'),raw)
            self.assertEqual(len(self.app.db.jobs()),1)
            job=self.app.db.jobs()[0]
            self.assertEqual(job['work_state'],'WAITING_USER')
            called=await client.call_tool('monkey_events',{'job_id':job['id']})
            self.assertFalse(called.is_error,called)
            refused=await client.call_tool('monkey_run_mission',{'job_id':job['id']})
            self.assertTrue(refused.is_error)
            remembered=await client.call_tool('monkey_remember',{'text':'Gateway fixture note: retain the exact source before acting.'})
            self.assertFalse(remembered.is_error)
            memories=[r for r in self.app.db.records('artifacts') if r.get('kind')=='memory.note']
            self.assertEqual(memories[-1]['origin'],'monkey_api_client')
        commands=[json.loads(row[0]) for row in self.app.db.db.execute('SELECT data FROM commands')]
        self.assertTrue(any(r.get('command',{}).get('origin')=='monkey_api_client' for r in commands))
        port=self.app.gateway.port
        with self.assertRaises(Refused):
            await self.app.dispatch('api',action='start')
        await self.app.dispatch('api',action='stop')
        self.assertFalse(path.exists())
        self.assertFalse(self.app.gateway.status()['running'])
        with self.assertRaises(OSError):
            await asyncio.open_connection('127.0.0.1',port)
        restarted=await self.app.dispatch('api',action='start',port=port)
        async with httpx.AsyncClient(trust_env=False) as http:
            self.assertEqual((await http.get(restarted['api_url']+'/status',headers={'Authorization':token})).status_code,401)

    async def test_owned_api_starts_only_the_exact_authorized_native_service_mission(self):
        ledger=[]
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(('POST',self.path))
                ledger.append({'gid':'known-fixture-id',**body['data']})
                self.reply({'data':ledger[0]})
            def do_GET(self):
                requests.append(('GET',self.path))
                self.reply({'data':ledger[0]})
            def reply(self,body):
                raw=json.dumps(body).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        thread.start()
        try:
            config=native_config('asana','synthetic-token',{})
            config['base_url']='http://127.0.0.1:'+str(http.server_port)+'/api/1.0'
            await self.app.connectors.connect_config('asana',config)
            jid=(await self.app.dispatch('task',text='Create one exact Asana-shaped private fixture task and verify it'))['job_id']
            clock=dt.datetime.now(dt.timezone.utc)
            await self.app.dispatch('schedule',jid,start=(clock-dt.timedelta(minutes=1)).isoformat(),finish=(clock+dt.timedelta(hours=1)).isoformat(),timezone='UTC')
            plan={'understanding':'Create one private fixture record, then read known-fixture-id to verify its title.',
                'questions':[],'steps':[
                    {'agent':'worker','purpose':'Create the reviewed fixture task','server':'asana','tool':'create_task',
                     'arguments_json':json.dumps({'body':{'data':{'name':'Owned adapter fixture','workspace':'123'}}})},
                    {'agent':'reviewer','purpose':'Read the fixture record from the service','server':'asana','tool':'get_task',
                     'arguments_json':json.dumps({'path':{'task_gid':'known-fixture-id'}})}],
                'checks':[{'step':2,'pointer':'/structuredContent/body/data/name','equals_json':'"Owned adapter fixture"'}]}
            await self.app.missions.plan(jid,'Create and verify the one specified private fixture record',plan)
            stored=self.app.execution.record(self.app.db.job(jid),'mission_plan_id')
            started=await self.app.dispatch('api',action='start')
            gateway=json.loads(Path(started['client_config']).read_text())
            async def assessment(c,model,pin,instruction,data,shape,**kw):
                return {'decision':'PROCEED','reason':'Exact fixture plan is consistent.','questions':[],
                    'evidence_refs':[data['evidence_refs'][0]]},{'fixture':True}
            self.app.models.local=assessment
            async with self.app.connectors.client(gateway['mcpServers']['monkey']) as client:
                denied=await client.call_tool('monkey_run_mission',{'job_id':jid})
                self.assertTrue(denied.is_error)
                self.assertEqual(ledger,[])
                await self.app.dispatch('mission-authorize',jid,exact_hash=stored['plan_hash'],note='Fixture harness reviewed each exact service action and read-back check')
                accepted=await client.call_tool('monkey_run_mission',{'job_id':jid})
                self.assertFalse(accepted.is_error)
                await self.app.execution.task
                self.assertEqual(self.app.db.job(jid)['mission_state'],'AWAITING_SIGNOFF',self.app.db.job(jid)['reason'])
                _,result=self.app.missions.verify(self.app.db.job(jid))
                self.assertEqual(len(result['calls']),2)
                self.assertEqual(requests,[('POST','/api/1.0/tasks'),('GET','/api/1.0/tasks/known-fixture-id')])
                self.assertEqual(len(ledger),1)
                self.assertEqual(self.app.gateway.status()['running'],True)
                self.assertEqual((await self.app.dispatch('status'))['worker'],'idle')
                replay=await client.call_tool('monkey_run_mission',{'job_id':jid})
                self.assertTrue(replay.is_error)
                self.assertEqual(len(ledger),1)
        finally:
            await asyncio.to_thread(http.shutdown)
            http.server_close()
            thread.join(timeout=2)
