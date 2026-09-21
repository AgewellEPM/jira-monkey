"""Live local mission planning and tool agents, confined to a private fixture."""
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
from monkey.browser import Browser


async def main():
    report = {'synthetic_fixture':True,'live_local_models':True,'external_services_contacted':False,
        'approval':'Fixture harness checks the exact proposed actions and predicates before authorizing; not a production human review'}
    folder = tempfile.TemporaryDirectory(prefix='monkey-live-mission-',dir='/private/tmp')
    root = Path(folder.name)
    app = App(root/'state')
    samples = []
    async def finish():
        while not app.execution.task.done():
            started = time.perf_counter()
            await app.dispatch('status')
            samples.append((time.perf_counter()-started)*1000)
            await asyncio.sleep(.03)
        await app.execution.task
    try:
        await app.capture_config()
        config = root/'mcp.json'
        config.write_text(json.dumps({'command':sys.executable,'args':[str(ROOT/'tests/mcp_fixture_server.py'),str(root/'ledger.json')],'sandbox':{'write':[str(root/'ledger.json')]}}))
        await app.dispatch('connect',name='fixture',path=str(config))
        title = 'Mission fixture Alpha'
        body = ('Use only fixture tools: first add_task with title "Mission fixture Alpha" and lose_response false, '
            'then list_tasks with empty arguments to verify it. The private ledger starts empty. '
            'The documented fixture list_tasks response is JSON text: {"tasks":[{"title":"Mission fixture Alpha"}]}. '
            'Use exactly two steps and check step 2 at /parsedContent/0/tasks/0/title equals the JSON string "Mission fixture Alpha". '
            'Assign one worker for creation and one reviewer for the separate read-back. No other tool, service or task is needed.')
        jid = app.db.add({'source':'local','instance':'local://monkey','key':'MISSION-101','revision':'live-fixture',
            'title':'Create and verify one local fixture task','body':body},fixture=True)['id']
        clock = dt.datetime.now(dt.timezone.utc)
        await app.dispatch('schedule',jid,start=(clock-dt.timedelta(minutes=1)).isoformat(),finish=(clock+dt.timedelta(hours=1)).isoformat(),timezone='UTC')
        app.focus = jid
        await app.dispatch('mission',jid,text=body)
        await finish()
        plan = app.execution.record(app.db.job(jid),'mission_plan_id')
        report['plan'] = plan
        assert not plan['questions'] and len(plan['steps'])==2,plan
        assert [s['request']['tool'] for s in plan['steps']]==['add_task','list_tasks'],plan
        first,second = [s['request'] for s in plan['steps']]
        assert first['server']==second['server']=='fixture'
        assert first['arguments'].get('title')==title and first['arguments'].get('lose_response',False) is False
        assert second['arguments']=={}
        assert plan['checks']==[{'step':2,'pointer':'/parsedContent/0/tasks/0/title','expected':title}],plan['checks']
        await app.dispatch('mission-authorize',jid,exact_hash=plan['plan_hash'],note='Harness checked exactly one private task creation and its separately recorded verification predicate.')
        await app.dispatch('mission-run',jid)
        await finish()
        job = app.db.job(jid)
        assert job['mission_state']=='AWAITING_SIGNOFF',job['reason']
        _,result = app.missions.verify(job)
        report['result'] = result
        assert json.loads((root/'ledger.json').read_text())==[{'title':title}]
        assert Browser(app).completed_at(job) is None
        await app.dispatch('mission-signoff',jid,exact_hash=result['result_hash'],note='Harness inspected the exact private ledger, calls and approved response checks.')
        assert Browser(app).completed_at(app.db.job(jid)) is not None
        report['agents'] = app.missions.view(app.db.job(jid))
        report['passed'] = True
    except Exception as exc:
        report.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        report['model_calls'] = app.db.records('provider_calls')
        report['status_samples'] = len(samples)
        report['status_p95_ms'] = sorted(samples)[int(len(samples)*.95)] if samples else None
        report['observed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (ROOT/'docs/live-mission-smoke.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in {'model_calls','plan','result','agents'}}),flush=True)
        await app.close()
        folder.cleanup()
    return 0 if report.get('passed') else 1


if __name__=='__main__':
    sys.exit(asyncio.run(main()))
