from __future__ import annotations

import asyncio
import getpass
import os
from pathlib import Path
import re
import time

from . import VERSION
from .adapters import HTTP, Jira, Models
from .common import (ACTIVE_DELIVERY, PROVIDERS, Refused, clean_text, clone, digest,
                     identity, material_hash, now, read_import, require, safe)
from .conversation import Conversation, resolve
from .database import Database
from .publishing import Publisher
from .worker import Worker
from .scheduling import Scheduler
from .execution import Execution
from .connectors import Connectors, json_file
from .services import Services
from .gateway import Gateway, ORIGIN
from .operator_context import REQUEST_FOCUS, UNBOUND
from .learning import Learning
from .missions import Missions
from .windows import Windows
from .completion import completed_at

HELP = """Monkey · coding, research, tickets & requests
Tell me what you need. General coding and research require no ticket.

build TEXT [--path ROOT]      Plan, create source, run checks and rethink failures
research TEXT [--path ROOT]   Public search and original sources with retained citations
agent JOB                    Inspect plan, tool results, sources and final result
steer JOB TEXT               Change direction at the next recorded boundary
agent-continue JOB           Continue stopped work inside its original limits
agent-resolve JOB --note TEXT Record inspected interrupted effects; no replay
agent-accept JOB --hash HASH --note TEXT   Accept the exact inspected result
lessons [TEXT]               Inspect reusable procedures from accepted outcomes

routes                       Inspect model roles, captured choices and measured results
route ROLE --model NAME [--provider ollama|claude|openai|deepseek] [--append]
                             Set an explicit role candidate; no authority changes
setup --routing-policy ordered|measured
                             Rank future job rosters from recorded verified jobs

trace JOB | audit JOB         Inspect and verify the signed activity trace
audit-export JOB             Export a private SHA-256 / Ed25519 journal bundle
Monkey audit-verify PATH --fingerprint HASH
                             Verify an exported bundle without the signing key

request TEXT                 Draft any ticket or request from your description
task TEXT                    Capture an execution task; choose Project or Tools
ticket TEXT | sprint TEXT    Ticket and sprint request shortcuts
schedule JOB --start DATETIME --finish DATETIME [--timezone ZONE]
                             Assign a planned start and finish; revisions retained
dates JOB                    Inspect planned dates and schedule history
return JOB --note TEXT       Pause and request new dates; preserve prior evidence
project JOB --path ROOT --objective TEXT --write FILE --verify 'EXEC FILE' --expect FILE=TEXT
                             Select an exact project contract; /project --help
explore JOB | plan JOB       Recorded reads, then a proposed local Ollama plan
authorize JOB --hash HASH --note TEXT   Approve only the inspected plan
execute JOB                 Run approved project tools with durable receipts
execution JOB               Inspect project contracts, plans, tools and receipts
signoff JOB --hash HASH --note TEXT     Sign the exact verified local result
connect [SERVICE]            Monkey builds the adapter; guided, hidden credential entry
connect NAME --file PATH     Import an existing MCP/API configuration
services | disconnect NAME  Available adapters / disconnect while keeping receipts
api start | status | stop    Monkey's own local MCP/API, in this foreground application
dashboard [start|status|stop] Web workspace and chat for this same foreground session
builder setup | start | stop Prepare and own the foreground build environment
builder status | use | native | recover Inspect, select or reconcile the builder
tools [--server NAME]        Connected tools and their exact input schemas
tool JOB --server NAME --name TOOL --args-file PATH   Preview an exact MCP request
tool-request JOB TEXT        Ask local Ollama to propose a connected tool request
tool-run JOB --hash HASH --note TEXT   Approve and call that exact MCP request
tool-result JOB              Inspect retained MCP results and uncertain outcomes
windows-bind JOB --server LEGACY --workflow-server VCARDS --target-id ID --profile-id ID --note TEXT
                             Bind one exact enrolled Windows target; no VM launch
windows JOB                  Inspect session, workflow and cleanup evidence
windows-plan JOB ACTION [--args-file PATH]
                             Propose a typed request for existing /tool-run approval
windows-sync JOB --call ID   Link a separate status read; never resend an action
tool-signoff JOB --help      Sign off against a separate recorded verification call
tool-resolve JOB --help      Resolve an uncertain call using inspected evidence
remember TEXT | recall TEXT  Save advisory notes / search recorded work
delegate JOB TEXT            Queue a bounded local specialist; no tool authority
mission JOB TEXT             Propose a bounded mission of exact tool requests
mission-review JOB           Inspect agent assignments, arguments and outcome checks
mission-answer JOB TEXT      Clarify an unexecuted mission; original objective retained
mission-authorize JOB --hash HASH --note TEXT   Approve the exact mission
mission-run JOB              Run its assigned agents within the original budget
mission-signoff JOB --hash HASH --note TEXT     Sign the observed mission outcome
agents JOB                   Inspect mission agents, recorded steps and limits
evolve-status [JOB]          Read Kist's actual DGM readiness and blockers
improve JOB TEXT             Retain an improvement proposal with parent evidence

fetch KEY | import PATH       Capture a ticket; no implicit publishing
run [JOB_ID]                  One candidate attempt and review
work                          Process queue and bounded revisions while open
status | jobs [--state STATE] Queue, activity and delivery facts
focus JOB_ID                  Select conversational job (visible in prompt)
show | draft | events JOB_ID  Source, exact candidate, or committed timeline
pause JOB_ID --after review   Request a pause at a stage boundary
resume | cancel JOB_ID        Resume boundary / stop future local stages
retry JOB_ID --note TEXT      Another candidate within original limits
reject JOB_ID --note TEXT     Close local work without posting
approve JOB_ID --note TEXT    Approve exact reviewed payload; no publication
publish JOB_ID --confirm KEY  Explicitly send the currently approved payload
reconcile JOB_ID              Read uncertain delivery evidence; never resend
resolve-delivery JOB_ID --note TEXT  Record operator evidence; keep resend blocked
refresh JOB_ID                Capture fresh ticket evidence, invalidate if changed
recover JOB_ID --note TEXT    Acknowledge interrupted work; budgets retained
models | usage | caps         Actual routes, measured calls, capability limits
doctor | setup --help         Non-mutating diagnostics / public configuration
demo [--scenario NAME]        Offline fixtures with no sockets or credentials
multiline                     Compose until a single dot line; /abort discards
help | quit                   Commands / orderly shutdown; no hidden worker

All commands accept / aliases. Natural language uses a local model, while exact
commands remain usable if it is unavailable. Ticket drafting requires no project.
Project edits require a scoped contract, dates and an exact authorized plan.
"""


