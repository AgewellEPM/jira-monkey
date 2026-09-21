"""Versioned procedures learned from accepted outcomes, never permission grants."""
import re

from .common import digest, identity, now, require

STOP={'the','a','an','and','to','for','of','in','with','it','this','that','build','make','create','please','another','new','write','research'}


def terms(text):
    return sorted(set(re.findall(r'[a-z][a-z0-9_+-]{2,}',text.lower()))-STOP)[:80]


class Experience:
    def __init__(self,app):
        self.app,self.db,self.core=app,app.db,app.execution

    def records(self):
        return [self.core.check_seal(r) for r in self.db.records('artifacts') if r.get('kind')=='experience.procedure']

    def recommend(self,objective,work_type):
        wanted=set(terms(objective));latest={}
        for record in self.records(): latest[record['family']]=record
        scored=[]
        for record in latest.values():
            overlap=wanted & set(record['terms'])
            if record['work_type']==work_type and overlap:
                scored.append((len(overlap)/max(1,len(wanted | set(record['terms']))),record))
        selected=[r for _,r in sorted(scored,key=lambda v:(v[0],v[1]['version']),reverse=True)[:3]]
        return [{'id':r['id'],'family':r['family'],'version':r['version'],'procedure':r['procedure'],
            'accepted_examples':r['accepted_examples'],'source_jobs':r['source_jobs'],'measurement':r['measurement'],
            'authority':'advisory procedure; host tool scope and operator authority are unchanged'} for r in selected]

    def learn(self,job,result,reflection,signoff):
        procedure=self.validate_procedure(reflection)
        objective=self.db.record('snapshots',job['snapshot_id'])['ticket']['body']
        key=digest([job['work_type'],terms(objective)])
        previous=[r for r in self.records() if r['family']==key]
        prior=previous[-1] if previous else None
        source_jobs=list(dict.fromkeys([*prior['source_jobs'],job['id']])) if prior else [job['id']]
        record=self.core.seal({'id':identity('experience_'),'kind':'experience.procedure','at':now(),
            'family':key,'version':prior['version']+1 if prior else 1,'parent_id':prior['id'] if prior else None,
            'work_type':job['work_type'],'terms':terms(objective),'procedure':procedure[:8],
            'accepted_examples':len(source_jobs),'source_jobs':source_jobs,'result_hash':result['result_hash'],
            'signoff_id':signoff['id'],'measurement':{'model_calls':job['call_count'],'tool_calls':job['agent_tool_count'],
                'failed_commands':result['failed_commands'],'successful_commands':result['successful_commands'],
                'paired_quality_improvement_proven':False},
            'authority':'reusable advisory procedure; cannot authorize tools or override policy'})
        self.core.save(job['id'],'experience.learned',{},'A versioned procedure was retained from the accepted outcome; future similar work can use it.',record)
        return record

    @staticmethod
    def validate_procedure(reflection):
        procedure=reflection.get('procedure',[])
        require(procedure and all(type(s) is str and 0<len(s)<=1000 for s in procedure),'Retain a concrete evidence-linked procedure before learning')
        return procedure

    def view(self,query=''):
        rows=self.records()
        if query: rows=[r for r in rows if set(terms(query)) & set(r['terms'])]
        return {'procedures':rows[-30:],'automatic_recall':True,'source':'operator-accepted general work outcomes',
            'measurement':'Recorded results and participation; paired held-out improvement must be evaluated separately.'}
