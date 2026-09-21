"""Real loopback HTTP calls through Monkey's own admission; no Kist or model."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from monkey import admission
from monkey.app import App
from monkey.common import Refused
from monkey.services import native_config


class HostAdmission(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='monkey-admission-',dir=Path(tempfile.gettempdir()).resolve())
        self.addAsyncCleanup(self.cleanup)
        self.root=Path(self.temp.name)
        self.app=App(self.root/'state')
        self.ledger=[]
        self.lose=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))['data']
                owner.ledger.append({'gid':'fixture-1',**data})
                if owner.lose:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.reply({'data':owner.ledger[-1]})
            def do_GET(self):
                self.reply({'data':owner.ledger[-1] if owner.ledger else None})
            def reply(self,data):
                raw=json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.http.serve_forever)
        self.thread.start()
        config=native_config('asana','synthetic-token',{})
        config['base_url']='http://127.0.0.1:'+str(self.http.server_port)+'/api/1.0'
        await self.app.connectors.connect_config('fixture',config)
        self.kist=patch('monkey.connectors.SML.execute',AsyncMock(side_effect=AssertionError('Kist must not run')))
        self.captain=patch('monkey.captain.build',AsyncMock(side_effect=AssertionError('Captain must not build')))
        self.kist.start();self.captain.start()
        self.jid=self.job('NATIVE-1')

    async def cleanup(self):
        try:
            if hasattr(self,'app'):
                await self.app.close()
        finally:
            for name in ('kist','captain'):
                if hasattr(self,name): getattr(self,name).stop()
            if hasattr(self,'http'):
                await asyncio.to_thread(self.http.shutdown)
                self.http.server_close()
            if hasattr(self,'thread'):
                self.thread.join(timeout=5)
                self.assertFalse(self.thread.is_alive())
            self.temp.cleanup()

    def job(self,key):
        return self.app.db.add({'source':'local','instance':'local://monkey','key':key,'revision':'fixture',
            'title':'Private native admission check','body':'Create one private fixture record and verify its name.'},fixture=True)['id']

    def plan(self,jid,read=False):
        return self.app.connectors.tool_plan(self.app.db.job(jid),'fixture','get_task' if read else 'create_task',
            {'path':{'task_gid':'fixture-1'}} if read else {'body':{'data':{'name':'Native fixture','workspace':'123'}}},
            self.app.command('tool',self.app.db.job(jid)))['tool_plan']

    async def run_plan(self,jid,plan):
        await self.app.dispatch('tool-run',jid,exact_hash=plan['plan_hash'],note='Reviewed this private fixture request')
        await self.app.execution.task
        return self.app.db.job(jid)

    async def test_create_readback_signoff_signed_trace_without_kist(self):
        plan=self.plan(self.jid)
        self.assertEqual(plan['runtime']['kind'],admission.KIND)
        created=await self.run_plan(self.jid,plan)
        self.assertEqual(created['tool_delivery'],'RETURNED_UNVERIFIED',created['reason'])
        call_id=created['tool_call_id']
        checked=await self.run_plan(self.jid,self.plan(self.jid,True))
        await self.app.connectors.attest(checked,call_id,checked['tool_call_id'],
            '/structuredContent/body/data/name','"Native fixture"','Read back the actual fixture name')
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'SIGNED_OFF')
        self.assertEqual(len(self.ledger),1)
        trace=self.app.audit.view(self.jid)['trace']
        claimed=[r['seq'] for r in trace if r['kind']=='admission.claimed']
        returned=[r['seq'] for r in trace if r['kind']=='admission.response_retained']
        self.assertEqual(len(claimed),2)
        self.assertTrue(all(a<b for a,b in zip(claimed,returned)))
        self.assertFalse(any(r['kind']=='sml.requested' for r in trace))
        self.app.audit.verify()

    async def test_response_loss_is_consumed_and_never_resent_after_restart(self):
        self.lose=True
        plan=self.plan(self.jid)
        uncertain=await self.run_plan(self.jid,plan)
        self.assertEqual(uncertain['tool_delivery'],'UNKNOWN')
        self.assertEqual(len(self.ledger),1)
        original=uncertain['tool_call_id']
        await self.app.close()
        self.app=App(self.root/'state')
        again=await self.run_plan(self.jid,plan)
        self.assertEqual(again['tool_delivery'],'UNKNOWN')
        self.assertEqual(len(self.ledger),1)
        verification_job=self.job('NATIVE-VERIFY')
        verified=await self.run_plan(verification_job,self.plan(verification_job,True))
        await self.app.connectors.attest(self.app.db.job(self.jid),original,verified['tool_call_id'],
            '/structuredContent/body/data/name','"Native fixture"','Inspected the stored record after response loss',resolving=True)
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'RESOLVED_WITH_EVIDENCE')
        self.assertEqual(len(self.ledger),1)

    async def test_bad_approval_hash_cannot_reach_the_service(self):
        plan=self.plan(self.jid)
        await self.app.dispatch('tool-run',self.jid,exact_hash='0'*64,note='Incorrect fixture hash')
        await self.app.execution.task
        self.assertEqual(self.ledger,[])
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'PROPOSED')
        self.assertFalse((self.root/'state'/'executions').exists())

    async def test_upgrade_can_resolve_old_uncertainty_without_replaying_it(self):
        self.lose=True
        uncertain=await self.run_plan(self.jid,self.plan(self.jid))
        call_id=uncertain['tool_call_id']
        upgraded={**admission.LOADED_RECIPE,'sources':{**admission.LOADED_RECIPE['sources'],'future.py':'1'*64}}
        with patch.object(admission,'LOADED_RECIPE',upgraded),patch.object(admission,'recipe',return_value=upgraded):
            verify_job=self.job('UPGRADE-READ')
            verified=await self.run_plan(verify_job,self.plan(verify_job,True))
            result=await self.app.connectors.attest(self.app.db.job(self.jid),call_id,verified['tool_call_id'],
                '/structuredContent/body/data/name','"Native fixture"','Inspected service after upgrading Monkey',resolving=True)
            self.assertTrue(result['attestation']['admission']['runtime_changed'])
        self.assertEqual(len(self.ledger),1)
        self.assertEqual(self.app.db.job(self.jid)['tool_delivery'],'RESOLVED_WITH_EVIDENCE')

    async def test_changed_native_claim_breaks_evidence_verification(self):
        result=await self.run_plan(self.jid,self.plan(self.jid))
        call,_=self.app.connectors.observed(result['tool_call_id'])
        Path(call['evidence']['claim_path']).write_text('{"forged":true}')
        with self.assertRaises(Refused):
            self.app.connectors.observed(result['tool_call_id'])

    def test_every_required_admission_fact_is_enforced(self):
        facts={k:True for k in ('exact_plan','source_current','schema_current','operator_reviewed','original_budget','scope_current')}
        self.assertEqual(admission.judgment(facts)['verdict'],'green')
        for key in facts:
            self.assertEqual(admission.judgment({**facts,key:False})['failed_rules'],[key])
        with self.assertRaises(Refused):
            admission.judgment({**facts,'approved':True})
