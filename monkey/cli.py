from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
import sys
import tempfile

from . import VERSION
from .common import Refused, safe
from .command_text import split


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused(message + "; /help")


def parser():
    p = Parser(description="Monkey · local coding, research, tickets and requests")
    p.add_argument("--state", type=Path, default=Path.home() / ".jira-monkey")
    p.add_argument("--json", action="store_true", help="JSON lines in the non-TTY REPL")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", parser_class=Parser)
    sub.add_parser("repl")
    for op in ('build','research'):
        action=sub.add_parser(op)
        action.add_argument('text',nargs='+')
        action.add_argument('--path',help='Existing workspace; omitted creates a private Monkey workspace')
    for op in ('agent','agent-continue','agent-accept','agent-resolve','steer'):
        action=sub.add_parser(op)
        action.add_argument('target')
        if op=='steer': action.add_argument('text',nargs='+')
        if op=='agent-accept':
            action.add_argument('--hash',dest='exact_hash',required=True)
        if op in {'agent-accept','agent-resolve'}:
            action.add_argument('--note',required=True)
    lessons=sub.add_parser('lessons')
    lessons.add_argument('text',nargs='*')
    mission = sub.add_parser('mission')
    mission.add_argument('target')
    mission.add_argument('text',nargs='*')
    mission.add_argument('--file',dest='plan_file')
    answer = sub.add_parser('mission-answer')
    answer.add_argument('target')
    answer.add_argument('text',nargs='+')
    for op in ('mission-review','mission-run','mission-authorize','mission-signoff','agents'):
        action = sub.add_parser(op)
        action.add_argument('target')
        if op in {'mission-authorize','mission-signoff'}:
            action.add_argument('--hash',dest='exact_hash',required=True)
            action.add_argument('--note',required=True)
    connection = sub.add_parser('connect')
    connection.add_argument('name',nargs='?')
    connection.add_argument('--file',dest='path')
    sub.add_parser('services')
    sub.add_parser('routes')
    from .model_routing import ROLES
    route=sub.add_parser('route')
    route.add_argument('role',choices=ROLES)
    route.add_argument('--provider',choices=['ollama','claude','openai','deepseek'],default='ollama')
    route.add_argument('--model',required=True)
    route.add_argument('--append',action='store_true')
    for op in ('trace','audit','audit-export'):
        audit=sub.add_parser(op)
        audit.add_argument('target',nargs='?')
    verify_audit=sub.add_parser('audit-verify')
    verify_audit.add_argument('path')
    verify_audit.add_argument('--fingerprint',required=True)
    disconnect = sub.add_parser('disconnect')
    disconnect.add_argument('name')
    api = sub.add_parser('api')
    api.add_argument('action',choices=['start','stop','status'],nargs='?',default='status')
    api.add_argument('--port',type=int,default=0)
    dashboard = sub.add_parser('dashboard', help='Open a local web workspace sharing this foreground Monkey session')
    dashboard.add_argument('action', choices=['start', 'status', 'stop'], nargs='?', default='start')
    dashboard.add_argument('--port', type=int, default=0)
    builder = sub.add_parser('builder', help='Select a captured offline build environment for new general jobs')
    builder.add_argument('action', choices=['status','setup','start','stop','use','native','recover'], nargs='?', default='status')
    builder.add_argument('target', nargs='?')
    builder.add_argument('--file','--root', dest='path')
    builder.add_argument('--operation', dest='call_id')
    tool_list = sub.add_parser('tools')
    tool_list.add_argument('--server')
    tool = sub.add_parser('tool')
    tool.add_argument('target')
    tool.add_argument('--server',required=True)
    tool.add_argument('--name',required=True)
    tool.add_argument('--args-file',required=True)
    tool_run = sub.add_parser('tool-run')
    tool_run.add_argument('target')
    tool_run.add_argument('--hash',dest='exact_hash',required=True)
    tool_run.add_argument('--note',required=True)
    tool_result = sub.add_parser('tool-result')
    tool_result.add_argument('target')
    windows = sub.add_parser('windows')
    windows.add_argument('target')
    binding = sub.add_parser('windows-bind')
    binding.add_argument('target')
    for name in ('server', 'workflow-server', 'target-id', 'profile-id', 'note'):
        binding.add_argument('--'+name, required=True)
    from .windows import ACTIONS
    windows_plan = sub.add_parser('windows-plan')
    windows_plan.add_argument('target')
    windows_plan.add_argument('action', choices=ACTIONS)
    windows_plan.add_argument('--args-file')
    windows_sync = sub.add_parser('windows-sync')
    windows_sync.add_argument('target')
    windows_sync.add_argument('--call', dest='call_id', required=True)
    for op in ('tool-signoff','tool-resolve'):
        attest = sub.add_parser(op)
        attest.add_argument('target')
        attest.add_argument('--call',dest='call_id',required=True)
        attest.add_argument('--verification',dest='verification_id',required=True)
        attest.add_argument('--pointer',required=True)
        attest.add_argument('--equals',dest='expected',required=True,help='Exact expected JSON value in a separately recorded verification response')
        attest.add_argument('--note',required=True)
    for op in ('remember','recall','delegate','improve','tool-request'):
        action = sub.add_parser(op)
        if op in {'delegate','improve','tool-request'}:
            action.add_argument('target')
        if op=='tool-request':
            action.add_argument('--server')
        action.add_argument('text',nargs='+')
    evolution = sub.add_parser('evolve-status')
    evolution.add_argument('target',nargs='?')
    project = sub.add_parser("project", description="Explicit project scope and verification. Missing fields request clarification; no edits run.")
    project.add_argument("target")
    project.add_argument("--path")
    project.add_argument("--objective")
    project.add_argument("--write", action="append", dest="writes")
    project.add_argument("--read", action="append", dest="reads")
    project.add_argument("--verify", help="Exact installed executable and existing verifier file; shell syntax is not evaluated")
    project.add_argument("--expect", action="append", help="Editable path=expected result text")
    for name in ("explore", "plan", "execute", "execution", "authorize", "signoff"):
        action = sub.add_parser(name)
        action.add_argument("target")
        if name == "plan":
            action.add_argument("--file", dest="plan_file", help="Inspect an operator-supplied proposal JSON instead of calling Ollama")
        if name in {"authorize", "signoff"}:
            action.add_argument("--hash", dest="exact_hash", required=True)
            action.add_argument("--note", required=True)
    rule = sub.add_parser("admit-rule")
    rule.add_argument("target")
    rule.add_argument("--path", required=True)
    rule.add_argument("--note", required=True)
    schedule = sub.add_parser("schedule")
    schedule.add_argument("target")
    schedule.add_argument("--start", required=True)
    schedule.add_argument("--finish", required=True)
    schedule.add_argument("--timezone")
    schedule.add_argument("--note", default="")
    dates = sub.add_parser("dates")
    dates.add_argument("target")
    returned = sub.add_parser("return")
    returned.add_argument("target")
    returned.add_argument("--note", required=True)
    for name in ("request", "ticket", "sprint",'task'):
        request = sub.add_parser(name)
        request.add_argument("text", nargs="+", help="Describe the ticket or request to draft locally")
    for name in ("help", "status", "caps", "models", "doctor", "work", "quit", "multiline"):
        sub.add_parser(name)
    demo = sub.add_parser("demo")
    demo.add_argument("--repl", action="store_true")
    demo.add_argument("--scenario", choices=["pass", "response-loss", "readback-error", "no-match", "revise", "revise-once", "pause"], default="response-loss")
    jobs = sub.add_parser("jobs")
    jobs.add_argument("--state", dest="filter_state")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("key")
    ingest = sub.add_parser("import")
    ingest.add_argument("path")
    for name in ("run", "usage"):
        s = sub.add_parser(name)
        s.add_argument("target", nargs="?")
    for name in ("show", "draft", "events", "focus", "pause", "resume", "cancel", "retry", "reject", "approve", "publish", "reconcile", "recover", "refresh", "resolve-delivery"):
        s = sub.add_parser(name)
        s.add_argument("target")
        if name in {"retry", "reject", "approve", "recover", "resolve-delivery"}:
            s.add_argument("--note", required=True)
        if name == "pause":
            s.add_argument("--after", choices=["now", "triage", "draft", "review"], default="now")
        if name == "publish":
            s.add_argument("--confirm", required=True)
            s.add_argument("--draft-hash")
    s = sub.add_parser("confirm")
    s.add_argument("token")
    s.add_argument("key")
    setup = sub.add_parser("setup", description="Public configuration only. Credentials come from JIRA_API_TOKEN or provider API keys in the launch environment. No model downloads.")
    for name in ("model", "ollama-model", "chat-model", "site", "email", "chat-digest", "ollama-digest", "model-digest", "timezone", "kist-binary", "kist-source"):
        setup.add_argument("--" + name)
    setup.add_argument("--provider", choices=["ollama", "claude", "openai", "deepseek"])
    setup.add_argument('--routing-policy',choices=['ordered','measured'])
    setup.add_argument('--admission-backend',choices=['monkey','kist'])
    setup.add_argument("--review-policy", choices=["human", "queue"])
    setup.add_argument("--max-attempts", type=int)
    for name in ('agent-max-calls','agent-max-tools','agent-max-seconds','agent-context','request-timeout'):
        setup.add_argument('--'+name,type=int)
    setup.add_argument("--project", action="append", dest="projects")
    return p


