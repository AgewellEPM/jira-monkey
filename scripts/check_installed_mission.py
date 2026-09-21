"""Exercise the installed Monkey CLI with real local agents and a private MCP ledger."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main(command, report_path):
    payload = Path(__file__).resolve().parents[1]
    report = {'surface':str(command),'cwd':'/private/tmp','synthetic_fixture':True,
        'external_services_contacted':False,'live_local_agents':True,
        'approval':'Explicit fixture harness assertions; not production human review',
        'planning':'Operator fixture file; live model planning is tested separately'}
    try:
        with tempfile.TemporaryDirectory(prefix='monkey-installed-mission-',dir='/private/tmp') as folder:
            root = Path(folder)
            def cli(*args, ok=True):
                result = subprocess.run([str(command),'--state',str(root/'state'),*args],
                    cwd='/private/tmp',text=True,capture_output=True,timeout=300)
                value = json.loads(result.stdout)
                if ok:
                    assert result.returncode==0,value
                else:
                    assert result.returncode!=0,value
                return value
            report['version'] = subprocess.check_output([str(command),'--version'],cwd='/private/tmp',text=True).strip()
            config = root/'mcp.json'
            ledger = root/'ledger.json'
            config.write_text(json.dumps({'command':sys.executable,
                'args':[str(payload/'tests/mcp_fixture_server.py'),str(ledger)],'sandbox':{'write':[str(ledger)]}}))
            connection = cli('connect','fixture','--file',str(config))
            report['tools'] = [t['name'] for t in connection['tools']]
            title = 'Installed mission fixture Alpha'
            objective = ('Create exactly one private fixture task titled '+title+'. Use fixture.add_task with that title '
                'and lose_response false, then fixture.list_tasks with empty arguments. The ledger starts empty. '
                'list_tasks returns JSON text with tasks[0].title; verify it equals the exact requested title. '
                'This is only a private local fixture; no other service or authority is needed.')
            job = cli('task',objective)
            jid = job['job_id']
            clock = dt.datetime.now(dt.timezone.utc)
            dates = cli('schedule',jid,'--start',(clock-dt.timedelta(minutes=1)).isoformat(),
                '--finish',(clock+dt.timedelta(hours=1)).isoformat(),'--timezone','UTC')
            report['schedule'] = dates['schedule']
            proposal = {'understanding':objective,'questions':[],
                'steps':[{'agent':'worker','purpose':'Create the exact private fixture task','server':'fixture',
                    'tool':'add_task','arguments_json':json.dumps({'title':title,'lose_response':False})},
                    {'agent':'reviewer','purpose':'Read the persisted ledger to verify the task','server':'fixture',
                     'tool':'list_tasks','arguments_json':'{}'}],
                'checks':[{'step':2,'pointer':'/parsedContent/0/tasks/0/title','equals_json':json.dumps(title)}]}
            plan_file = root/'mission.json'
            plan_file.write_text(json.dumps(proposal))
            planned = cli('mission',jid,'--file',str(plan_file))
            assert planned['mission_state']=='PLAN_READY',planned
            plan = next(r for r in planned['mission_records'] if r['kind']=='mission.plan')
            assert [s['request']['arguments'] for s in plan['steps']]==[{'title':title,'lose_response':False},{}]
            status = cli('status')
            assert status['worker']=='idle' and status['jobs'][0]['execution_state']=='MISSION_PLAN_READY',status
            cli('mission-authorize',jid,'--hash',plan['plan_hash'],'--note','Harness inspected both exact fixture requests and title check.')
            assert not ledger.exists()
            print('Installed CLI: exact fixture mission authorized; starting local agents.',flush=True)
            run = cli('mission-run',jid)
            report['mission'] = run
            assert run['mission_state']=='AWAITING_SIGNOFF',run['reason']
            result = next(r for r in run['mission_records'] if r['kind']=='mission.result')
            assert len(result['calls'])==2 and all(c['passed'] for c in result['checks']),result
            assert json.loads(ledger.read_text())==[{'title':title}]
            signed = cli('mission-signoff',jid,'--hash',result['result_hash'],'--note','Harness inspected each recorded call and the exact private ledger outcome.')
            report['signoff'] = signed['signoff']
            report['replay_refusal'] = cli('mission-run',jid,ok=False)
            assert len(json.loads(ledger.read_text()))==1
            recalled = cli('recall',jid)
            report['recall_count'] = len(recalled['records'])
            final = cli('status')
            assert final['worker']=='idle' and final['needs_you']==0,final
            assert final['jobs'][0]['tool_delivery']=='MISSION_SIGNED_OFF',final
            report['final_status'] = final
            report['evolution'] = cli('evolve-status')
            trace=cli('trace',jid)
            assert trace['valid'] and trace['runs']
            trace_kinds={entry['kind'] for entry in trace['trace']}
            assert {'mcp.requested','mcp.returned','model.selected','model.completed','run.finished','run.sealed'}<=trace_kinds,trace_kinds
            exported=cli('audit-export',jid)
            # The verifier must work with no access to the original state/key.
            checked=subprocess.run([str(command),'--state',str(root/'never-opened'),'audit-verify',exported['path'],
                '--fingerprint',exported['signer_fingerprint']],cwd='/private/tmp',text=True,capture_output=True,timeout=20)
            assert checked.returncode==0,checked.stdout
            assert json.loads(checked.stdout)['valid']
            assert not (root/'never-opened').exists()
            report['signed_trace']={k:exported[k] for k in ('sha256','signer_fingerprint','entries')}
            report['signed_trace']['independent_public_key_verification']=True
            report['signed_trace']['observed_kinds']=sorted(trace_kinds)
            report_path.parent.mkdir(parents=True,exist_ok=True)
            retained=report_path.with_name(report_path.stem+'-trace-'+exported['sha256'][:12]+'.json')
            fd=os.open(retained,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as stream:
                stream.write(Path(exported['path']).read_bytes())
            report['signed_trace']['retained_bundle']=str(retained)
            assert not list((root/'state/connector-runs').iterdir())
            report['confined_children_cleaned_up']=True
            manifest = json.loads((payload/'installation.json').read_text())
            mismatch = [name for name,sha in manifest['files'].items()
                if not (payload/name).is_file() or hashlib.sha256((payload/name).read_bytes()).hexdigest()!=sha]
            report.update(manifest_files=len(manifest['files']),manifest_mismatches=mismatch)
            assert not mismatch,mismatch
            report['passed'] = True
    except Exception as exc:
        report.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        report['observed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k in {'version','passed','error','observed_at','manifest_files','manifest_mismatches'}}),flush=True)
    return 0 if report.get('passed') else 1


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command',type=Path,default=Path.home()/'bin/Monkey')
    parser.add_argument('--report',type=Path,required=True)
    args = parser.parse_args()
    sys.exit(main(args.command,args.report))
