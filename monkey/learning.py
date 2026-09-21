"""Durable advisory recall, bounded local specialists, and Kist evolution status."""
import asyncio
import re
from pathlib import Path

from .common import STR, STRINGS, digest, encoded, identity, now, object_schema, require
from .sml import environment, file_hash, stop


class Learning:
    def __init__(self, app):
        self.app, self.db, self.core = app, app.db, app.execution
        self.tasks = set()
        records = self.db.records('artifacts')
        settled = {r['agent_id'] for r in records if r.get('kind') == 'agent.settled'}
        completed = {r['id'] for r in records if r.get('kind') == 'agent.result'}
        for request in records:
            if request.get('kind') == 'agent.request' and request['agent_id'] not in settled:
                self.core.check_seal(request)
                state = 'COMPLETED' if request['agent_id'] in completed else 'INTERRUPTED'
                self.settle(request['parent_job'],request['agent_id'],state,'Recovered the recorded specialist outcome; no automatic replay')

    def settle(self, jid, aid, state, message):
        row = self.core.seal({'id':identity('agentsettled_'),'kind':'agent.settled','at':now(),'agent_id':aid,'parent_job':jid,'state':state})
        self.core.save(jid,'agent.'+state.lower(),{},message,row)

    def remember(self, text, job=None):
        require(type(text) is str and 0 < len(text.strip()) <= 6000, 'Give a bounded note to remember')
        record = self.core.seal({'id':identity('memory_'),'kind':'memory.note','text':text,'at':now(),
            'operator':self.app.operator,'origin':self.app.command('remember')['origin'],
            'job_id':job['id'] if job else None,'authority':'advisory; cannot grant tools or establish completion'})
        if job:
            self.core.save(job['id'],'memory.saved',{},'Note retained with provenance; executable rules remain separate',record)
        else:
            self.db.global_event('memory.saved',{'message':'Advisory note retained'},self.app.command('remember'),record=('artifacts',record['id'],record))
        return record

    def recall(self, query):
        words = set(re.findall(r'[\w-]+',query.lower()))
        require(words,'Enter words or an exact ticket ID to recall')
        hits = []
        for job in self.db.jobs():
            ticket = self.db.record('snapshots',job['snapshot_id'])['ticket']
            if any(word in (ticket['title']+' '+ticket['body']+' '+job['id']+' '+job['key']).lower() for word in words):
                hits.append({'kind':'ticket','job_id':job['id'],'key':job['key'],'title':ticket['title'],'evidence':'/show '+job['id']})
        for record in self.db.records('artifacts'):
            if record.get('kind') in {'memory.note','experience.procedure','agent.reflection','agent.result','project.signoff','improvement.proposal','mcp.call','mcp.signoff','mcp.resolution','mission.signoff','mission.result'} and any(word in encoded(record).decode().lower() for word in words):
                self.core.check_seal(record)
                excerpt = {k:record[k] for k in ('text','task','result','summary','procedure','findings','objective','status','parent_job','job_id','plan_hash','call_id','note','authority','origin') if k in record}
                rendered = encoded(excerpt).decode()
                hits.append({'kind':record['kind'],'id':record['id'],'at':record['at'],'excerpt':rendered[:1800],
                    'excerpt_truncated':len(rendered)>1800,'evidence':'artifact:'+record['id']})
        selected = hits[-12:]
        while len(encoded(selected))>16000:
            selected.pop(0)
        return {'query':query,'records':selected,'truncated':len(hits)>len(selected),'note':'Recorded evidence and advisory memory retain their original authority; recalling does not promote them'}

    def delegate(self, job, text,context=None):
        require(len(self.tasks)<2,'Two local specialists are already queued or running')
        require(type(text) is str and 0<len(text)<=3000,'Give a bounded specialist subtask')
        require(job.get('agent_count',0)<3 and job['call_count']<job['limits']['calls'],'Original agent/provider-call budget exhausted')
        aid = identity('agent_')
        request = self.core.seal({'id':identity('agentrequest_'),'kind':'agent.request','at':now(),'agent_id':aid,'parent_job':job['id'],
            'snapshot_id':job['snapshot_id'],'task':text,'authority':'advisory; no delegated tool approval'})
        self.core.save(job['id'],'agent.requested',{'agent_count':job.get('agent_count',0)+1,'call_count':job['call_count']+1},
            'Local specialist queued for a bounded advisory subtask',request,details={'agent_id':aid,'parent_job':job['id'],'task':text,'authority':'proposal only'})
        async def work():
            try:
                c = job['recipe']
                events = self.db.events(job_id=job['id'])[-20:]
                references = ['event:'+str(e['seq']) for e in events]
                schema = object_schema({'summary':STR,'questions':STRINGS,'evidence_refs':{'type':'array','maxItems':12,'items':{'type':'string','enum':references}}})
                value, usage = await self.app.models.local(c,c['ollama_model'],c['ollama_digest'],
                    'Work on one advisory specialist subtask using the supplied recorded evidence. You have no mutation tools or approval authority. '
                    'Give a short recommendation and cite only supplied evidence references. Ask questions if information is missing. '
                    'Never claim execution, tests, independent review or completed external actions.',
                    {'subtask':text,'parent_ticket':self.db.record('snapshots',job['snapshot_id'])['ticket'],'events':events,
                     'recall':self.recall(text),'general_work_evidence':context},schema,output=2048,label='specialist '+job['key'])
                self.app.record_chat_call('specialist',usage,False)
                record = self.core.seal({'id':aid,'kind':'agent.result','at':now(),'parent_job':job['id'],'snapshot_id':job['snapshot_id'],
                    'task':text,'result':value,'model_route':usage,'independent':False,'authority':'advisory; exact operator plan approval still required'})
                self.core.save(job['id'],'agent.completed',{},'Local specialist recommendation saved; no tools or approval authority delegated',record)
                self.settle(job['id'],aid,'COMPLETED','Specialist result recorded with evidence references')
            except asyncio.CancelledError:
                self.settle(job['id'],aid,'INTERRUPTED','Specialist interrupted; no completed result recorded')
            except Exception as exc:
                self.settle(job['id'],aid,'FAILED','Specialist could not complete: '+type(exc).__name__)
        async def traced_work():
            async with self.db.audit.run(job['id'],'specialist'):
                await work()
        task = asyncio.create_task(traced_work())
        task.monkey_job = job['id']
        task.monkey_agent = aid
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {'message':'Specialist queued; keep using the prompt','agent_id':aid,'parent_job':job['id'],'authority':'advisory only'}

    async def evolution_status(self, job=None):
        c = self.db.config()
        if not c['kist_binary']:
            return {'runtime':'Kist DGM','execution':'unconfigured','automatic_promotion':False,
                    'automatic_improvement_claim':False,'report':'The optional native DGM adapter is not configured. Monkey MCP/API work uses its own captured admission runtime.'}
        root = self.core.record(job,'contract_id')['workspace'] if job and job.get('contract_id') else str(Path(__file__).resolve().parents[1])
        binary = str(Path(c['kist_binary']).expanduser())
        process = await asyncio.create_subprocess_exec(binary,'--project',root,'--evolve-status',stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,env=environment(),start_new_session=True)
        try:
            raw, _ = await asyncio.wait_for(process.communicate(),10)
            require(process.returncode==0 and len(raw)<65536,'Kist evolution diagnostics unavailable')
            report = raw.decode(errors='replace')
        finally:
            await stop(process)
        return {'runtime':'Kist DGM','binary_hash':file_hash(binary),'observed_at':now(),'report':report,
                'execution':'blocked' if 'REFUSED' in report else 'requires native Kist gate verification',
                'automatic_promotion':False,'automatic_improvement_claim':False}

    async def improve(self, job, text):
        require(text and len(text)<=6000,'Describe the capability improvement to investigate')
        status = await self.evolution_status(job)
        record = self.core.seal({'id':identity('improvement_'),'kind':'improvement.proposal','at':now(),'parent_job':job['id'],
            'objective':text,'parent_snapshot':job['snapshot_id'],'parent_recipe_hash':digest(job['recipe']),
            'kist_readiness':status,'advisory_memory':self.recall(text),'status':'PROPOSED',
            'required_evidence':['pinned parent','private held-out benchmark','containment receipt','distinct reviewer','rollback witness','explicit promotion approval'],
            'capability_delta':None,'promoted':False})
        self.core.save(job['id'],'improvement.proposed',{},'Improvement proposal retained. Kist readiness governs execution; no capability gain or deployment is claimed.',record)
        return record

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks),return_exceptions=True)