EXACT = {"schedule", "dates", "return", "request", "ticket", "sprint", "repl", "help", "status", "caps", "models", "doctor", "work", "quit", "multiline", "demo", "jobs", "fetch", "import", "run", "usage", "show", "draft", "events", "focus", "pause", "resume", "cancel", "retry", "reject", "approve", "publish", "reconcile", "recover", "refresh", "setup", "confirm", "resolve-delivery"}
EXACT |= {"project", "explore", "plan", "authorize", "execute", "execution", "signoff", "admit-rule"}
EXACT |= {'connect','tools','tool','tool-run','tool-result','tool-request','remember','recall','delegate','improve','evolve-status'}
EXACT |= {'tool-signoff','tool-resolve'}
EXACT |= {'windows','windows-bind','windows-plan','windows-sync'}
EXACT |= {'task'}
EXACT |= {'services','disconnect','api'}
EXACT |= {'dashboard','builder'}
EXACT |= {'trace','audit','audit-export'}
EXACT |= {'audit-verify'}
EXACT |= {'route','routes'}
EXACT |= {'mission','mission-answer','mission-review','mission-authorize','mission-run','mission-signoff','agents'}
EXACT |= {'build','research','agent','agent-continue','agent-accept','agent-resolve','steer','lessons'}


async def execute(app, args):
    values = vars(args).copy()
    op = values.pop("command")
    for name in ("state", "json"):
        values.pop(name, None)
    if op == "setup":
        return await app.dispatch(op, settings={k: v for k, v in values.items() if v is not None})
    if op == 'route':
        from .common import require
        from .model_routing import validate_routes
        candidate={'provider':values['provider'],'model':values['model'],'digest':''}
        if candidate['provider']=='ollama':
            tags=await app.models.catalog(app.db.config())
            installed=next((r for r in tags if r.get('name')==candidate['model']),None)
            require(installed and installed.get('digest'),'Choose an exact installed local model; no download performed')
            candidate['digest']=installed['digest']
        config=app.db.config()
        routes=config['model_routes']
        routes[values['role']]=[*routes.get(values['role'],[]),candidate] if values['append'] else [candidate]
        validate_routes(routes)
        await app.dispatch('setup',settings={'model_routes':routes})
        return await app.dispatch('routes')
    if op == 'audit-verify':
        from .audit import verify_file
        return await asyncio.to_thread(verify_file,values['path'],values['fingerprint'])
    if op == "confirm":
        return await app.confirm(values["token"], values["key"])
    if op == "demo":
        from .demo import demonstration
        return await demonstration(values["scenario"])
    if "filter_state" in values:
        values["state"] = values.pop("filter_state")
    if op in {"request", "ticket", "sprint",'task','remember','recall','delegate','improve','tool-request','mission','mission-answer','build','research','steer','lessons'}:
        values["text"] = " ".join(values["text"])
    return await app.dispatch(op, **values)


