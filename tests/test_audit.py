import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import sqlite3
import unittest
from unittest.mock import patch

import httpx

from monkey.app import App
from monkey.audit import verify_bundle, verify_file
from monkey.common import Refused, digest
from monkey.project_tools import ProjectFiles


class FixtureModels:
    fixture=True
    async def triage(self,*_):
        return {'goal':'Draft a request','criteria':['Ask for details'],'unknowns':[],'needs_human':False,'reason':'Synthetic fixture'},{}
    async def draft(self,*_): return 'Please provide the reproduction steps.',{}
    async def review(self,*_): return {'verdict':'PASS','findings':[],'unresolved_questions':[]},{}


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='monkey-signed-trace-',dir='/private/tmp')
        self.root=Path(self.folder.name)
        self.app=App(self.root/'state',offline=True,models=FixtureModels())
        self.job=self.app.db.add({'source':'local','instance':'local://monkey','key':'TRACE-1','revision':'fixture','title':'Trace fixture','body':'No external business actions'},fixture=True)
    async def asyncTearDown(self):
        await self.app.close()
        self.folder.cleanup()

    async def bundle(self):
        result=self.app.audit.export(self.job['id'])
        return json.loads(Path(result['path']).read_text()),result

    async def test_reads_writes_and_signed_run_capture_exact_hashes(self):
        project=self.root/'project';project.mkdir()
        file=project/'example.txt';file.write_text('before')
        async with self.app.audit.run(self.job['id'],'scoped_fixture'):
            files=ProjectFiles(project,audit=self.app.audit)
            before=files.read('example.txt')
            files.write('example.txt','after',before['sha256'])
        trace=self.app.audit.view(self.job['id'])
        kinds={r['kind'] for r in trace['trace']}
        self.assertTrue({'file.read.requested','file.read.completed','file.write.requested','file.write.completed','run.finished','run.sealed'} <= kinds)
        effect=next(r for r in trace['trace'] if r['kind']=='file.write.completed')
        self.assertEqual(effect['data']['after_sha256'],hashlib.sha256(file.read_bytes()).hexdigest())
        bundle,result=await self.bundle()
        self.assertTrue(verify_bundle(bundle,expected_fingerprint=result['signer_fingerprint'])['valid'])
        self.assertTrue(verify_file(result['path'],result['signer_fingerprint'])['valid'])
        self.assertEqual((self.app.audit.root/'signing-key.json').stat().st_mode & 0o777,0o600)

    async def test_worker_automatically_signs_a_completed_attempt(self):
        await self.app.worker.run(self.job['id'])
        self.assertEqual(self.app.db.job(self.job['id'])['work_state'],'DRAFT_READY')
        view=self.app.audit.view(self.job['id'])
        run=next(r for r in view['runs'] if r['kind']=='response_worker')
        self.assertEqual(run['outcome']['work'],'DRAFT_READY')
        self.assertEqual(run['outcome']['delivery'],'DRAFT')
        self.assertEqual(run['algorithm'],'Ed25519 + SHA-256')

    async def test_sealing_does_not_block_status_and_covers_an_exact_prefix(self):
        entered,release=threading.Event(),threading.Event()
        original=self.app.audit._verify_snapshot
        def delayed(snapshot):
            entered.set()
            if not release.wait(3):raise RuntimeError('Test did not release verification')
            return original(snapshot)
        async def work():
            async with self.app.audit.run(self.job['id'],'concurrent_fixture'):
                self.app.audit.observe('fixture.work',{'performed':True})
        with patch.object(self.app.audit,'_verify_snapshot',delayed):
            task=asyncio.create_task(work())
            try:
                await asyncio.wait_for(asyncio.to_thread(entered.wait),1)
                result=await asyncio.wait_for(self.app.dispatch('builder',action='status'),.1)
                self.assertFalse(result['configured'])
                self.app.audit.observe('fixture.concurrent_append',{'observed':True})
            finally:release.set()
            await asyncio.wait_for(task,3)
        proof=self.app.audit.view(self.job['id'])
        self.assertTrue(proof['valid'])
        seal=next(row for row in proof['runs'] if row['kind']=='concurrent_fixture')
        self.assertLess(seal['entry_count'],proof['entries'])
        self.assertTrue(any(row['kind']=='builder.status.read' for row in self.app.audit.entries()))

    async def test_async_verification_rejects_external_store_changes(self):
        entered,release=threading.Event(),threading.Event()
        original=self.app.audit._verify_snapshot
        def delayed(snapshot):
            entered.set()
            if not release.wait(3):raise RuntimeError('Test did not release verification')
            return original(snapshot)
        with patch.object(self.app.audit,'_verify_snapshot',delayed):
            task=asyncio.create_task(self.app.audit.verify_async())
            try:
                await asyncio.wait_for(asyncio.to_thread(entered.wait),1)
                with sqlite3.connect(self.app.db.root/'monkey.sqlite3') as other:
                    other.execute('DROP TRIGGER audit_jobs_update')
                    row=self.app.db.job(self.job['id']);row['reason']='out-of-band edit during verification'
                    other.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(row),row['id']))
            finally:release.set()
            with self.assertRaisesRegex(Refused,'external writer'):await task
        self.assertTrue(self.app.db.failed)

    async def test_edit_delete_reorder_and_rehash_do_not_forge_signatures(self):
        bundle,result=await self.bundle()
        variations=[]
        changed=copy.deepcopy(bundle);changed['entries'][0]['data']['coverage']='Forged complete coverage';variations.append(changed)
        changed=copy.deepcopy(bundle);changed['entries'].pop();variations.append(changed)
        changed=copy.deepcopy(bundle);changed['entries'][0],changed['entries'][1]=changed['entries'][1],changed['entries'][0];variations.append(changed)
        changed=copy.deepcopy(bundle);changed['seal']['outcome']='everything fixed';variations.append(changed)
        changed=copy.deepcopy(bundle);row=changed['entries'][0];row['data']['coverage']='Forged';row['hash']=digest({k:v for k,v in row.items() if k not in {'hash','signature'}});variations.append(changed)
        for changed in variations:
            with self.assertRaises(Refused): verify_bundle(changed,expected_fingerprint=result['signer_fingerprint'])
        with self.assertRaises(Refused): verify_bundle(bundle,expected_fingerprint='0'*64)

    async def test_store_tampering_is_detected_even_with_trigger_removed(self):
        self.app.db.db.execute('DROP TRIGGER audit_jobs_update')
        row=self.app.db.job(self.job['id']);row['reason']='Forged by an out-of-band writer'
        self.app.db.db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(row),row['id']))
        with self.assertRaises(Refused): self.app.audit.verify()

    async def test_rollback_of_journal_tail_is_detected_on_restart(self):
        self.app.audit.observe('fixture.checkpoint',{'synthetic':True})
        self.app.db.db.execute('DELETE FROM audit_entries WHERE seq=(SELECT MAX(seq) FROM audit_entries)')
        with self.assertRaises(Refused): self.app.audit.verify()

    async def test_crash_leaves_an_interrupted_signed_recovery_not_success(self):
        run_id='run_synthetic_crash'
        self.app.audit.observe('run.started',{'run_id':run_id,'kind':'crash_fixture'},self.job['id'])
        await self.app.close()
        self.app=App(self.root/'state',offline=True,models=FixtureModels())
        run=next(r for r in self.app.audit.view(self.job['id'])['runs'] if r['run_id']==run_id)
        self.assertEqual(run['outcome']['settlement'],'interrupted')
        self.assertTrue(self.app.audit.verify()['valid'])

    async def test_crash_after_manifest_write_recovers_the_existing_signature_without_replay(self):
        run_id='run_durable_ending_crash'
        self.app.audit.observe('run.started',{'run_id':run_id,'kind':'crash_fixture'},self.job['id'])
        original=self.app.audit.append
        def crash(kind,*args,**kwargs):
            if kind=='run.sealed': raise OSError('Synthetic crash before run index commit')
            return original(kind,*args,**kwargs)
        with patch.object(self.app.audit,'append',side_effect=crash),self.assertRaises(OSError):
            self.app.audit.finish(self.job['id'],run_id,'crash_fixture',{'settlement':'settled','work':'QUEUED'})
        manifest=self.app.audit.root/'runs'/(run_id+'.json')
        original_bytes=manifest.read_bytes()
        await self.app.close()
        self.app=App(self.root/'state',offline=True,models=FixtureModels())
        self.assertEqual(manifest.read_bytes(),original_bytes)
        self.assertTrue(self.app.audit.verify()['valid'])
        self.assertTrue(any(e['kind']=='run.sealed' and e['data'].get('recovered_durable_manifest') for e in self.app.audit.entries()))
        self.assertEqual(self.app.db.job(self.job['id'])['attempt_count'],0)

    async def test_failed_audit_write_prevents_a_file_effect(self):
        project=self.root/'project';project.mkdir()
        path=project/'source.txt';path.write_text('before')
        files=ProjectFiles(project,audit=self.app.audit)
        before=files.read('source.txt')
        original=self.app.audit.observe
        def fail(kind,*args,**kwargs):
            if kind=='file.write.requested': raise Refused('Synthetic journal unavailable')
            return original(kind,*args,**kwargs)
        with patch.object(self.app.audit,'observe',side_effect=fail),self.assertRaises(Refused):
            files.write('source.txt','after',before['sha256'])
        self.assertEqual(path.read_text(),'before')

    async def test_http_trace_has_destination_and_result_hash_but_no_credentials(self):
        from monkey.adapters import HTTP
        secret='synthetic-private-token-not-for-trace'
        async def fixture(request): return httpx.Response(200,json={'actual':'fixture'})
        http=HTTP(transport=httpx.MockTransport(fixture));http.audit=self.app.audit
        try:
            async with self.app.audit.run(self.job['id'],'http_fixture'):
                await http.request('https://fixture.invalid/tickets/EXACT-1',headers={'Authorization':'Bearer '+secret})
        finally: await http.close()
        bundle,_=await self.bundle()
        raw=json.dumps(bundle)
        self.assertNotIn(secret,raw)
        self.assertIn('https://fixture.invalid/tickets/EXACT-1',raw)
        self.assertIn('provider_http.completed',raw)


if __name__=='__main__': unittest.main()