class App:
    @property
    def focus(self):
        requested = REQUEST_FOCUS.get()
        return self._focus if requested is UNBOUND else requested

    @focus.setter
    def focus(self, value):
        self._focus = value
        if REQUEST_FOCUS.get() is not UNBOUND:
            REQUEST_FOCUS.set(value)

    def __init__(self, root, *, models=None, jira_factory=None, http=None, offline=False):
        self.db = Database(root)
        self.audit = self.db.audit
        self.offline = offline
        self.http = http if http is not None else (None if offline else HTTP())
        if self.http is not None:
            self.http.audit = self.audit
        self.models = models or Models(self.http)
        self.worker = Worker(self.db, self.models)
        self.jira_factory = jira_factory or (lambda c: Jira(c, self.http))
        self.publisher = Publisher(self.db, self.jira_factory)
        self.scheduler = Scheduler(self)
        self.session_id = identity("session_")
        from .platform_files import operator_identity
        self.operator = operator_identity()
        self.execution = Execution(self)
        self.build_environment=None
        self.worker.blocked=lambda:bool(self.execution.active or self.build_environment and self.build_environment.busy)
        self.connectors = Connectors(self)
        self.windows = Windows(self)
        self.services = Services(self)
        self.gateway = Gateway(self)
        self.dashboard = None
        self.learning = Learning(self)
        self.missions = Missions(self)
        from .agent import Agent
        self.agent = Agent(self)
        self.focus = None
        self.pending = None
        self.health = {"ollama": "unchecked", "jira": "unconfigured" if not self.db.config()["site"] else "unchecked"}
        self.conversation = Conversation(self)
        self.background = set()
        self.db.session(self.session_id, {"started_at": now(), "focus": None, "operator": self.operator})
        self.audit.recover_runs()

    def command(self, operation, job=None):
        return {"command_id": identity("cmd_"), "session_id": self.session_id, "operator_identity": self.operator,
                "origin": ORIGIN.get(), "operation": operation, "resolved_job_id": job["id"] if job else None,
                "expected_job_version": job["version"] if job else None, "expected_snapshot_id": job["snapshot_id"] if job else None,
                "expected_draft_hash": self.db.record("drafts", job["draft_id"])["payload_hash"] if job and job["draft_id"] else None, "received_at_utc": now()}

    def summary(self, j):
        reason = j["reason"]
        if j["outbox_id"]:
            reason = self.db.record("outbox", j["outbox_id"]).get("reason", reason)
        ticket = self.db.record("snapshots", j["snapshot_id"])["ticket"]
        return {"job_id": j["id"], "key": j["key"], "source": ticket["source"], "title": ticket["title"], "work_state": j["work_state"], "stage": j["stage"],
                "delivery_state": j["delivery_state"], "attempts": j["attempt_count"], "attempt_limit": j["limits"]["attempts"],
                "provider_calls": j["call_count"], "call_limit": j["limits"]["calls"], "version": j["version"],
                "reason": reason, "fixture": j["fixture"], "schedule": clone(j.get("schedule")), "execution_state": j.get("execution_state"), 'tool_delivery':j.get('tool_delivery'),
                'work_type':j.get('work_type'),'agent_state':j.get('agent_state'),'workspace':j.get('agent_scope',{}).get('workspace'), "evidence": "/agent " + j['id'] if j.get('work_type') else "/show " + j["id"]}

    def status(self):
        jobs = self.db.jobs()
        focused_paused = any(j["id"] == self.focus and j["work_state"] == "PAUSED" for j in jobs)
        execution_paused = bool(self.execution.active and self.db.job(self.execution.active).get('execution_state')=='PAUSED')
        return {"jobs": [self.summary(j) for j in jobs], "focus": self.focus,
                "worker": "paused" if execution_paused else "active" if self.worker.active or self.execution.active else "queue enabled" if self.worker.continuous else "paused" if focused_paused else "idle",
                "active_job": self.worker.active or self.execution.active, "queue": sum(j["work_state"] == "QUEUED" for j in jobs),
                "needs_you": sum(self.needs_you(j) for j in jobs),
                "provider_activity": clone(self.http.activity) if self.http else {},
                "build_environment":self.build_environment.status() if self.build_environment else None,
                "last_event_seq": self.db.sequence(), "health": clone(self.health), "external_changes": "See delivery states and /execution receipts for actual project effects"}

    def needs_you(self, job):
        if job.get('work_type') and self.execution.active==job['id']:
            return job.get('execution_state')=='PAUSED'
        if completed_at(self, job):
            return False
        return job['work_state'] in {'DRAFT_READY','WAITING_USER','INTERRUPTED','FAILED'} or job['delivery_state'] in {'POST_UNKNOWN','POSTED_UNVERIFIED','POSTED_MISMATCH','STALE'} or job.get('tool_delivery') in {'PROPOSED','UNKNOWN','RETURNED_UNVERIFIED','REPORTED_ERROR'}

    def capabilities(self):
        c = self.db.config()
        from .agent import TOOLS
        from .platform_support import report
        platform=report()
        return {"draft.generate": {"declared": True, "configured": c["provider"] == "ollama" or bool(os.environ.get(PROVIDERS[c["provider"]])), "route": c["provider"] + ":" + c["model"]},
                'platform':platform,
                'general_agent':{'tools':list(TOOLS),'ticket_required':False,'new_workspaces':True,'planning_and_reflection':True,
                    'public_research':True,'source_citations':True,'experience_recall':True,'start':'/build TEXT or /research TEXT',
                    'execution_available':None if c.get('build_recipe') else platform['commands']['available'],
                    'configured_backend':'offline container' if c.get('build_recipe') else 'native',
                    'availability_check':'Captured engine/image and enforced limits are checked before each container command',
                    'file_tools_available':platform['project_files']['available'],
                    'execution_limit':'Multiprocess work in a private offline copy; validated source imports' if c.get('build_recipe') else platform['commands']['boundary']},
                "ticket.requests": {"declared": True, "kinds": ["sprint", "story", "task", "bug", "support", "general request"], "input": "plain-language request or six-field import", "output": "local reviewed draft"},
                "draft.review": {"declared": True, "route": "ollama:" + c["ollama_model"], "health": self.health["ollama"]},
                "chat.local": {"declared": True, "route": c["ollama_url"] + " / " + c["chat_model"], "health": self.health["ollama"]},
                "jira.read_ticket": {"declared": True, "configured": self.offline or bool(c["site"] and c["email"] and os.environ.get("JIRA_API_TOKEN")), "health": self.health["jira"]},
                "jira.publish_comment": {"declared": True, "automatic": False, "requires": "current exact approval, target confirmation and source preflight"},
                "job.pause": {"declared": True, "boundary": True},
                "project.execution": {"declared": True,'available':platform['commands']['available'], "runtime": "Monkey durable admission" if c['admission_backend']=='monkey' else "Kist durable SML + SymbolicCaptain", "requires": "explicit project contract, exact plan authorization, current dates", "tools": ["scoped read", "whole-file write with read-back", "pinned sandboxed verifier"], "sandbox": platform['commands']['boundary']},
                "connectors": {"transports": ['stdio','streamable-http','sse','api'], "connected": list(self.connectors.connections()),
                    'admission':c['admission_backend'],'kist_required':c['admission_backend']=='kist',
                    "discovery": "exact tool schemas / explicit HTTP operations / selected OpenAPI 3 operations", "approval": "exact request hash", "automatic_retry": False, "universal_compatibility_verified": False},
                "memory": {"durable": True, "authority": "provenance retained; advisory notes do not grant tools"},
                "owned_mcp_api": {**self.gateway.status(),'managed_setup':True,'built_in_services':['asana','teams','monday','salesforce','quickbooks']},
                "specialists": {"local": True, "concurrent_limit": 2, "per_job_limit": 3, "authority": "advisory"},
                "mission.agents": {'local':True,'per_job_limit':3,'step_limit':6,'active_effects':1,'authority':'exact operator-approved mission steps; no argument substitution'},
                "evolution": {"runtime": "Kist DGM", "automatic_promotion": False, "readiness": "/evolve-status"},
                "unavailable": ["terminal.inspect", "background daemon", "automatic OAuth login", "unbounded autonomous tool agents"], "offline_demo": self.offline}

    def record_chat_call(self, scope, usage, repair):
        cid = identity("chatcall_")
        self.db.global_event("chat.call", {"scope": scope, "call_id": cid}, record=("provider_calls", cid,
            {"id": cid, "scope": "chat." + scope, "at": now(), "repair": repair, **usage}))

    async def doctor(self):
        if self.offline:
            return {"offline_fixture": True, "sockets": 0, "capabilities": self.capabilities()}
        c = self.db.config()
        try:
            tags = await self.models.catalog(c)
            names = {r["name"]: r.get("digest") for r in tags}
            self.health["ollama"] = {role: {"installed": c[key] in names, "model": c[key], "digest": names.get(c[key])} for role, key in (("chat", "chat_model"), ("triage/review", "ollama_model"), ("draft", "model")) if role != "draft" or c["provider"] == "ollama"}
        except (Refused, OSError) as exc:
            self.health["ollama"] = {"error": str(exc)}
        if c["site"] and c["email"] and os.environ.get("JIRA_API_TOKEN"):
            try:
                author = await self.jira_factory(c).author()
                self.health["jira"] = {"last_read_at": now(), "account_id": author, "publication_tested": False}
            except Refused as exc:
                self.health["jira"] = {"error": str(exc)}
        return {"version": VERSION, "state_path": str(self.db.root), "health": self.health, "capabilities": self.capabilities(),
                "recovery": self.status(), "cloud_provider_tested": False}

    async def capture_config(self):
        if self.offline:
            return
        c = self.db.config()
        # Read-only catalog freezes installed digests per job. Missing models do
        # not prevent evidence capture; later dispatch fails closed.
        try:
            tags = {r["name"]: r["digest"] for r in await self.models.catalog(c)}
        except Refused:
            return
        for name, pin in (("chat_model", "chat_digest"), ("ollama_model", "ollama_digest"), ("model", "model_digest")):
            if name == "model" and c["provider"] != "ollama":
                continue
            if not c[pin] and c[name] in tags:
                c[pin] = tags[c[name]]
        for chain in c['model_routes'].values():
            for model in chain:
                if model['provider']=='ollama' and not model['digest'] and model['model'] in tags:
                    model['digest']=tags[model['model']]
        if hasattr(self.models,'router'):
            c['model_routes']=self.models.router.freeze(c)
        self.db.configure(c)

    def publication_preview(self, job):
        require(self.db.record("snapshots", job["snapshot_id"])["ticket"]["source"] == "jira", "This request has a local draft to copy. Publishing is available for captured Jira tickets")
        draft, _, _ = self.publisher.validate_approval(job)
        token = identity()[:12]
        self.pending = {"job_id": job["id"], "key": job["key"], "hash": draft["payload_hash"], "version": job["version"], "token": token, "expires": time.monotonic() + 120}
        self.db.session(self.session_id, {"focus": self.focus, "pending_confirmation": {**self.pending, "expires_at_note": "120 seconds within this foreground session only"}})
        return {"site": job["site"], "key": job["key"], "job_id": job["id"], "candidate": draft["number"], "payload_hash": draft["payload_hash"],
                "text": draft["text"], "visibility": draft["payload"].get("visibility"), "properties": draft["payload"]["properties"],
                "confirmation": "confirm " + token + " " + job["key"], "message": "No comment sent. Type the exact confirmation above within 120 seconds"}

    async def confirm(self, token, key):
        pending = self.pending
        require(pending and pending["token"] == token and pending["key"] == key and time.monotonic() < pending["expires"], "No matching live confirmation; request an exact preview again")
        j = self.db.job(pending["job_id"])
        require(j["version"] == pending["version"], "Candidate or state changed; request a new preview")
        self.pending = None
        return await self.publisher.publish(j["id"], key, self.command("publish", j), pending["hash"])

    async def dispatch(self, operation, target=None, **arguments):
        if operation=='builder' and arguments.get('action') in {None,'status'}:
            try:
                result=await self._dispatch(operation,target,**arguments)
            except BaseException as exc:
                self.audit.observe('builder.status.refused',{'origin':ORIGIN.get(),'error':type(exc).__name__})
                raise
            self.audit.observe('builder.status.read',{'origin':ORIGIN.get(),'result':result})
            return result
        if operation in {'builder','windows-bind','windows-plan','windows-sync','approve','signoff','mission-signoff','tool-signoff','tool-resolve','connect','service-save','project','remember','setup','refresh','fetch','import','tool-request','reconcile','resolve-delivery','recover','agent-accept','build','research','agent-resolve','steer'}:
            job_id=next((j['id'] for j in self.db.jobs() if target and target in {j['id'],j['key']}),None)
            async with self.audit.run(job_id,operation):
                return await self._dispatch(operation,target,**arguments)
        return await self._dispatch(operation,target,**arguments)

    async def _dispatch(self, operation, target=None, *, note="", after="now", key=None, path=None, confirm=None, draft_hash=None, state=None, settings=None, text=None, start=None, finish=None, timezone=None, expected_version=None, objective=None, writes=None, reads=None, verify=None, expect=None, exact_hash=None, plan_file=None, server=None, name=None, args_file=None, call_id=None, verification_id=None, pointer=None, expected=None, service=None, secret=None, endpoint=None, api_version=None, company_id=None, environment=None, operation_name=None, method=None, operation_path=None, body_schema=None, action=None, port=0, workflow_server=None, target_id=None, profile_id=None):
        if ORIGIN.get() not in {'operator_repl', 'operator_dashboard'}:
            require(operation in {'services','tools','remember','recall','status','jobs','task','events','mission','mission-review','mission-run','agent','lessons','windows'},
                'This operation requires the local operator; API clients cannot grant authority')
        db = self.db
        if operation == 'builder':
            if action not in {None,'status'}:
                require(not self.offline,'Builder configuration and recovery are unavailable in offline demo')
            if action in {'setup','start','stop'}:
                if self.build_environment is None:
                    from .build_environment import BuildEnvironment
                    self.build_environment=BuildEnvironment(self)
                return self.build_environment.launch(action,path)
            if action=='recover':
                require(not self.execution.active and target and call_id,'Stop active work and name the exact job and --operation')
                job=resolve(db,target,None)
                recipe=job.get('agent_scope',{}).get('build_recipe')
                require(recipe,'This job did not capture a container build environment')
                import hashlib
                claimed={'build_'+hashlib.sha256(str(db.root/'agent-runs'/job['id']/r['id']).encode()).hexdigest()[:32]
                    for r in self.agent.records(job['id']) if r['kind']=='agent.operation' and r['action']=='run_command'}
                require(call_id in claimed,'The build operation is not bound to this job')
                if self.build_environment is None:
                    from .build_environment import BuildEnvironment
                    self.build_environment=BuildEnvironment(self)
                return self.build_environment.launch('recover',job=job,call_id=call_id)
            if action in {None,'status'}:
                if self.build_environment is not None:return self.build_environment.status()
                recipe=db.config().get('build_recipe')
                return {'configured':bool(recipe),'backend':'offline container' if recipe else 'native',
                    'image_id':recipe['image_id'] if recipe else None,
                    'engine_socket_present':Path(recipe['socket']).exists() if recipe else None,
                    'message':'Engine health is checked before each build. Existing jobs retain their captured backend.'}
            if action=='native':
                return await self.dispatch('setup',settings={'build_recipe':None})
            require(action=='use' and path,'Select a generated builder recipe with --file PATH')
            from .build_runner import BuildRunner
            from .security import strict_json
            recipe=strict_json(Path(path).expanduser().read_bytes(),limit=16000,nodes=150)
            runner=await asyncio.to_thread(BuildRunner,recipe,db.root/'builder-preflight',self.audit)
            health=await runner.preflight()
            await self.dispatch('setup',settings={'build_recipe':recipe,'build_environment':None})
            return {'configured':True,'backend':'offline container',**health,
                'message':'Builder selected for new jobs. Its environment must remain available while work runs.'}
        if operation in {'build','research'}:
            return await self.agent.start(text,operation,path)
        if operation=='lessons':
            return self.agent.experience.view(text or '')
        if operation == 'routes':
            c=db.config()
            return {'model_routes':c['model_routes'],'routing_policy':c['routing_policy'],
                'metrics':self.models.router.metrics() if hasattr(self.models,'router') else [],
                'message':'Each role uses its captured roster. Fallback is limited to transport failures. Refusals and validation failures stop; every model shares the same approvals and sandbox.',
                'measurement':'Latency, validated responses and operator-signed verified jobs; no refusal penalty and no independent-correctness claim.'}
        if operation in {'trace','audit','audit-export'}:
            job=resolve(db,target,self.focus) if target or self.focus else None
            return self.audit.export(job['id'] if job else None) if operation=='audit-export' else self.audit.view(job['id'] if job else None)
        self.audit.observe('command.received',{'operation':operation,'target':target,'origin':ORIGIN.get()},target if target and any(j['id']==target for j in db.jobs()) else None)
        if operation == 'dashboard':
            if self.dashboard is None:
                from .dashboard import Dashboard
                self.dashboard = Dashboard(self)
            if action in {None, 'start'}:
                return await self.dashboard.start(port)
            if action == 'stop':
                await self.dashboard.close()
            return self.dashboard.status(private=True)
        if operation == 'services':
            return self.services.list()
        if operation == 'service-select':
            return self.services.form(service)
        if operation == 'service-save':
            return await self.services.save(service,secret,{'name':name,'endpoint':endpoint,'api_version':api_version,
                'company_id':company_id,'environment':environment,'operation_name':operation_name,'method':method,
                'operation_path':operation_path,'body_schema':body_schema})
        if operation == 'disconnect':
            return self.services.disconnect(name)
        if operation == 'api':
            if action == 'start':
                return await self.gateway.start(port)
            if action == 'stop':
                await self.gateway.close()
                return {**self.gateway.status(),'message':'Monkey MCP/API stopped. Its temporary client credentials were removed.'}
            return self.gateway.status()
        if operation == 'connect':
            return await self.connectors.connect(name,path) if path else self.services.form(name)
        if operation == 'tools':
            connections = self.connectors.connections()
            if server:
                require(server in connections,'Unknown connection; /connect it first')
                return self.connectors.public(connections[server])
            return [self.connectors.public(row) for row in connections.values()]
        if operation == 'remember':
            selected = self.focus if ORIGIN.get() in {'operator_repl', 'operator_dashboard'} else None
            return self.learning.remember(text,resolve(db,target,selected) if target or selected else None)
        if operation == 'recall':
            return self.learning.recall(text)
        if operation == 'evolve-status':
            return await self.learning.evolution_status(resolve(db,target,self.focus) if target else None)
        if operation == "help":
            return HELP
        if operation == "status":
            return self.status()
        if operation in {"jobs", "needs_you", "completed"}:
            jobs = db.jobs()
            if state:
                jobs = [j for j in jobs if j["work_state"] == state.upper() or j["delivery_state"] == state.upper()]
            if operation == "needs_you":
                jobs = [j for j in jobs if self.needs_you(j)]
            if operation == "completed":
                jobs = [j for j in jobs if j["work_state"] == "DRAFT_READY" or j["delivery_state"].startswith("POSTED") or j.get('execution_state') == 'COMPLETED' or j.get('tool_delivery')=='SIGNED_OFF' or j.get('mission_state')=='COMPLETED']
            return [self.summary(j) for j in jobs]
        if operation == "caps":
            return self.capabilities()
        if operation == "models":
            c = db.config()
            return {"chat": {"model": c["chat_model"], "digest": c["chat_digest"], "route": c["ollama_url"]},
                    "triage_review": {"model": c["ollama_model"], "digest": c["ollama_digest"]},
                    "drafting": {"provider": c["provider"], "model": c["model"], "digest": c["model_digest"]},
                    "captured_jobs": [{"job_id": j["id"], "provider": j["recipe"]["provider"], "model": j["recipe"]["model"], "digest": j["recipe"]["model_digest"]} for j in db.jobs()]}
        if operation == "doctor":
            return await self.doctor()
        if operation == "setup":
            require(not self.offline, "Setup is unavailable in offline demo")
            c = db.config()
            c.update(settings or {})
            for name, pin in (("chat_model", "chat_digest"), ("ollama_model", "ollama_digest"), ("model", "model_digest")):
                if name in (settings or {}) and pin not in (settings or {}):
                    c[pin] = ""
            db.configure(c)
            return db.global_event("configuration.updated", {"message": "Configuration saved; existing jobs retain their captured recipe"}, self.command(operation))
        if operation in {"fetch", "import"}:
            await self.capture_config()
            issue_id = None
            if operation == "fetch":
                ticket, issue_id = await self.jira_factory(db.config()).fetch(key)
            else:
                ticket = read_import(path)
            job = db.add(ticket, fixture=self.offline, issue_id=issue_id, command=self.command(operation))
            if ORIGIN.get() in {'operator_repl', 'operator_dashboard'}:
                self.focus = job["id"]
            return self.summary(job)
        if operation in {"request", "ticket", "sprint",'task'}:
            clean_text(text, 6000)
            await self.capture_config()
            numbers = [int(j["key"][4:]) for j in db.jobs() if re.fullmatch(r"REQ-[0-9]+", j["key"])]
            key = "REQ-" + str(max([100, *numbers]) + 1)
            body = ("Draft a sprint ticket or plan from this request:\n" if operation == "sprint" else "") + text
            ticket = {"source": "local", "instance": "local://monkey", "key": key, "revision": now(),
                      "title": " ".join(text.split())[:240], "body": body}
            job = db.add(ticket, fixture=self.offline, command=self.command(operation))
            if ORIGIN.get() in {'operator_repl', 'operator_dashboard'}:
                self.focus = job["id"]
            db.session(self.session_id, {"focus": self.focus, "selected_at": now()})
            if operation == 'task':
                self.execution.save(job['id'],'task.captured',{'work_state':'WAITING_USER'},'Task saved. Choose Project for scoped local work or Tools for a connected service.')
                return self.summary(db.job(job['id']))
            if not self.execution.active and (not self.worker.task or self.worker.task.done()):
                result = self.worker.start(job["id"], continuous=True)
                db.global_event("work.requested", result, self.command("work"))
            queued = self.worker.continuous
            return {"message": "Captured " + key + ". " + ("I'll draft and review it here. You can keep talking." if queued else "Queued. Use /work after the current attempt to continue."), "job_id": job["id"], "source": "local", "external_changes": "none"}
        if operation == "work":
            require(not self.execution.active, "Project work is active; its exact controls remain available")
            result = self.worker.start(continuous=True)
            return db.global_event("work.requested", result, self.command(operation))
        if operation == "usage":
            j = resolve(db, target, self.focus) if target else None
            rows = db.records("provider_calls", j["id"] if j else None)
            return {"calls": len(rows), "cost": "unknown", "scopes": {s: sum(r["scope"] == s for r in rows) for s in {r["scope"] for r in rows}}, "records": rows,
                    "jira_http": [] if self.offline else self.http.measurements}
        if operation == "run" and not target and not self.focus:
            jobs = self.worker.eligible()
            require(jobs, "No queued work")
            target = jobs[0]["id"]
        job = resolve(db, target or "", self.focus)
        jid = job["id"]
        require(expected_version is None or job["version"] == expected_version, "Ticket changed while you were editing; reopen it before saving")
        if operation=='agent': return self.agent.view(job)
        if operation=='steer': return self.agent.steer(job,text)
        if operation=='agent-continue': return self.agent.continuation(job)
        if operation=='agent-accept': return self.agent.accept(job,exact_hash,note)
        if operation=='agent-resolve': return self.agent.resolve_operations(job,note)
        if operation == 'windows-bind':
            return self.windows.bind(job, server, workflow_server, target_id, profile_id, note)
        if operation == 'windows':
            return self.windows.view(job)
        if operation == 'windows-plan':
            return self.windows.prepare(job, action, json_file(args_file,45000) if args_file else {})
        if operation == 'windows-sync':
            return self.windows.sync(job, call_id)
        if operation == 'tool':
            return self.connectors.tool_plan(job,server,name,json_file(args_file,45000),self.command(operation,job))
        if operation == 'tool-request':
            return await self.connectors.propose(job['id'],text,server)
        if operation == 'tool-run':
            return self.execution.launch(job,'CALLING_TOOL',lambda:self.connectors.run(job['id'],exact_hash,note))
        if operation == 'tool-result':
            return self.connectors.view(job)
        if operation in {'tool-signoff','tool-resolve'}:
            return await self.connectors.attest(job,call_id,verification_id,pointer,expected,note,operation=='tool-resolve')
        if operation == 'mission':
            supplied = json_file(plan_file,250000) if plan_file else None
            return self.execution.launch(job,'MISSION_PLANNING',lambda:self.missions.plan(jid,text,supplied))
        if operation == 'mission-answer':
            require(not self.execution.active and not self.worker.active,'Wait for the current work boundary before clarifying a mission')
            answer = self.missions.answer(job,text)
            return self.execution.launch(self.db.job(jid),'MISSION_PLANNING',lambda:self.missions.plan(jid,answer['objective'],answer=answer))
        if operation in {'mission-review','agents'}:
            return self.missions.view(job)
        if operation == 'mission-authorize':
            return self.missions.authorize(job,exact_hash,note)
        if operation == 'mission-run':
            require(job.get('mission_state')=='AUTHORIZED' and not job.get('mission_run_id'),'Mission is unapproved or already consumed; inspect it without replaying steps')
            self.missions.current(job)
            return self.execution.launch(job,'MISSION_RUNNING',lambda:self.missions.run(jid))
        if operation == 'mission-signoff':
            return await self.missions.signoff(job,exact_hash,note)
        if operation == 'delegate':
            return self.learning.delegate(job,text)
        if operation == 'improve':
            return await self.learning.improve(job,text)
        if operation == "project":
            from .command_text import split
            writes = split(writes) if isinstance(writes, str) else writes
            reads = split(reads) if isinstance(reads, str) else reads
            expect = [expect] if isinstance(expect, str) else expect
            return await self.execution.attach(job, path, objective, writes, reads, split(verify) if isinstance(verify, str) else verify, expect, self.command(operation, job))
        if operation == "admit-rule":
            return self.execution.admit_rule(job, path, note, self.command(operation, job))
        if operation == "execution":
            return self.execution.view(job)
        if operation == "explore":
            return self.execution.launch(job, "EXPLORING", lambda: self.execution.explore(job["id"]))
        if operation == "plan":
            supplied = None
            if plan_file:
                import json
                p = Path(plan_file).expanduser()
                require(p.is_file() and not p.is_symlink() and p.stat().st_size < 300000, "Choose a bounded regular plan JSON file")
                supplied = json.loads(p.read_bytes())
            return self.execution.launch(job, "PLANNING", lambda: self.execution.plan(job["id"], supplied))
        if operation == "authorize":
            return self.execution.authorization(job, exact_hash, note, self.command(operation, job))
        if operation == "execute":
            return self.execution.launch(job, "EXECUTING", lambda: self.execution.execute(job["id"]))
        if operation == "signoff":
            return await self.execution.signoff(job, exact_hash, note, self.command(operation, job))
        if self.execution.active == job["id"] and operation in {"pause", "resume", "cancel", "return"}:
            if operation == "pause":
                self.execution.pause = True
                return self.execution.save(job["id"], "execution.pause_requested", {}, "Pause requested at the next completed tool boundary; the current operation is still running")
            if operation == "resume":
                require(job.get("execution_state") == "PAUSED", "Project worker has not reached its pause boundary yet")
                require((job.get('work_type') or job.get("plan_authorization_id") or job.get('tool_call_id') or job.get('mission_authorization_id')) and (job.get("schedule") or {}).get("status") != "RETURNED", "Dates or authorization changed; stop this run and re-plan from its saved effects")
                self.execution.wake.set()
                return {"message": "Resume requested; authorization will be checked at the boundary"}
            if operation in {"cancel", "return"}:
                self.execution.task.cancel()
        if operation == "dates":
            return self.scheduler.view(job)
        if operation == "schedule":
            return self.scheduler.assign(job, start, finish, timezone or db.config()["timezone"], note, self.command(operation, job))
        if operation == "return":
            return self.scheduler.return_for_dates(job, note, self.command(operation, job))
        if operation == "focus":
            self.focus = jid
            db.session(self.session_id, {"focus": jid, "selected_at": now()})
            return {"message": "Selected " + job["key"] + " / " + jid, "job_id": jid}
        if operation == "show":
            if job.get('work_type'): return self.agent.view(job)
            return {"job": job, "source": db.record("snapshots", job["snapshot_id"]),
                    "attempts": db.records("attempts", jid), "drafts": db.records("drafts", jid), "reviews": db.records("reviews", jid),
                    "approvals": db.records("approvals", jid), "outbox": db.records("outbox", jid), "evidence_seq": db.sequence()}
        if operation in {"events", "why"}:
            events = db.events(job_id=jid)
            if operation == "why":
                return {"job": self.summary(job), "recorded_reason": job["reason"], "review": db.record("reviews", job["review_id"]) if job["review_id"] else None,
                        "events": events[-12:], "evidence_snapshot_seq": db.sequence()}
            return events
        if operation == "draft":
            require(job["draft_id"], "No completed candidate. Provisional text is not a draft")
            return {"job_id": jid, "candidate": db.record("drafts", job["draft_id"]), "review": db.record("reviews", job["review_id"]) if job["review_id"] else None,
                    "delivery_state": job["delivery_state"]}
        command = self.command(operation, job)
        if operation == "run":
            if job.get('work_type'): return self.agent.continuation(job)
            require(not self.execution.active and not job.get("contract_id"), "Project work uses /explore, /plan and /execute")
            require(not self.worker.task or self.worker.task.done(), "One worker is already active")
            require(job["work_state"] == "QUEUED", "Job is not queued; inspect /show and /resume, /retry or /recover")
            _, receipt = db.change(jid, job["version"], "run.requested", {}, command=command, payload={"message": "One candidate attempt requested"})
            self.worker.start(jid)
            self.focus = jid
            return receipt
        if operation == "approve":
            require(not job.get("contract_id") or job.get("execution_signoff_id"), "Project work needs its own verified /signoff before approving the response")
            if job.get('contract_id'):
                require(completed_at(self, job), 'Project sign-off is stale; inspect the current result')
            clean_text(note, 2000)
            return self.publisher.approve(job, note, command)
        if operation == "publish":
            return await self.publisher.publish(jid, confirm, command, draft_hash)
        if operation == "reconcile":
            return await self.publisher.reconcile(jid)
        if operation == "resolve-delivery":
            clean_text(note, 2000)
            require(job["delivery_state"] in {"POST_UNKNOWN", "POSTED_UNVERIFIED", "POSTED_MISMATCH"}, "No uncertain delivery to annotate")
            _, receipt = db.change(jid, job["version"], "delivery.operator_evidence", {}, command=command,
                payload={"message": "Operator evidence recorded; delivery remains blocked from resend", "note": note, "operator": self.operator})
            return receipt
        if operation == "refresh":
            require(db.record("snapshots", job["snapshot_id"])["ticket"]["source"] == "jira", "Refresh uses the Jira adapter. Import an updated snapshot for other ticket sources")
            require(job["delivery_state"] not in ACTIVE_DELIVERY and job["work_state"] not in {"CANCELLED", "REJECTED", "INTERRUPTED"}, "Resolve delivery/interruption before refreshing")
            fresh, issue_id = await self.jira_factory(job["recipe"]).fetch(job["key"])
            require(fresh["instance"] == job["site"] and fresh["key"] == job["key"], "Refresh target mismatch")
            require(db.job(jid)["version"] == job["version"], "Job advanced during refresh; retry the read")
            old = db.record("snapshots", job["snapshot_id"])
            sid = identity("snapshot_")
            snapshot = {"id": sid, "schema_version": 1, "ticket": fresh, "content_hash": digest(fresh), "material_hash": material_hash(fresh),
                        "captured_at": now(), "origin": job["site"], "issue_id": issue_id, "server_updated": fresh["revision"]}
            changed = snapshot["material_hash"] != old["material_hash"] or issue_id != job["issue_id"]
            identity_only = snapshot["material_hash"] == old["material_hash"] and job["issue_id"] is None
            update = {} if not changed else {"snapshot_id": sid, "issue_id": issue_id, "delivery_state": "STALE", "approval_id": None,
                "draft_id": None, "review_id": None, "triage": None, "operation_token": None, "resume_stage": None,
                "work_state": "INTERRUPTED" if job["work_state"] == "RUNNING" else "QUEUED", "reason": "Source refreshed; prior candidate/approval stale"}
            if identity_only:
                update = {"issue_id": issue_id, "identity_snapshot_id": sid, "approval_id": None,
                          "delivery_state": "DRAFT" if job["draft_id"] else "NONE",
                          "reason": "Jira identity verified against unchanged material evidence; exact approval required"}
            _, receipt = db.change(jid, job["version"], "ticket.refreshed", update, command=command, records=[("snapshots", sid, snapshot)],
                                   payload={"message": "Material source changed; review required" if changed else "New observation retained; material evidence unchanged", "snapshot_id": sid})
            return receipt
        if operation == "pause":
            require(after in {"now", "triage", "draft", "review"}, "Choose now, triage, draft or review")
            require(job["work_state"] not in {"CANCELLED", "REJECTED"}, "Job is closed")
            if job["work_state"] == "RUNNING":
                update = {"pause_after": after}
                message = "Pause requested " + ("at the next boundary" if after == "now" else "after " + after) + ". It is not paused yet"
                kind = "pause.requested"
            else:
                update = {"work_state": "PAUSED", "pause_after": None, "resume_state": job["work_state"], "resume_stage": job["resume_stage"]}
                message, kind = "Job is now paused at its existing boundary", "pause.applied"
        elif operation == "resume":
            require(job.get("schedule", {}).get("status") != "RETURNED", "This ticket needs new dates before resuming. Use /schedule or the Dates view")
            require(job["work_state"] == "PAUSED", "Job is not paused")
            resumed = job["resume_state"] or "QUEUED"
            if resumed == "QUEUED":
                require(not self.worker.task or self.worker.task.done(), "Another job is active; resume after its boundary")
            update = {"work_state": resumed, "pause_after": None, "resume_state": None}
            message, kind = "Pause released; " + ("execution requested at the retained boundary" if resumed == "QUEUED" else resumed), "pause.resumed"
        elif operation in {"cancel", "reject"}:
            if operation == "reject":
                clean_text(note, 2000)
            update = {"work_state": "CANCELLED" if operation == "cancel" else "REJECTED", "operation_token": None, "pause_after": None}
            if job.get('work_type'): update.update(agent_state='CANCELLED' if operation=='cancel' else 'REJECTED')
            if job["delivery_state"] not in ACTIVE_DELIVERY:
                update.update(approval_id=None, delivery_state="STALE" if job["draft_id"] else "NONE")
            message, kind = "Further local stages stopped. Delivery state remains recorded separately", "job." + operation + "led" if operation == "cancel" else "job.rejected"
        elif operation == "recover":
            clean_text(note, 2000)
            require(job["work_state"] == "INTERRUPTED" and job["delivery_state"] not in ACTIVE_DELIVERY, "Only interrupted local work can recover; reconcile delivery separately")
            update = {"work_state": "WAITING_USER", "operation_token": None, "resume_stage": None, "reason": "Interruption acknowledged; inspect evidence before bounded retry"}
            message, kind = "Interrupted attempt acknowledged; limits retained. Use /retry with feedback", "recovery.acknowledged"
        elif operation == "retry":
            if job.get('work_type'): return self.agent.steer(job,note)
            if job.get("contract_id"):
                require(not self.execution.active, "Pause or cancel active project work before re-planning")
                clean_text(note, 2000)
                return self.execution.save(jid, "execution.feedback", {"execution_feedback": [*job.get("execution_feedback", []), note][-12:],
                    "plan_authorization_id": None, "execution_signoff_id": None, "execution_state": "READY_TO_EXPLORE"},
                    "Direction saved; /explore and /plan use it with the original remaining budgets", command=command)
            clean_text(note, 2000)
            require(job["work_state"] not in {"CANCELLED", "REJECTED", "INTERRUPTED"} and job["delivery_state"] not in ACTIVE_DELIVERY, "Job cannot retry; resolve interruption or delivery first")
            require(job["attempt_count"] < job["limits"]["attempts"] and job["call_count"] < job["limits"]["calls"], "Original attempt/provider-call budget exhausted; no reset")
            update = {"work_state": "QUEUED", "approval_id": None, "delivery_state": "STALE" if job["draft_id"] else "NONE",
                      "resume_stage": None, "pause_after": None, "feedback": [*job["feedback"], note][-12:], "reason": "Operator requested another bounded attempt"}
            message, kind = "Bounded retry queued with retained feedback; /run or /work", "retry.requested"
            if job["work_state"] == "RUNNING":
                update = {"retry_requested": True, "feedback": [*job["feedback"], note][-12:]}
                message = "Revision direction recorded for the next candidate after review. Current request is still running"
        else:
            raise Refused("Unknown command; /help")
        _, receipt = db.change(jid, job["version"], kind, update, command=command, payload={"message": message, "note": note})
        if operation == "resume" and resumed == "QUEUED":
            self.worker.start(jid, continuous=job.get("resume_continuous", False))
        if operation in {"cancel", "reject"} and self.worker.active == jid:
            self.worker.continuous = False
            self.worker.task.cancel()
        if operation in {"cancel", "reject"}:
            for task in list(self.learning.tasks):
                if getattr(task,'monkey_job',None) == jid:
                    task.cancel()
        return receipt

    async def close(self):
        failures=[]
        self.worker.continuous=False
        async def settle(action):
            try:await action()
            except BaseException as exc:failures.append(exc)
        async def background():
            for task in list(self.background):task.cancel()
            if self.background:await asyncio.gather(*self.background,return_exceptions=True)
        try:
            if self.dashboard:await settle(self.dashboard.close)
            for action in (self.gateway.close,background,self.learning.close,self.execution.stop,self.worker.stop,background):
                await settle(action)
            if self.build_environment:await settle(self.build_environment.close)
            if self.http:await settle(self.http.close)
            await settle(self.agent.close)
            if not self.db.failed:
                self.db.session(self.session_id, {"ended_at": now(), "focus": self.focus, "pending_confirmation": None})
        finally:
            self.db.close()
        if failures:raise BaseExceptionGroup('Monkey shutdown encountered errors; all cleanup steps were attempted',failures)
