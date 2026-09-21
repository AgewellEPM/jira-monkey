"""Monkey's durable admission boundary for an exactly scoped operation.

Models cannot invoke this boundary or supply its authority. It uses the live job,
sealed plan and sealed approval, then records a consumed claim before the callback.
This is an application control, not a sandbox or an independent correctness proof.
"""
from pathlib import Path

from .common import digest, identity, now, require
from .platform_files import read_regular
from .sml import file_hash, put
from .security import strict_json

KIND = 'monkey-host-v1'


def recipe():
    root = Path(__file__).resolve().parent
    files = sorted(root.glob('*.py'))
    require(files and all(not p.is_symlink() for p in files),'Admission source files must be regular package files')
    sources={p.name:file_hash(p) for p in files}
    sources['../jira_monkey.py']=file_hash(root.parent/'jira_monkey.py')
    return {'kind':KIND,'sources':sources}


LOADED_RECIPE = recipe()


def validate_runtime(runtime):
    require(runtime == LOADED_RECIPE == recipe(),
            'Monkey admission code changed; restart, then prepare and approve a fresh exact request')


def judgment(facts):
    """Only host-established facts enter this predicate; there is no model vote."""
    required = {'exact_plan','source_current','schema_current','operator_reviewed','original_budget','scope_current'}
    require(type(facts) is dict and set(facts) == required and all(type(v) is bool for v in facts.values()),
            'Admission requires all exact host facts')
    failed = sorted(key for key,value in facts.items() if not value)
    return {'runtime':KIND,'verdict':'blocked' if failed else 'green','failed_rules':failed,
            'facts':facts,'scope':'one exact reviewed request; service outcome remains unverified'}


def review_evidence(runtime,facts):
    """A software upgrade must not require replaying an already consumed effect."""
    validate_runtime(LOADED_RECIPE)
    require(type(runtime) is dict and runtime.get('kind')==KIND,'Unknown historical admission runtime')
    required={'exact_binding','evidence_intact','operator_reviewed','original_budget','scope_current'}
    require(type(facts) is dict and set(facts)==required and all(type(v) is bool for v in facts.values()),
        'Evidence review requires all exact host facts')
    failed=sorted(key for key,value in facts.items() if not value)
    return {'runtime':KIND,'verdict':'blocked' if failed else 'green','failed_rules':failed,'facts':facts,
        'execution_runtime_hash':digest(runtime),'review_runtime_hash':digest(LOADED_RECIPE),
        'runtime_changed':runtime!=LOADED_RECIPE,'scope':'operator review of retained evidence; grants no new execution authority'}


class Admission:
    def __init__(self,app,runtime,jid,plan,approval):
        self.app,self.runtime,self.jid,self.plan,self.approval = app,runtime,jid,plan,approval

    def guard(self,operation_id):
        validate_runtime(self.runtime)
        core = self.app.execution
        core.check_seal(self.plan)
        core.check_seal(self.approval)
        job = self.app.db.job(self.jid)
        require(job.get('tool_delivery')=='CALLING' and job.get('tool_call_id')==operation_id,
                'This connected action has no live, exact reserved call')
        require(self.approval['job_id']==self.jid and self.approval['call_id']==operation_id
                and self.approval['plan_id']==self.plan['id']
                and self.approval['plan_hash']==self.plan['plan_hash'], 'Connected approval binding changed')
        require(job.get('tool_plan_id')==self.plan['id'], 'Connected plan changed')
        return job

    async def execute(self,root,callback,before,*,operation_id):
        root = Path(root)
        require(root == self.app.db.root/'executions'/operation_id,'Admission evidence directory differs from its reserved call')
        self.guard(operation_id)
        await before()
        self.guard(operation_id)
        claim = self.app.execution.seal({'id':identity('claim_'),'kind':'admission.claim','at':now(),
            'runtime':self.runtime,'job_id':self.jid,'operation_id':operation_id,
            'plan_hash':self.plan['plan_hash'],'approval_id':self.approval['id'],
            'authority':'captured operator scope and exact effect approval','state':'CONSUMED'})
        # Exclusive creation means a retained claim is never silently replayed.
        put(root/'claim.json',claim)
        claim_hash = file_hash(root/'claim.json')
        self.app.audit.observe('admission.claimed',{'operation_id':operation_id,'job_id':self.jid,
            'plan_hash':self.plan['plan_hash'],'claim_hash':claim_hash,'runtime_hash':digest(self.runtime)},self.jid)
        self.guard(operation_id)
        try:
            result = await callback()
            receipt = self.app.execution.seal({'id':identity('receipt_'),'kind':'admission.receipt','at':now(),
                'operation_id':operation_id,'job_id':self.jid,'claim_hash':claim_hash,'result':result,
                'status':'RESPONSE_RETAINED','outcome_verified':False})
            put(root/'receipt.json',receipt)
            retained = strict_json(read_regular(root/'receipt.json',100000,private=True),limit=100000)
            self.app.execution.check_seal(retained)
            require(retained==receipt,'Admission receipt read-back differs')
            evidence = {'runtime':KIND,'task_id':operation_id,'runtime_hash':file_hash(root/'receipt.json'),
                'runtime_path':str(root/'receipt.json'),'claim_hash':claim_hash,'claim_path':str(root/'claim.json'),
                'recipe_hash':digest(self.runtime),'receipts':[claim,receipt],'result':result}
            self.app.audit.observe('admission.response_retained',{'operation_id':operation_id,
                'receipt_hash':evidence['runtime_hash'],'result_hash':digest(result),'outcome_verified':False},self.jid)
            return evidence
        except BaseException as exc:
            self.app.audit.observe('admission.uncertain',{'operation_id':operation_id,
                'error_type':type(exc).__name__,'claim_hash':claim_hash,'automatic_resend':False},self.jid)
            raise