async def line(app, text):
    if not text.lstrip().startswith("/"):
        names = app.connectors.connections()
        selected = [name for name in names if re.search(r'\b(?:in|on|using|via|with)\s+'+re.escape(name)+r'\b',text,re.I)]
        if selected and re.match(r'\s*(?:(?:please|can you|could you)\s+)?(?:create|update|look up|find|send|assign|move|close)\b',text,re.I):
            if len(selected)>1:
                return {'message':'Choose one connected service for this request: '+', '.join(selected),'action_taken':False}
            captured = await app.dispatch('task',text=text)
            result = await app.dispatch('tool-request',captured['job_id'],text=text,server=selected[0])
            return {**result,'captured_task':captured}
        from .intake import is_request, general_request
        if is_request(text):
            return await app.dispatch("request", text=text)
        general=general_request(text)
        if general and not re.search(r'\s--[a-z]',text):
            return await app.dispatch(general,text=text)
        if app.focus and app.db.job(app.focus).get('work_type') and re.match(r'\s*(?:instead|actually|change it|also|make it|try again|use |add |now |continue with)',text,re.I):
            return await app.dispatch('steer',app.focus,text=text)
    words = split(text)
    if not words:
        return None
    verb = words[0].lstrip("/")
    if not words[0].startswith("/") and len(words) > 1 and not any(w.startswith("--") for w in words):
        from .conversation import fast
        identifiers = re.search(r"\bJM-[a-zA-Z0-9]+\b|\b[A-Za-z][A-Za-z0-9_]*-[0-9]+\b|\b[0-9a-f]{32}\b", text)
        if not identifiers and fast(text):
            return await app.conversation.handle(text)
    if verb in EXACT or words[0].startswith("/"):
        try:
            args = parser().parse_args([verb, *words[1:]])
        except Refused:
            if words[0].startswith("/") or any(w.startswith("--") for w in words):
                raise
            return await app.conversation.handle(text)
        return await execute(app, args)
    return await app.conversation.handle(text)


