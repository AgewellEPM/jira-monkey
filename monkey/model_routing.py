"""Explicit model rosters. Changing a model never changes application authority."""
import re
import time

from .common import Refused, clone, digest, require

ROLES=('chat','planning','execution','vision','triage','draft','review','specialist','research','reflection')


def validate_routes(routes):
    require(type(routes) is dict and not set(routes)-set(ROLES),'Unknown model role')
    for role,chain in routes.items():
        require(type(chain) is list and 1<=len(chain)<=3,'Each model role needs one to three explicit candidates')
        for model in chain:
            require(type(model) is dict and set(model)=={'provider','model','digest'},'Model routes contain only provider, model and digest; no policy overrides')
            require(model['provider'] in {'ollama','claude','openai','deepseek'},'Unsupported model provider')
            require(role not in {'chat','vision'} or model['provider']=='ollama','Chat and vision stay on explicitly installed local models')
            require(type(model['model']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}',model['model']),'Invalid exact model identity')
            require(type(model['digest']) is str and (not model['digest'] or re.fullmatch('[0-9a-f]{64}',model['digest'])),'Invalid model digest')
        require(len({digest(m) for m in chain})==len(chain),'Duplicate model candidate')
    return routes


def role_for(label,operator=False):
    if operator: return 'chat'
    label=label.lower()
    if label.startswith('vision'): return 'vision'
    if label.startswith('research '): return 'research'
    if label.startswith('reflection '): return 'reflection'
    if label.startswith(('worker ','researcher ')): return 'execution'
    if label.startswith(('review','reviewer ')): return 'review'
    if label.startswith('triage'): return 'triage'
    if label.startswith('draft'): return 'draft'
    if label.startswith('specialist'): return 'specialist'
    return 'planning'


class Router:
    def __init__(self,models):
        self.models=models
    @property
    def audit(self): return getattr(self.models.http,'audit',None)
    def observe(self,kind,data):
        if self.audit: self.audit.observe(kind,data)

    def metrics(self):
        if not self.audit: return []
        self.audit.verify()
        entries=self.audit.entries()
        profiles={}
        by_job={}
        verified=set()
        for entry in entries:
            data=entry['data']
            if entry['kind'] in {'model.completed','model.transport_failed'}:
                key=(data['role'],data['provider'],data['model'],data.get('digest',''))
                row=profiles.setdefault(key,{'role':key[0],'provider':key[1],'model':key[2],'digest':key[3],
                    'validated_responses':0,'transport_failures':0,'verified_jobs':0,'latencies':[]})
                if entry['kind']=='model.completed':
                    row['validated_responses']+=1
                    row['latencies'].append(data['latency_ms'])
                    for job in entry['job_ids']: by_job.setdefault(job,set()).add(key)
                else: row['transport_failures']+=1
            if entry['kind']=='run.finished' and isinstance(data.get('outcome'),dict):
                outcome=data['outcome']
                if data.get('kind') in {'signoff','mission-signoff','agent-accept'} and outcome.get('settlement')=='settled' and any(outcome.get(field)=='COMPLETED' for field in ('execution','mission','agent')):
                    verified.update(entry['job_ids'])
        for job in verified:
            for key in by_job.get(job,[]): profiles[key]['verified_jobs']+=1
        rows=[]
        for row in profiles.values():
            values=row.pop('latencies')
            row['mean_latency_ms']=sum(values)/len(values) if values else None
            rows.append(row)
        return rows

    def freeze(self,c):
        result=clone(c.get('model_routes',{}))
        if c.get('routing_policy')!='measured': return result
        require(self.audit is not None,'Measured routing requires a verified signed journal')
        metrics={(r['role'],r['provider'],r['model'],r['digest']):r for r in self.metrics()}
        for role,chain in result.items():
            def score(pair):
                index,model=pair
                measured=metrics.get((role,model['provider'],model['model'],model['digest']))
                if not measured or measured['verified_jobs']<3: return (1,index,0)
                reliability=measured['transport_failures']/max(1,measured['validated_responses']+measured['transport_failures'])
                return (0,reliability,measured['mean_latency_ms'] or 0)
            result[role]=[model for _,model in sorted(enumerate(chain),key=score)]
        return result

    def reserve_fallback(self,role):
        from .audit import JOB
        if self.audit and JOB.get():
            db=self.audit.owner
            job=db.job(JOB.get())
            require(job['call_count']<job['limits']['calls'],'Original provider-call budget exhausted; model switching cannot reset it')
            require(job['retry_count']<job['limits']['retries'],'Original infrastructure retry budget exhausted')
            db.change(job['id'],job['version'],'model.fallback_reserved',{'call_count':job['call_count']+1,'retry_count':job['retry_count']+1},
                payload={'role':role,'message':'Configured model fallback reserved inside the original call and retry limits'})

    async def generate(self,c,role,default,instruction,data,shape,**options):
        from .adapters import RequestError
        from .ollama_stream import IncompleteStream
        chain=c.get('model_routes',{}).get(role) or [default]
        validate_routes({role:chain})
        for index,candidate in enumerate(chain):
            if index: self.reserve_fallback(role)
            descriptor={'role':role,**candidate,'candidate':index+1,'candidate_count':len(chain),'recipe_hash':digest(chain),
                'authority':'proposal only; host approvals and sandbox do not change'}
            self.observe('model.selected',descriptor)
            started=time.monotonic()
            try:
                if candidate['provider']=='ollama':
                    value,usage=await self.models._local(c,candidate['model'],candidate['digest'],instruction,data,shape,**options)
                else:
                    require(candidate['provider'] in c.get('allowed_destinations',[]),'Cloud route was not captured in this job’s allowed destinations')
                    require(not options.get('images'),'Cloud image routing is unavailable')
                    value,usage=await self.models._cloud({**c,'provider':candidate['provider'],'model':candidate['model']},instruction,data,shape=shape)
            except IncompleteStream:
                self.observe('model.stream_interrupted',{**descriptor,
                    'reason':'Stream ended without completion; no fallback. The owning worker controls bounded retries.',
                    'latency_ms':(time.monotonic()-started)*1000})
                raise
            except RequestError as exc:
                if exc.status not in {None,429,500,502,503,504}:
                    self.observe('model.blocked',{**descriptor,'reason':'Non-retryable provider response; no fallback or ranking penalty','status':exc.status})
                    raise
                self.observe('model.transport_failed',{**descriptor,'latency_ms':(time.monotonic()-started)*1000,'status':exc.status})
                if index+1==len(chain): raise
                self.observe('model.fallback',{'role':role,'from':candidate,'to':chain[index+1],'reason':'classified transport failure; no authority change'})
                continue
            except Refused:
                # Includes provider refusals, invalid content/schema, permissions,
                # unavailable pins and host safety checks. Never try another model.
                self.observe('model.blocked',{**descriptor,'reason':'refusal or validation boundary; no fallback and no refusal demotion'})
                raise
            latency=(time.monotonic()-started)*1000
            self.observe('model.completed',{**descriptor,'latency_ms':latency,'result_hash':digest(value),'returned_model':usage.get('returned_model'),
                'quality':'validated response; task correctness still requires outcome checks and operator sign-off'})
            return value,{**usage,'role':role,'route_candidate':index+1,'route_recipe_hash':digest(chain)}
