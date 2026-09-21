"""Exercise installed general work across an upgrade and reject unsafe downgrades.

Decisions are explicit local fixtures. Source edits, checkers, SQLite recovery,
console commands and signed exports use the actual installed packages.
No inference, UI automation or business-service request is performed.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


STAGE = '''import asyncio
import json
from pathlib import Path
import sys
import monkey
from monkey.app import App

async def main():
    root=Path(sys.argv[1]);root.mkdir(mode=0o700)
    app=App(root/'state',offline=True)
    plan=('plan',{'steps':['Implement addition.','Check positive and negative inputs.'],
        'checks':['Two independent assertion cases must pass.']})
    async def work(folder,actions):
        folder.mkdir(mode=0o700)
        choices=iter(actions)
        async def local(c,m,p,instruction,data,shape=None,**kwargs):
            action,arguments=next(choices)
            return {'action':action,'arguments':arguments,'reason':'Installed qualification fixture',
                'evidence_refs':data['evidence_refs'][-3:]},{'provider':'fixture','model':'scripted-decisions','fixture':True}
        app.models.local=local
        request=await app.dispatch('build',text='Build and verify addition',path=str(folder))
        await app.execution.task
        return app.db.job(request['job_id'])
    try:
        complete=await work(root/'completed-project',[plan,
            ('write_file',{'path':'calc.py','content':'def add(a, b):\\n    return a + b\\n'}),
            ('write_file',{'path':'check.py','content':'from calc import add\\nassert add(2, 3)==5\\nassert add(-3, 2)==-1\\nprint("two fixed arithmetic checks passed")\\n'}),
            ('run_command',{'argv':['python','check.py']}),
            ('reflect',{'findings':['The actual checker passed both cases.'],'adjustments':[],
                'procedure':['Check positive and negative arithmetic cases after changing the implementation.']}),
            ('finish',{'summary':'Implemented and verified addition.','checks':['Both recorded assertion cases passed.'],'source_ids':[]})])
        assert complete['agent_state']=='AWAITING_REVIEW',complete['reason']
        result=app.execution.record(complete,'agent_result_id')
        paused=await work(root/'stopped-project',[plan,('ask',{'question':'Fixture stopping point before further actions.'})])
        assert paused['agent_state']=='NEEDS_INPUT',paused['reason']
        calls=[row for row in app.agent.observations(complete['id']) if row['action']=='run_command']
        assert calls[-1]['result']['exit_code']==0
        assert 'two fixed arithmetic checks passed' in calls[-1]['result']['output']
        report={'version':monkey.VERSION,'module':monkey.__file__,'python':sys.executable,
            'complete':{'job_id':complete['id'],'result_hash':result['result_hash'],'call_count':complete['call_count'],
                'runtime_captured':'application_runtime' in complete['agent_scope']},
            'paused':{'job_id':paused['id'],'call_count':paused['call_count'],'deadline':paused['agent_deadline'],
                'limits':paused['agent_limits']},'verifier_output':calls[-1]['result']['output'],
            'database_version':app.db.db.execute('PRAGMA user_version').fetchone()[0]}
        app.audit.verify()
        (root/'captured.json').write_text(json.dumps(report,indent=2)+'\\n')
        print(json.dumps(report),flush=True)
    finally:
        await app.close()
asyncio.run(main())
'''


def run(args):
    os.umask(0o077)
    root=Path(args.output).expanduser().absolute()
    root.mkdir(parents=True,mode=0o700,exist_ok=False)
    fixture=root/'fixture_stage.py';fixture.write_text(STAGE)
    paths={key:str(Path(getattr(args,key)).expanduser().resolve()) for key in
        ('old_python','old_monkey','new_python','new_monkey')}
    env={key:value for key,value in os.environ.items() if key in
        {'PATH','HOME','USER','LOGNAME','LANG','TMPDIR','TEMP','TMP','SYSTEMROOT','WINDIR'}}
    env['PYTHONNOUSERSITE']='1'
    commands=[]
    report={'schema':'monkey.agent-upgrade-qualification.v1','started_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'model_generation_tested':False,'physical_terminal_ui_tested':False,'business_writes':0,
        'fixture_source_sha256':hashlib.sha256(STAGE.encode()).hexdigest(),'executables':paths}

    def command(argv,success=True):
        started=time.perf_counter()
        process=subprocess.run(argv,cwd=root,env=env,capture_output=True,timeout=90)
        record={'argv':argv,'exit_code':process.returncode,'elapsed_ms':(time.perf_counter()-started)*1000,
            'stdout':process.stdout.decode(errors='replace'),'stderr':process.stderr.decode(errors='replace')}
        commands.append(record)
        (root/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert (process.returncode==0)==success,'Unexpected command exit; inspect commands.json'
        return json.loads(record['stdout']) if success else record['stdout']+record['stderr']

    def cli(which,folder,*argv,success=True):
        return command([paths[which+'_monkey'],'--state',str(folder/'state'),*argv],success)

    try:
        legacy=root/'legacy';fresh=root/'fresh'
        older=command([paths['old_python'],str(fixture),str(legacy)])
        assert older['database_version']==2 and not older['complete']['runtime_captured']
        report['legacy_capture']=older
        jid=older['paused']['job_id']
        before=cli('new',legacy,'agent',jid)
        refusal=cli('new',legacy,'agent-continue',jid,success=False)
        assert 'no captured application runtime' in refusal
        after=cli('new',legacy,'agent',jid)
        assert before['model_calls']==after['model_calls']==older['paused']['call_count']
        assert before['deadline']==after['deadline']==older['paused']['deadline']
        assert before['limits']==after['limits']==older['paused']['limits']
        assert not after['runtime']['captured']
        accepted=cli('new',legacy,'agent-accept',older['complete']['job_id'],'--hash',older['complete']['result_hash'],
            '--note','Inspected retained source and fixed arithmetic checker after upgrading the installed runtime')
        assert accepted['accepted'] and not accepted['runtime']['captured']
        assert accepted['runtime']['execution_runtime_hash'] is None
        assert not accepted['runtime']['new_execution_authorized']
        report['legacy_review']=accepted['runtime']

        captured=command([paths['new_python'],str(fixture),str(fresh)])
        assert captured['database_version']==3 and captured['complete']['runtime_captured']
        report['fresh_capture']=captured
        refusal=cli('old',fresh,'status',success=False)
        assert 'Database is newer than this application' in refusal
        report['older_runtime_rejected']=True
        current=cli('new',fresh,'agent',captured['paused']['job_id'])
        assert current['model_calls']==captured['paused']['call_count']
        assert current['deadline']==captured['paused']['deadline']
        accepted=cli('new',fresh,'agent-accept',captured['complete']['job_id'],'--hash',captured['complete']['result_hash'],
            '--note','Inspected actual installed source and fixed checker output under the captured application runtime')
        assert accepted['accepted'] and accepted['runtime']['matches_running_code']
        report['fresh_review']=accepted['runtime']
        for name,folder,jid in (('legacy',legacy,older['complete']['job_id']),('fresh',fresh,captured['complete']['job_id'])):
            exported=cli('new',folder,'audit-export',jid)
            verified=cli('new',folder,'audit-verify',exported['path'],'--fingerprint',exported['signer_fingerprint'])
            assert verified['valid']
            report[name+'_export']={'export':exported,'verification':verified}
        report['passed']=True
    except BaseException as exc:
        report.update(passed=False,error=type(exc).__name__+': '+str(exc))
        raise
    finally:
        report['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'passed':report.get('passed',False),'report':str(root/'result.json')}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('old-python','old-monkey','new-python','new-monkey','output'):
        parser.add_argument('--'+name,required=True)
    run(parser.parse_args())
