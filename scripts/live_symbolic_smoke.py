"""Actual local Ollama + Captain + SML + MCP proof in a private fixture only."""
import asyncio
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from monkey.app import App


async def main():
    folder = tempfile.TemporaryDirectory(prefix='monkey-live-symbolic-',dir='/private/tmp')
    root = Path(folder.name)
    project = root/'project'
    project.mkdir()
    (project/'calc.py').write_text('def add(a, b):\n    return a - b\n')
    (project/'test_calc.py').write_text('from calc import add\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\nprint("two fixed arithmetic assertions passed")\n')
    (project/'AGENTS.md').write_text('Preserve the add(a, b) interface. Never edit the verifier.\n')
    app = App(root/'state')
    app.db.configure({'kist_binary':str(Path.home()/'bin/kist-current'),'kist_source':'/Volumes/PRO-G40/kist-loops','admission_backend':'kist'})
    report = {'synthetic_fixture':True,'live_local_models':True,'external_services_contacted':False,
        'fixture_approval':'explicit harness assertions, not an independent reviewer or a production sign-off'}
    samples = []
    try:
        await app.capture_config()
        job = app.db.add({'source':'local','instance':'local://monkey','key':'REQ-101','revision':'live-fixture',
            'title':'Fix the arithmetic fixture','body':'The add function must return the sum, including negative inputs.'},fixture=True)
        jid = job['id']
        app.focus = jid
        await app.dispatch('project',jid,path=str(project),objective='Make add(a,b) return a+b and preserve its interface.',writes=['calc.py'],reads=[],
            verify='/opt/homebrew/bin/python3.11 test_calc.py',expect=['calc.py=return a + b'])
        clock = dt.datetime.now(dt.timezone.utc)
        await app.dispatch('schedule',jid,start=(clock-dt.timedelta(minutes=1)).isoformat(),finish=(clock+dt.timedelta(hours=1)).isoformat(),timezone='UTC')
        await app.dispatch('explore',jid)
        await app.execution.task
        await app.dispatch('plan',jid)
        while not app.execution.task.done():
            started = time.perf_counter()
            await app.dispatch('status')
            samples.append((time.perf_counter()-started)*1000)
            await asyncio.sleep(.025)
        await app.execution.task
        plan = app.execution.record(app.db.job(jid),'plan_id')
        report['local_plan'] = plan['proposal']
        assert not plan['proposal']['questions'], plan['proposal']
        assert [(e['path'],e['content']) for e in plan['proposal']['edits']] == [('calc.py','def add(a, b):\n    return a + b\n')], plan['proposal']
        await app.dispatch('authorize',jid,exact_hash=plan['plan_hash'],note='Fixture harness checked the exact addition edit against the fixed expected bytes.')
        await app.dispatch('execute',jid)
        await app.execution.task
        contract,result,effects = app.execution.verify_result(app.db.job(jid))
        await app.dispatch('signoff',jid,exact_hash=result['result_hash'],note='Synthetic harness observed exact source read-back and the pinned verifier result.')
        report['execution'] = {'state':app.db.job(jid)['execution_state'],'result_hash':result['result_hash'],
            'effects':[{'kind':e['evidence']['result'].get('kind'),'runtime':e['evidence']['runtime'],
                'receipt_kinds':[r['kind'] for r in e['evidence']['receipts']], 'result':e['evidence']['result']} for e in effects],
            'captain':contract['captain']}
        await app.dispatch('remember',text='The arithmetic fixture uses a pinned verifier and exact SML write receipts.')
        await app.dispatch('delegate',jid,text='Review the recorded arithmetic work and list any remaining uncertainty, citing recorded event references.')
        await asyncio.gather(*list(app.learning.tasks))
        agents = [r for r in app.db.records('artifacts',jid) if r.get('kind')=='agent.result']
        assert len(agents)==1,app.db.events(job_id=jid)[-5:]
        report['specialist'] = agents[0]
        config = root/'mcp.json'
        config.write_text(json.dumps({'command':sys.executable,'args':[str(ROOT/'tests/mcp_fixture_server.py'),str(root/'ledger.json')],'sandbox':{'write':[str(root/'ledger.json')]}}))
        await app.dispatch('connect',name='fixture',path=str(config))
        proposal = await app.dispatch('tool-request',jid,text='Use fixture lookup_ticket to look up exactly REQ-101. Do not create a task.')
        toolplan = proposal['tool_plan']
        assert toolplan['server']=='fixture' and toolplan['tool']=='lookup_ticket' and toolplan['arguments']=={'key':'REQ-101'},toolplan
        await app.dispatch('tool-run',jid,exact_hash=toolplan['plan_hash'],note='Harness approves the exact local fixture lookup for REQ-101.')
        await app.execution.task
        assert app.db.job(jid)['tool_delivery']=='RETURNED_UNVERIFIED',app.db.job(jid)['reason']
        report['tool_proposal'] = {'tool':toolplan['tool'],'arguments':toolplan['arguments'],'delivery':app.db.job(jid)['tool_delivery']}
        report['passed'] = True
    except Exception as exc:
        report.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        report['model_calls'] = app.db.records('provider_calls')
        report['status_samples'] = len(samples)
        report['status_p95_ms'] = sorted(samples)[int(len(samples)*.95)] if samples else None
        report['observed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (ROOT/'docs/live-symbolic-smoke.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in {'model_calls','specialist','execution','local_plan'}}),flush=True)
        await app.close()
        folder.cleanup()
    return 0 if report.get('passed') else 1


if __name__=='__main__':
    sys.exit(asyncio.run(main()))
