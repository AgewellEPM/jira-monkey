"""Exercise an installed Monkey executable with private fixtures and signed export.

This checks the real console entry point, not native Terminal/PowerShell UI.
No model generation or business-service writes are performed.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import time


def qualification_environment():
    # Retain Windows home discovery without inheriting model credentials or
    # Python startup hooks into the installed console under qualification.
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'HOME', 'USER', 'USERNAME', 'LOGNAME', 'LANG', 'TMPDIR', 'TEMP', 'TMP',
        'SYSTEMROOT', 'WINDIR', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH'}}
    env['PYTHONNOUSERSITE'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if sys.pycache_prefix:
        env['PYTHONPYCACHEPREFIX'] = sys.pycache_prefix
    return env


def checked_process(argv, *, cwd, env, timeout):
    """Collect a trusted CLI check, retaining its parent until timeout cleanup."""
    options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
               else {'start_new_session': True})
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **options)
    timed_out = False
    cleanup = None
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'nt':
                taskkill = Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'
                stopped = subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                                         capture_output=True, timeout=30)
                cleanup = {'exit_code': stopped.returncode,
                           'stdout': stopped.stdout.decode(errors='replace'),
                           'stderr': stopped.stderr.decode(errors='replace')}
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                cleanup = {'process_group_signal': 'SIGKILL'}
            if process.poll() is None:
                process.kill()
            stdout, stderr = process.communicate(timeout=10)
        return {'exit_code': 'timeout' if timed_out else process.returncode,
                'stdout': stdout.decode(errors='replace'), 'stderr': stderr.decode(errors='replace'),
                'timeout_cleanup': cleanup}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def run(monkey,python,output,project):
    os.umask(0o077)
    root=Path(output).expanduser().absolute()
    root.mkdir(mode=0o700,parents=True,exist_ok=False)
    monkey=str(Path(monkey).expanduser().resolve())
    python=str(Path(python).expanduser().resolve())
    env=qualification_environment()
    records=[]
    result={'schema':'monkey.cli-qualification.v1','platform':platform.platform(),'monkey':monkey,
        'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'physical_terminal_ui_tested':False,
        'model_generation_tested':False,'business_writes':0}
    def command(*args,success=True,plain=False):
        argv=[monkey,'--state',str(root/'state'),*args]
        start=time.perf_counter()
        outcome=checked_process(argv,cwd=root,env=env,timeout=90)
        record={'argv':argv,**outcome,'elapsed_ms':(time.perf_counter()-start)*1000}
        records.append(record)
        (root/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        if outcome['exit_code']=='timeout':
            raise TimeoutError('CLI check exceeded 90 seconds; inspect '+str(root/'commands.json'))
        if (outcome['exit_code']==0)!=success:
            raise AssertionError('Unexpected CLI exit; inspect '+str(root/'commands.json'))
        return record['stdout'].strip() if plain else json.loads(record['stdout'])
    try:
        result['version']=command('--version',plain=True)
        result['capabilities']=command('caps')
        assert command('status')['jobs']==[]
        snapshot=root/'snapshot.json'
        snapshot.write_text(json.dumps({'source':'local','instance':'local://monkey','key':'QUAL-101',
            'revision':'private-fixture','title':'Correct addition','body':'Return the sum of the two supplied numbers.'}))
        jid=command('import',str(snapshot))['job_id']
        result['job_id']=jid
        if project:
            workspace=root/'workspace';workspace.mkdir()
            (workspace/'calc.py').write_text('def add(a, b):\n    return a - b\n')
            (workspace/'test_calc.py').write_text('from calc import add\nassert add(2, 3)==5\nassert add(-3, 2)==-1\nprint("two fixed arithmetic checks passed")\n')
            current=dt.datetime.now(dt.timezone.utc)
            command('schedule',jid,'--start',(current-dt.timedelta(minutes=1)).isoformat(),
                '--finish',(current+dt.timedelta(hours=1)).isoformat(),'--timezone','UTC')
            command('project',jid,'--path',str(workspace),'--objective','Return the sum of the supplied numbers',
                '--write','calc.py','--verify',shlex.join([python,'test_calc.py']),'--expect','calc.py=return a + b')
            assert command('explore',jid)['execution_state']=='READY_TO_PLAN'
            plan_path=root/'proposal.json'
            plan_path.write_text(json.dumps({'understanding':'The pinned verifier expects addition; the source subtracts.',
                'questions':[],'edits':[{'path':'calc.py','content':'def add(a, b):\n    return a + b\n','reason':'Correct addition'}],
                'learned_rules':[]}))
            planned=command('plan',jid,'--file',str(plan_path))
            plan=next(r for r in planned['project_records'] if r['kind']=='project.plan')
            command('authorize',jid,'--hash',plan['plan_hash'],'--note','Inspected private fixture plan and fixed verifier')
            executed=command('execute',jid)
            assert executed['execution_state']=='AWAITING_SIGNOFF',executed['reason']
            outcome=next(r for r in executed['project_records'] if r['kind']=='project.execution')
            effects=[r for r in executed['project_records'] if r['kind']=='project.effect']
            assert effects and all(e['evidence']['runtime']=='monkey-host-v1' for e in effects)
            assert effects[-1]['evidence']['result']['test']['exit_code']==0
            command('signoff',jid,'--hash',outcome['result_hash'],'--note','Inspected actual source and fixed check output')
            assert command('status')['jobs'][0]['execution_state']=='COMPLETED'
            result['project']={'admission':'monkey-host-v1','state':'COMPLETED','effects':len(effects),
                'source_sha256':hashlib.sha256((workspace/'calc.py').read_bytes()).hexdigest(),
                'verifier_output':effects[-1]['evidence']['result']['test']['output']}
        exported=command('audit-export',jid)
        verified=command('audit-verify',exported['path'],'--fingerprint',exported['signer_fingerprint'])
        result['export']=exported;result['verification']=verified
        bundle=json.loads(Path(exported['path']).read_text())
        bundle['entries'][0]['kind']='tampered-fixture'
        tampered=root/'tampered-export.json';tampered.write_text(json.dumps(bundle))
        command('audit-verify',str(tampered),'--fingerprint',exported['signer_fingerprint'],success=False)
        result['tampering_rejected']=True
        result['passed']=True
    except BaseException as exc:
        result.update(passed=False,error=type(exc).__name__+': '+str(exc))
        raise
    finally:
        result['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'passed':result.get('passed',False),'report':str(root/'result.json')}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monkey',required=True)
    parser.add_argument('--python',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--project',action='store_true',help='Exercise the currently supported macOS project verifier')
    args=parser.parse_args()
    run(args.monkey,args.python,args.output,args.project)
