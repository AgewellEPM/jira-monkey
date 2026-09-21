"""Signed, hash-linked host trace. External peer internals remain explicitly unobserved."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import stat

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .common import Refused, digest, encoded, identity, now, require
from .security import private_parent, strict_json
from .platform_files import read_regular, write_private

JOB = ContextVar('monkey_trace_job',default=None)
RUN = ContextVar('monkey_trace_run',default=None)
ZERO = '0'*64
TABLES = ('jobs','snapshots','attempts','drafts','reviews','approvals','outbox','provider_calls','artifacts','schedules','events','commands','chat_messages','sessions')


def read_private(path):
    return strict_json(read_regular(path,16384,private=True),limit=16384)


def replace_private(path,value):
    write_private(path,encoded(value),replace=True)


def verify_entries(entries,public_key, *, expected_fingerprint=None):
    require(type(entries) is list and type(public_key) is str,'Malformed signed journal')
    try:
        raw=base64.b64decode(public_key,validate=True)
        key=Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError,TypeError): raise Refused('Invalid audit public key') from None
    fingerprint=hashlib.sha256(raw).hexdigest()
    require(expected_fingerprint is None or fingerprint==expected_fingerprint,'Audit signer differs from the trusted key fingerprint')
    previous=ZERO
    for number,entry in enumerate(entries,1):
        require(type(entry) is dict and set(entry)=={'seq','id','at','kind','job_ids','run_id','data','previous_hash','hash','signature'},'Malformed signed trace entry')
        fields={k:v for k,v in entry.items() if k not in {'hash','signature'}}
        require(entry['seq']==number and entry['previous_hash']==previous and digest(fields)==entry['hash'],'Audit chain was altered, reordered or truncated internally')
        try: key.verify(base64.b64decode(entry['signature'],validate=True),bytes.fromhex(entry['hash']))
        except Exception: raise Refused('Audit signature is invalid') from None
        previous=entry['hash']
    return {'entries':len(entries),'head':previous,'signer_fingerprint':fingerprint}


def verify_bundle(bundle, *, expected_fingerprint=None):
    require(type(bundle) is dict and set(bundle)=={'schema_version','entries','public_key','seal'} and bundle['schema_version']==1,'Unknown trace bundle schema')
    result=verify_entries(bundle['entries'],bundle['public_key'],expected_fingerprint=expected_fingerprint)
    seal=bundle['seal']
    require(type(seal) is dict and type(seal.get('entry_count')) is int and type(seal.get('head')) is str,'Malformed signed trace ending')
    require(seal['entry_count']==result['entries'] and seal['head']==result['head'],'Signed trace ending does not match its journal')
    fields={k:v for k,v in seal.items() if k!='signature'}
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(bundle['public_key'],validate=True)).verify(base64.b64decode(seal['signature'],validate=True),encoded(fields))
    except Exception: raise Refused('Trace ending signature is invalid') from None
    return {**result,'valid':True,'run_id':seal.get('run_id'),'job_id':seal.get('job_id'),'outcome':seal.get('outcome')}


def verify_file(path,fingerprint):
    bundle=strict_json(read_regular(Path(path).expanduser(),50000000),limit=50000000,nodes=1000000)
    return verify_bundle(bundle,expected_fingerprint=fingerprint)


class Audit:
    def __init__(self, database):
        self.owner,self.db,self.root=database,database.db,database.root/'audit'
        from jira_monkey import private_directory
        from .sml import put
        private_directory(self.root)
        existing=self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_entries'").fetchone()
        key_path=self.root/'signing-key.json'
        if not key_path.exists():
            require(not existing,'Audit signing key is missing; do not silently replace trust identity')
            key=Ed25519PrivateKey.generate()
            seed=key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
            put(key_path,{'schema_version':1,'seed':base64.b64encode(seed).decode()})
        seed=read_private(key_path)['seed']
        self.key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed,validate=True))
        raw=self.key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        self.public_key=base64.b64encode(raw).decode()
        self.fingerprint=hashlib.sha256(raw).hexdigest()
        self.columns={table:[row[1] for row in self.db.execute('PRAGMA table_info('+table+')')] for table in TABLES}
        self.db.create_function('monkey_row_hash',-1,lambda *values:digest(list(values)))
        if not existing:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('CREATE TABLE audit_entries(seq INTEGER PRIMARY KEY, data TEXT NOT NULL)')
                self.db.execute('CREATE TABLE audit_pending(table_name TEXT, row_id TEXT, job_id TEXT, operation TEXT, record_hash TEXT)')
                self.db.execute('CREATE TABLE audit_runs(run_id TEXT PRIMARY KEY, job_id TEXT, data TEXT NOT NULL)')
                self.append('audit.baseline',{'changes':self.inventory(),'coverage':'Existing records captured at upgrade; earlier I/O has not been reconstructed'},[]) 
                self.db.commit()
            except BaseException:
                self.db.rollback();raise
        else:
            self.verify()
        # Triggers record every application-store row mutation, even when a caller
        # forgets to supply an event. Pending mutations must be signed in its transaction.
        for table,columns in self.columns.items():
            primary=columns[0]
            for action,alias in (('insert','NEW'),('update','NEW'),('delete','OLD')):
                job=(alias+'.id') if table=='jobs' else (alias+'.job_id') if 'job_id' in columns else 'NULL'
                values=','.join(alias+'.'+column for column in columns)
                content="'deleted'" if action=='delete' else 'monkey_row_hash('+values+')'
                self.db.execute('CREATE TRIGGER IF NOT EXISTS audit_'+table+'_'+action+' AFTER '+action.upper()+' ON '+table+
                    ' BEGIN INSERT INTO audit_pending VALUES(\''+table+'\',CAST('+alias+'.'+primary+' AS TEXT),'+job+',\''+action+'\','+content+'); END')
        self.checkpoint()

    def inventory(self):
        rows=[]
        for table,columns in self.columns.items():
            for row in self.db.execute('SELECT * FROM '+table):
                data=dict(row)
                rows.append({'table':table,'id':str(row[0]),'job_id':data.get('job_id',data.get('id') if table=='jobs' else None),'operation':'insert','hash':digest(list(row))})
        return rows

    def entries(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT data FROM audit_entries ORDER BY seq')]

    def append(self,kind,data,job_ids=None):
        require(self.db.in_transaction,'Audit append must share a durable transaction')
        prior=self.db.execute('SELECT seq,data FROM audit_entries ORDER BY seq DESC LIMIT 1').fetchone()
        seq=prior[0]+1 if prior else 1
        previous=json.loads(prior[1])['hash'] if prior else ZERO
        job_ids=job_ids if job_ids is not None else ([JOB.get()] if JOB.get() else [])
        row={'seq':seq,'id':identity('trace_'),'at':now(),'kind':kind,'job_ids':sorted(set(job_ids)),
             'run_id':RUN.get(),'data':data,'previous_hash':previous}
        row['hash']=digest(row)
        row['signature']=base64.b64encode(self.key.sign(bytes.fromhex(row['hash']))).decode()
        self.db.execute('INSERT INTO audit_entries VALUES(?,?)',(seq,encoded(row).decode()))
        return row

    def commit(self):
        pending=[{'table':row[0],'id':row[1],'job_id':row[2],'operation':row[3],'hash':row[4]} for row in self.db.execute('SELECT * FROM audit_pending')]
        if pending:
            events=[]
            for change in pending:
                if change['table']=='events' and change['operation']=='insert':
                    row=self.db.execute('SELECT seq,kind,job_id,data FROM events WHERE seq=?',(change['id'],)).fetchone()
                    events.append({'seq':row[0],'kind':row[1],'job_id':row[2],'data':json.loads(row[3])})
            self.append('store.committed',{'changes':pending,'events':events},[r['job_id'] for r in pending if r['job_id']])
            self.db.execute('DELETE FROM audit_pending')

    def checkpoint(self):
        row=self.db.execute('SELECT data FROM audit_entries ORDER BY seq DESC LIMIT 1').fetchone()
        if row:
            tip=json.loads(row[0])
            replace_private(self.root/'checkpoint.json',{'seq':tip['seq'],'hash':tip['hash'],'fingerprint':self.fingerprint})

    def observe(self,kind,data,job_id=None):
        jobs=[job_id or JOB.get()] if job_id or JOB.get() else []
        if self.db.in_transaction:
            return self.append(kind,data,jobs)
        with self.owner.transaction():
            return self.append(kind,data,jobs)

    def verify(self):
        try:
            return self._verify()
        except (Refused,OSError,ValueError,TypeError,KeyError,IndexError) as exc:
            self.owner.failed=True
            raise Refused('Signed audit verification failed; work is stopped. '+(str(exc) if isinstance(exc,Refused) else type(exc).__name__)) from None

    def _verify(self):
        snapshot=self.verification_snapshot()
        result=self._verify_snapshot(snapshot)
        require(self.db.execute('PRAGMA data_version').fetchone()[0]==snapshot['data_version'],
                'An external writer changed the store during verification')
        return result

    def verification_snapshot(self):
        # Copy SQLite state on its owning thread without awaiting. Expensive
        # signature checks and immutable run-file reads can then run off-loop.
        version=self.db.execute('PRAGMA data_version').fetchone()[0]
        entries=self.entries()
        checkpoint=self.root/'checkpoint.json'
        require(checkpoint.exists(),'Audit checkpoint is missing; preserve the journal and its trust identity')
        anchor=read_private(checkpoint)
        require(not self.db.execute('SELECT 1 FROM audit_pending LIMIT 1').fetchone(),'Unsigned store mutations detected; preserve evidence and stop work')
        inventory=[(table,str(row[0]),list(row)) for table in self.columns for row in self.db.execute('SELECT * FROM '+table)]
        runs={row[0]:json.loads(row[1]) for row in self.db.execute('SELECT run_id,data FROM audit_runs')}
        require(self.db.execute('PRAGMA data_version').fetchone()[0]==version,'An external writer changed the store while capturing verification')
        return {'entries':entries,'anchor':anchor,'inventory':inventory,'runs':runs,'data_version':version}

    def _verify_snapshot(self,snapshot):
        entries,anchor=snapshot['entries'],snapshot['anchor']
        result=verify_entries(entries,self.public_key,expected_fingerprint=self.fingerprint)
        require(anchor['fingerprint']==self.fingerprint and 0<anchor['seq']<=len(entries)
            and entries[anchor['seq']-1]['hash']==anchor['hash'],'Audit rollback or signer replacement detected against the saved checkpoint')
        expected={}
        for entry in entries:
            for change in entry['data'].get('changes',[]) if entry['kind'] in {'audit.baseline','store.committed'} else []:
                key=(change['table'],change['id'])
                if change['operation']=='delete': expected.pop(key,None)
                else: expected[key]=change['hash']
        actual={(table,identifier):digest(values) for table,identifier,values in snapshot['inventory']}
        require(actual==expected,'Recorded store content differs from the signed trace')
        expected_runs={entry['data']['run_id']:entry['data']['seal_hash'] for entry in entries if entry['kind']=='run.sealed'}
        actual_runs=snapshot['runs']
        require(set(actual_runs)==set(expected_runs),'Signed run index differs from the trace')
        for run_id,seal in actual_runs.items():
            require(digest(seal)==expected_runs[run_id],'Run seal changed')
            fields={k:v for k,v in seal.items() if k!='signature'}
            try: self.key.public_key().verify(base64.b64decode(seal['signature'],validate=True),encoded(fields))
            except Exception: raise Refused('Run signature changed') from None
            require(0<seal['entry_count']<=len(entries) and entries[seal['entry_count']-1]['hash']==seal['head'],'Run signature refers to a different trace')
            require(read_private(self.root/'runs'/(run_id+'.json'))==seal,'Durable run signature file changed')
        return {**result,'valid':True,'store_matches':True}

    async def verify_async(self):
        try:
            snapshot=self.verification_snapshot()
            result=await asyncio.to_thread(self._verify_snapshot,snapshot)
            require(self.db.execute('PRAGMA data_version').fetchone()[0]==snapshot['data_version'],
                    'An external writer changed the store during verification')
            # Concurrent Monkey appends remain individually signed. This proof
            # covers exactly the captured prefix, as its entry count specifies.
            return result
        except (Refused,OSError,ValueError,TypeError,KeyError,IndexError) as exc:
            self.owner.failed=True
            raise Refused('Signed audit verification failed; work is stopped. '+(str(exc) if isinstance(exc,Refused) else type(exc).__name__)) from None

    def finish(self,job_id,run_id,kind,outcome):
        self.observe('run.finished',{'run_id':run_id,'kind':kind,'outcome':outcome},job_id)
        verification=self.verify()
        return self.write_seal(job_id,run_id,kind,outcome,verification)

    async def finish_async(self,job_id,run_id,kind,outcome):
        self.observe('run.finished',{'run_id':run_id,'kind':kind,'outcome':outcome},job_id)
        verification=await self.verify_async()
        return self.write_seal(job_id,run_id,kind,outcome,verification)

    def write_seal(self,job_id,run_id,kind,outcome,verification):
        seal={'schema_version':1,'run_id':run_id,'job_id':job_id,'kind':kind,'finished_at':now(),
              'outcome':outcome,'entry_count':verification['entries'],'head':verification['head'],
              'signer_fingerprint':self.fingerprint,'algorithm':'Ed25519 + SHA-256',
              'coverage':'Monkey-mediated requests, scoped file operations, child boundaries, committed state and observed results. External service and child-internal activity is not an OS-wide trace.'}
        seal['signature']=base64.b64encode(self.key.sign(encoded(seal))).decode()
        from .sml import put
        destination=self.root/'runs'/(run_id+'.json')
        put(destination,seal)
        with self.owner.transaction():
            self.db.execute('INSERT INTO audit_runs VALUES(?,?,?)',(run_id,job_id,encoded(seal).decode()))
            self.append('run.sealed',{'run_id':run_id,'seal_hash':digest(seal),'path':str(destination)},[job_id] if job_id else [])
        return seal

    @asynccontextmanager
    async def run(self,job_id,kind):
        run_id=identity('run_')
        job_marker,run_marker=JOB.set(job_id),RUN.set(run_id)
        outcome='settled'
        try:
            self.observe('run.started',{'kind':kind,'run_id':run_id},job_id)
            yield run_id
        except BaseException as exc:
            outcome='interrupted' if type(exc).__name__=='CancelledError' else 'failed'
            raise
        finally:
            try:
                if job_id:
                    job=self.owner.job(job_id)
                    outcome={'settlement':outcome,'work':job['work_state'],'delivery':job['delivery_state'],
                        'execution':job.get('execution_state'),'mission':job.get('mission_state'),'tool_delivery':job.get('tool_delivery'),'agent':job.get('agent_state')}
                await self.finish_async(job_id,run_id,kind,outcome)
            finally:
                JOB.reset(job_marker);RUN.reset(run_marker)

    def view(self,job_id=None):
        result=self.verify()
        entries=[r for r in self.entries() if job_id is None or job_id in r['job_ids']]
        runs=[json.loads(r[0]) for r in self.db.execute('SELECT data FROM audit_runs'+(' WHERE job_id=?' if job_id else '')+' ORDER BY rowid',(job_id,) if job_id else ())]
        return {**result,'job_id':job_id,'trace':entries[-300:],'trace_truncated':len(entries)>300,'runs':runs[-20:],
            'message':'Signed recorded trace; an interrupted or failed run remains interrupted or failed.'}

    def recover_runs(self):
        entries=self.entries()
        sealed={row[0] for row in self.db.execute('SELECT run_id FROM audit_runs')}
        started={entry['data']['run_id']:entry for entry in entries if entry['kind']=='run.started'}
        for run_id,entry in started.items():
            if run_id not in sealed:
                job_id=entry['job_ids'][0] if len(entry['job_ids'])==1 else None
                destination=self.root/'runs'/(run_id+'.json')
                if destination.exists():
                    # A crash can leave the durable signed ending just before
                    # its SQLite index commit. Validate and index that receipt;
                    # never overwrite it or re-execute the original work.
                    seal=read_private(destination)
                    verify_bundle({'schema_version':1,'entries':entries[:seal['entry_count']],
                        'public_key':self.public_key,'seal':seal},expected_fingerprint=self.fingerprint)
                    require(seal['run_id']==run_id and seal['job_id']==job_id and seal['kind']==entry['data']['kind'], 'Orphaned run signature has a different identity')
                    with self.owner.transaction():
                        self.db.execute('INSERT INTO audit_runs VALUES(?,?,?)',(run_id,job_id,encoded(seal).decode()))
                        self.append('run.sealed',{'run_id':run_id,'seal_hash':digest(seal),'path':str(destination),'recovered_durable_manifest':True},[job_id] if job_id else [])
                else:
                    self.finish(job_id,run_id,entry['data']['kind'],{'settlement':'interrupted','reason':'Foreground owner ended before a signed closing manifest; retained effects need inspection'})

    def export(self,job_id=None):
        self.verify()
        run_id=identity('export_')
        seal=self.finish(job_id,run_id,'operator_export','checkpoint; does not mark work complete')
        bundle={'schema_version':1,'public_key':self.public_key,'entries':self.entries()[:seal['entry_count']],'seal':seal}
        verify_bundle(bundle,expected_fingerprint=self.fingerprint)
        from .sml import put
        path=self.root/'exports'/(run_id+'.json')
        put(path,bundle)
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'signer_fingerprint':self.fingerprint,'entries':len(bundle['entries']),
            'message':'Private signed journal bundle exported. It includes shared application activity; keep the public-key fingerprint separately to detect signer replacement.'}
