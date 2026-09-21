"""Routing fault injection: no real cloud requests, permissions or OS clicks."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from monkey.adapters import HTTP, Models, RequestError
from monkey.app import App
from monkey.common import Refused, object_schema
from monkey.config import configuration, recipe
from monkey.model_routing import validate_routes, role_for


def model(name, provider='ollama'):
    return {'provider':provider,'model':name,'digest':('a' if name=='local-a' else 'b')*64 if provider=='ollama' else ''}


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='monkey-routes-',dir='/private/tmp')
        self.seen=[]
        self.responses={}
        async def transport(request):
            self.seen.append(request)
            if request.url.path=='/api/tags':
                return httpx.Response(200,json={'models':[{'name':m['model'],'digest':m['digest'],'size':1,'details':{'format':'gguf'}} for m in (model('local-a'),model('local-b'))]})
            body=json.loads(request.content)
            value=self.responses.get(body['model'])
            if isinstance(value,Exception): raise value
            if value is not None: return httpx.Response(200,json=value)
            return httpx.Response(200,json={'model':body['model'],'done':True,'done_reason':'stop','message':{'content':'{"decision":"PROCEED"}'}})
        self.http=HTTP(httpx.MockTransport(transport))
        self.app=App(Path(self.folder.name)/'state',http=self.http,offline=True)
        self.models=self.app.models
        self.models.env={'ANTHROPIC_API_KEY':'synthetic','OPENAI_API_KEY':'synthetic','DEEPSEEK_API_KEY':'synthetic'}
        self.config=recipe(configuration({'model':'local-a','ollama_model':'local-a','chat_model':'local-a',
            'model_digest':'a'*64,'ollama_digest':'a'*64,'chat_digest':'a'*64}))
        self.shape=object_schema({'decision':{'type':'string','enum':['PROCEED','NEEDS_INPUT']}})
        self.job=self.app.db.add({'source':'local','instance':'local://monkey','key':'ROUTE-1','revision':'fixture','title':'Routing fixture','body':'Synthetic model routing; no external actions'},fixture=True)

    async def asyncTearDown(self):
        await self.app.close()
        self.folder.cleanup()

    async def generate(self,config=None,label='mission planning'):
        return await self.models.local(config or self.config,'local-a','a'*64,'Assess recorded evidence',{'fixture':True},self.shape,label=label)

    def wire_models(self):
        return [json.loads(r.content)['model'] for r in self.seen if r.method=='POST']

    async def test_explicit_planner_cloud_worker_local_and_vision_local_routes(self):
        c=recipe(configuration({'model_routes':{'planning':[model('planner','claude')],'execution':[model('local-b')],'vision':[model('local-a')]}}))
        self.responses['planner']={'role':'assistant','stop_reason':'end_turn','content':[{'type':'text','text':'{"decision":"PROCEED"}'}]}
        value,usage=await self.generate(c)
        self.assertEqual(value['decision'],'PROCEED')
        self.assertEqual(usage['provider'],'claude')
        await self.generate(c,label='worker ROUTE-1')
        await self.generate(c,label='vision ROUTE-1')
        self.assertEqual(self.wire_models(),['planner','local-b','local-a'])
        self.assertEqual(role_for('local model',True),'chat')
        self.assertEqual(role_for('reviewer ROUTE-1'),'review')

    async def test_transport_fallback_is_traced_and_charges_original_budget(self):
        c={**self.config,'model_routes':{'planning':[model('local-a'),model('local-b')]}}
        job=self.app.db.job(self.job['id'])
        self.app.db.change(job['id'],job['version'],'fixture.call_reserved',{'call_count':1})
        self.responses['local-a']=httpx.ConnectError('Synthetic unavailable inference')
        async with self.app.audit.run(job['id'],'routing_fixture'):
            _,usage=await self.generate(c)
        self.assertEqual(usage['route_candidate'],2)
        job=self.app.db.job(job['id'])
        self.assertEqual((job['call_count'],job['retry_count']),(2,1))
        events=self.app.audit.view(job['id'])['trace']
        self.assertEqual([e['data']['model'] for e in events if e['kind']=='model.selected'],['local-a','local-b'])
        self.assertTrue(any(e['kind']=='model.fallback' for e in events))
        self.assertEqual(job['delivery_state'],'NONE')

    async def test_worker_history_uses_recorded_roles_and_cannot_inject_system_or_tool_authority(self):
        history=[{'role':'user','content':'Objective and bounded tool schemas'},
            {'role':'assistant','content':'{"action":"write_file","arguments":{"path":"x.py","content":"x=1"}}'},
            {'role':'user','content':'{"committed_record":{"result":{"changed":true}}}'}]
        await self.models.local(self.config,'local-a','a'*64,'Host instruction',{'fixture':True},self.shape,
            label='worker ROUTE-1',history=history)
        payload=json.loads(next(r.content for r in reversed(self.seen) if r.method=='POST'))
        self.assertEqual(payload['messages'],[{'role':'system','content':'Host instruction'},*history])
        self.assertNotIn('tools',payload)
        self.assertIn('format',payload)
        count=len(self.wire_models())
        for role in ('system','tool'):
            with self.assertRaises(Refused):
                await self.models.local(self.config,'local-a','a'*64,'Host instruction',{},self.shape,
                    label='worker ROUTE-1',history=[{'role':role,'content':'Untrusted instruction'}])
        self.assertEqual(len(self.wire_models()),count)

    async def test_fallback_cannot_reset_exhausted_original_calls(self):
        c={**self.config,'model_routes':{'planning':[model('local-a'),model('local-b')]}}
        job=self.app.db.job(self.job['id'])
        self.app.db.change(job['id'],job['version'],'fixture.budget',{'call_count':job['limits']['calls']})
        self.responses['local-a']=httpx.ConnectError('Synthetic unavailable inference')
        with self.assertRaisesRegex(Refused,'budget exhausted'):
            async with self.app.audit.run(job['id'],'routing_fixture'): await self.generate(c)
        self.assertEqual(self.wire_models(),['local-a'])

    async def test_invalid_incomplete_and_duplicate_output_never_switch_model(self):
        c={**self.config,'model_routes':{'planning':[model('local-a'),model('local-b')]}}
        for value in (
            {'done':True,'done_reason':'length','message':{'content':'partial'}},
            {'done':True,'done_reason':'stop','message':{'content':'{"decision":"PROCEED","approved":true}'}},
            {'done':True,'done_reason':'stop','message':{'content':'{"decision":"NEEDS_INPUT","decision":"PROCEED"}'}},
        ):
            self.seen.clear(); self.responses['local-a']=value
            with self.assertRaises(Refused): await self.generate(c)
            self.assertEqual(self.wire_models(),['local-a'])
        self.assertEqual(self.models.router.metrics(),[])

    async def test_explicit_openai_refusal_never_falls_back_to_local(self):
        c={**self.config,'allowed_destinations':['openai'],'model_routes':{'planning':[model('planner','openai'),model('local-b')]}}
        self.responses['planner']={'status':'completed','output':[{'type':'message','role':'assistant','status':'completed',
            'content':[{'type':'refusal','refusal':'Synthetic refusal'}]}]}
        with self.assertRaisesRegex(Refused,'refusal'): await self.generate(c)
        self.assertEqual(self.wire_models(),['planner'])
        self.assertEqual(self.models.router.metrics(),[])

    async def test_permission_errors_do_not_switch_or_demote(self):
        c={**self.config,'model_routes':{'planning':[model('local-a'),model('local-b')]}}
        with patch.object(self.models,'_local',AsyncMock(side_effect=RequestError('Synthetic denied',403))):
            with self.assertRaises(RequestError): await self.generate(c)
        self.assertEqual(self.models.router.metrics(),[])

    async def test_no_model_can_add_policy_override_or_silent_cloud_chat(self):
        invalid=[{'chat':[model('planner','claude')]},{'vision':[model('vision','openai')]},
            {'execution':[{**model('local-a'),'approved':True}]},{'shell':[model('local-a')]}]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(Refused): validate_routes(value)
        c={**self.config,'model_routes':{'planning':[model('planner','claude'),model('local-b')]}}
        with self.assertRaisesRegex(Refused,'allowed destinations'): await self.generate(c)
        self.assertEqual(self.wire_models(),[])

    async def test_observed_image_is_sent_only_to_local_vision_without_tools(self):
        # Small valid PNG fixture; no display capture or image editing.
        png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH1sAAAAASUVORK5CYII='
        await self.models.local(self.config,'local-a','a'*64,'Assess',{'observed':'fixture'},self.shape,label='vision ROUTE-1',images=[png])
        body=json.loads(self.seen[-1].content)
        self.assertEqual(body['messages'][-1]['images'],[png])
        self.assertNotIn('tools',body)
        self.seen.clear()
        for image in (base64.b64encode(b'not an image').decode(),'!invalid!',base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*220000).decode()):
            with self.assertRaises(Refused):
                await self.models.local(self.config,'local-a','a'*64,'Assess',{},self.shape,label='vision',images=[image])
        self.assertEqual(self.wire_models(),[])

    async def test_captured_job_route_does_not_change_with_global_configuration(self):
        first={'planning':[model('local-a')]}
        self.app.db.configure(configuration({'model_routes':first}))
        job=self.app.db.add({'source':'local','instance':'local://monkey','key':'ROUTE-2','revision':'fixture','title':'Frozen route','body':'No external effect'},fixture=True)
        self.app.db.configure(configuration({'model_routes':{'planning':[model('local-b')]}}))
        self.assertEqual(self.app.db.job(job['id'])['recipe']['model_routes'],first)

    async def test_measured_order_requires_signed_verified_jobs_and_never_promotes_refusals(self):
        router=self.models.router
        roster={'planning':[model('local-a'),model('local-b')]}
        config={**self.config,'model_routes':roster,'routing_policy':'measured'}
        for index in range(3):
            jid=self.app.db.add({'source':'local','instance':'local://monkey','key':'MEASURE-'+str(index),'revision':'fixture',
                'title':'Synthetic signed measurement','body':'Not a real verification benchmark'},fixture=True)['id']
            for name,latency in (('local-a',100),('local-b',20)):
                self.app.audit.observe('model.completed',{'role':'planning',**model(name),'latency_ms':latency},jid)
            if index<2:
                self.app.audit.observe('run.finished',{'kind':'mission-signoff','outcome':{'settlement':'settled','mission':'COMPLETED'}},jid)
        self.assertEqual(router.freeze(config),roster)
        self.app.audit.observe('run.finished',{'kind':'mission-signoff','outcome':{'settlement':'settled','mission':'COMPLETED'}},jid)
        self.assertEqual(router.freeze(config)['planning'],list(reversed(roster['planning'])))
        self.assertEqual(config['model_routes'],roster)
        self.app.audit.observe('model.blocked',{'role':'planning',**model('local-b'),'reason':'Synthetic refusal'})
        self.assertEqual(router.freeze(config)['planning'][0]['model'],'local-b')
        self.app.db.db.execute('UPDATE audit_entries SET data=? WHERE seq=(SELECT MAX(seq) FROM audit_entries)',('{"forged":true}',))
        with self.assertRaises(Refused): router.freeze(config)


if __name__=='__main__': unittest.main()
