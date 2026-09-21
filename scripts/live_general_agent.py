"""Real Ollama and real tools; retained private state; no service writes."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monkey.app import App


async def run(args):
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=bool(args.resume),mode=0o700)
    workspace=root/'workspace';workspace.mkdir(mode=0o700,exist_ok=bool(args.resume))
    app=App(root/'state')
    started=time.monotonic()
    try:
        if args.resume:
            app.audit.verify()
            if args.resolve_note:
                await app.dispatch('agent-resolve',args.resume,note=args.resolve_note)
            request=await app.dispatch('agent-continue',args.resume)
        else:
            config={'agent_max_calls':24,'agent_max_seconds':1200,'request_timeout':180}
            if args.model:
                config['model_routes']={role:[{'provider':'ollama','model':args.model,'digest':''}] for role in ('planning','execution','reflection')}
            app.db.configure(config)
            request=await app.dispatch(args.mode,text=args.objective,path=str(workspace))
        jid=request['job_id'];seq=0
        print(json.dumps({'job_id':jid,'workspace':str(workspace)}),flush=True)
        while app.execution.task and not app.execution.task.done():
            for event in app.db.events(seq,job_id=jid):
                seq=event['seq']
                if event['data'].get('message'): print(event['kind']+': '+event['data']['message'][:180],flush=True)
            await asyncio.sleep(.5)
        await app.execution.task
        result=app.agent.view(app.db.job(jid))
        result['elapsed_seconds']=time.monotonic()-started
        result['validation_kind']='real local Ollama with real file, command and public research tools'
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:result[k] for k in ('state','reason','model_calls','tool_calls','elapsed_seconds')}),flush=True)
        print('Result: '+str(root/'result.json'),flush=True)
        app.audit.verify()
        return 0 if result['state']=='AWAITING_REVIEW' else 1
    finally: await app.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--mode',choices=['build','research'],default='build')
    parser.add_argument('--model',help='Benchmark an exact installed model for planning/execution/reflection; no download')
    parser.add_argument('--resume')
    parser.add_argument('--resolve-note')
    parser.add_argument('objective',nargs='?')
    raise SystemExit(asyncio.run(run(parser.parse_args())))