def display(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


async def run(args):
    if args.command=='audit-verify':
        from .audit import verify_file
        display(await asyncio.to_thread(verify_file,args.path,args.fingerprint))
        return 0
    if args.command == "demo":
        from .demo import demo_app, demonstration
        if not args.repl:
            display(await demonstration(args.scenario))
            return 0
        from .ui import repl
        with tempfile.TemporaryDirectory(prefix="jira-monkey-repl-demo-", dir=Path(tempfile.gettempdir()).resolve()) as root:
            app, _ = demo_app(root, args.scenario, delay=1)
            try:
                return await repl(app, json_mode=args.json)
            finally:
                await app.close()
    from .app import App
    app = App(args.state)
    try:
        if args.command in {None, "repl"}:
            from .ui import repl
            return await repl(app, json_mode=args.json)
        result = await execute(app, args)
        if args.command=='connect' and not args.path:
            if sys.stdin.isatty() and sys.stdout.isatty():
                from .ui import repl
                return await repl(app,initial_form=result['form'])
            display({'message':'Open Monkey and use /connect'+(' '+args.name if args.name else '')+' for guided setup with hidden credential entry. No file is required.'})
            return 0
        if args.command=='api' and args.action=='start':
            display(result)
            await app.gateway.task
            return 0
        if args.command == 'dashboard' and args.action == 'start':
            print(json.dumps({'type': 'dashboard', 'value': result}) if args.json else 'Dashboard: ' + result['dashboard_url'], flush=True)
            from .ui import repl
            return await repl(app, json_mode=args.json)
        if args.command=='builder' and args.action in {'setup','start','recover'}:
            print(json.dumps({'type':'builder','value':result}) if args.json else result['message'],flush=True)
            from .ui import repl
            return await repl(app,json_mode=args.json)
        if args.command=='builder' and args.action=='stop' and app.build_environment:
            await app.build_environment.task
            result=app.build_environment.status()
        if args.command in {"explore", "plan", "execute",'tool-run','mission','mission-run','mission-answer','build','research','agent-continue','run'} and app.execution.task:
            await app.execution.task
            from .conversation import resolve
            target=getattr(args,'target',None) or app.focus
            result = app.execution.view(resolve(app.db, target, app.focus))
            if args.command == 'tool-run':
                result = app.connectors.view(resolve(app.db,args.target,app.focus))
            elif args.command in {'mission','mission-run','mission-answer'}:
                result = app.missions.view(resolve(app.db,args.target,app.focus))
            elif args.command in {'build','research','agent-continue','run'}:
                result=app.agent.view(resolve(app.db,target,app.focus))
        if args.command == 'delegate' and app.learning.tasks:
            await asyncio.gather(*list(app.learning.tasks))
            result = app.learning.recall(args.target)
        if args.command in {"run", "work", "resume", "request", "ticket", "sprint"} and app.worker.task:
            if app.worker.continuous:
                while app.worker.task and not app.worker.task.done():
                    if not app.worker.active and not app.worker.eligible():
                        app.worker.continuous = False
                        break
                    await asyncio.sleep(0.05)
            await app.worker.task
            result = app.status()
        display(result)
        return 0
    finally:
        await app.close()


def main(argv=None):
    os.umask(0o077)
    try:
        args = parser().parse_args(argv)
        return asyncio.run(run(args))
    except (Refused, OSError, ValueError, KeyError, TypeError) as exc:
        display({"error": safe(str(exc)) if isinstance(exc, Refused) else type(exc).__name__})
        return 1
    except KeyboardInterrupt:
        display({"message": "Foreground execution ended. Retained work and delivery can be inspected on restart"})
        return 130