class ProjectAdmission(Admission):
    """Scoped project reads or one approved write/verifier operation."""
    def __init__(self,app,contract,jid,plan=None,approval=None,execution_id=None,expected=None):
        self.contract=contract
        self.execution_id=execution_id
        self.expected=expected
        self.exploring=plan is None
        super().__init__(app,contract['admission'],jid,
            plan or {'plan_hash':digest(contract)},approval or contract)

    def guard(self,operation_id):
        import datetime as dt
        from .scheduling import schedule_hash
        validate_runtime(self.runtime)
        core=self.app.execution
        core.check_seal(self.contract)
        job=self.app.db.job(self.jid)
        files=core.stable(job,self.contract)
        require(job['work_state'] not in {'PAUSED','CANCELLED','REJECTED'},'Project work is stopped')
        require(job.get('tool_count',0)<=24,'Original project tool budget exhausted')
        if self.exploring:
            require(job.get('execution_state')=='EXPLORING','Project exploration has no live reserved stage')
        else:
            core.check_seal(self.plan)
            core.check_seal(self.approval)
            require(job.get('execution_state') in {'EXECUTING','VERIFYING'}
                and job.get('execution_id')==self.execution_id
                and operation_id.startswith(self.execution_id+'-'),'Project operation is not part of the live execution')
            require(job.get('plan_id')==self.plan['id']
                and self.plan['contract_id']==self.contract['id']
                and job.get('plan_authorization_id')==self.approval['id']
                and self.approval['plan_hash']==self.plan['plan_hash']
                and self.approval['plan_id']==self.plan['id']
                and self.approval['contract_id']==self.contract['id'],'Project approval binding changed')
            schedule=job.get('schedule') or {}
            moment=dt.datetime.now(dt.timezone.utc)
            require(schedule.get('status')=='SCHEDULED'
                and schedule_hash(job)==self.approval['schedule_hash']
                and dt.datetime.fromisoformat(schedule['start_utc'])<=moment<dt.datetime.fromisoformat(schedule['finish_utc']),
                'Project schedule is no longer current')
            require(all(files.read(path)['sha256']==row['sha256'] for path,row in self.expected.items()),
                'Project source changed before dispatch')
        return job


def observed(core,evidence,call,*,approval_id=None):
    require(evidence['runtime']==KIND and evidence['task_id']==call['id'],'Admission evidence belongs to another operation')
    expected = core.db.root/'executions'/call['id']
    require(Path(evidence['runtime_path'])==expected/'receipt.json'
            and Path(evidence['claim_path'])==expected/'claim.json','Admission evidence path differs from its operation')
    claim = core.check_seal(strict_json(read_regular(expected/'claim.json',100000,private=True),limit=100000))
    receipt = core.check_seal(strict_json(read_regular(expected/'receipt.json',100000,private=True),limit=100000))
    require(file_hash(expected/'receipt.json')==evidence['runtime_hash'],'Admission receipt changed')
    require(digest(claim['runtime'])==evidence['recipe_hash'],'Admission runtime binding changed')
    require(file_hash(expected/'claim.json')==evidence['claim_hash']==receipt['claim_hash'], 'Admission claim changed')
    require(claim['operation_id']==receipt['operation_id']==call['id']
            and claim['job_id']==receipt['job_id']==call['job_id']
            and claim['plan_hash']==call['plan_hash'] and claim['state']=='CONSUMED', 'Admission receipt binding changed')
    require(receipt['status']=='RESPONSE_RETAINED' and receipt['result']==evidence['result'], 'Admission result changed')
    if approval_id is not None:
        require(claim['approval_id']==approval_id,'Admission receipt belongs to another approval')
