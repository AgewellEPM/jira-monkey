"""Verify the installed Monkey REPL, owned MCP/API and file-free credential setup."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import datetime as dt
from unittest.mock import AsyncMock

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

async def main(report_path, command=None, payload=None, manifest_path=None):
    os.umask(0o077)
    command=(command or Path.home()/'bin/Monkey').absolute()
    payload=(payload or Path(__file__).resolve().parents[1]).resolve()
    sys.path.insert(0,str(payload))
    from monkey import VERSION
    import monkey
    from monkey.app import App
    from monkey.ui import History,repl

    report={'surface':str(command),'synthetic_fixture':True,'external_services_contacted':False,
        'payload':str(payload),'python':sys.executable,
        'native_keyboard_automation':False,'ui_check':'Actual installed PromptSession with pipe input'}
    process=None
    try:
        assert Path(monkey.__file__).resolve().parent.parent==payload, 'Imported payload differs from selected installation'
        manifest=json.loads((manifest_path or payload/'installation.json').read_text())
        files=manifest.get('files')
        if files is None:
            files={name:sha for name,sha in manifest['source_hashes'].items()
                   if name.startswith('monkey/') or name=='jira_monkey.py'}
        assert files and 'monkey/cli.py' in files and 'monkey/gateway.py' in files
        mismatch=[name for name,sha in files.items() if hashlib.sha256((payload/name).read_bytes()).hexdigest()!=sha]
        assert not mismatch,mismatch
        report.update(manifest_files=len(files),manifest_mismatches=mismatch)
        with tempfile.TemporaryDirectory(prefix='monkey-owned-installed-',dir=Path(tempfile.gettempdir()).resolve()) as folder:
            root=Path(folder)
            version=await asyncio.create_subprocess_exec(str(command),'--version',stdout=asyncio.subprocess.PIPE)
            raw,_=await version.communicate()
            report['version']=raw.decode().strip()
            assert version.returncode==0 and report['version']==VERSION, 'CLI and selected payload versions differ'
            process=await asyncio.create_subprocess_exec(str(command),'--state',str(root/'state'),'--json','repl',cwd=root,
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
            async def reply(predicate):
                async with asyncio.timeout(15):
                    while True:
                        line=await process.stdout.readline()
                        assert line,'Installed REPL stopped unexpectedly'
                        try: row=json.loads(line)
                        except ValueError: continue
                        if row.get('type')=='reply' and predicate(row.get('value')):
                            return row['value']
            async def send(line):
                process.stdin.write((line+'\n').encode())
                await process.stdin.drain()
            await reply(lambda v:isinstance(v,dict) and 'worker' in v)
            await send('/api start')
            started=await reply(lambda v:isinstance(v,dict) and v.get('running') is True)
            client_path=Path(started['client_config'])
            settings=json.loads(client_path.read_text())
            report['client_config_mode']=oct(client_path.stat().st_mode&0o777)
            assert report['client_config_mode']=='0o600'
            async with httpx.AsyncClient(trust_env=False) as http:
                anonymous=await http.get(started['api_url']+'/status')
                assert anonymous.status_code==401
                status=await http.get(started['api_url']+'/status',headers=settings['api']['headers'])
                assert status.json()['worker']=='idle'
                forbidden=await http.post(started['api_url']+'/rpc',headers=settings['api']['headers'],
                    json={'operation':'approve','arguments':{'approved':True}})
                assert forbidden.status_code==400
            config=settings['mcpServers']['monkey']
            async with httpx2.AsyncClient(headers=config['headers'],trust_env=False) as http:
                async with Client(streamable_http_client(config['url'],http_client=http),cache=None) as client:
                    listed=(await client.list_tools()).model_dump(mode='json',by_alias=True)
                    report['mcp_tools']=[t['name'] for t in listed['tools']]
                    assert set(report['mcp_tools'])=={
                        'monkey_status','monkey_jobs','monkey_services','monkey_tools',
                        'monkey_task','monkey_events','monkey_recall','monkey_remember',
                        'monkey_propose_tool','monkey_mission','monkey_mission_result',
                        'monkey_run_mission','monkey_windows_result','monkey_agent_result','monkey_lessons'}
                    captured=await client.call_tool('monkey_task',{'text':'Owned gateway installed fixture; no external service action'})
                    assert not captured.is_error
                    note=await client.call_tool('monkey_remember',{'text':'Installed gateway fixture recall marker'})
                    assert not note.is_error
                    recalled=await client.call_tool('monkey_recall',{'text':'fixture recall marker'})
                    assert not recalled.is_error
                    report['mcp_capture_and_recall']=True
            await send('/status')
            state=await reply(lambda v:isinstance(v,dict) and 'worker' in v and len(v.get('jobs',[]))==1)
            assert state['worker']=='idle' and state['jobs'][0]['work_state']=='WAITING_USER'
            await send('/api stop')
            await reply(lambda v:isinstance(v,dict) and v.get('running') is False)
            assert not client_path.exists()
            await send('/quit')
            await asyncio.wait_for(process.wait(),10)
            assert process.returncode==0
            report['repl_gateway_cleanup']=True
            print('Installed REPL and real MCP/API checks passed.',flush=True)

            app=App(root/'setup')
            app.doctor=AsyncMock(return_value={})
            history=History(str(root/'setup-history'))
            async def until(predicate):
                async with asyncio.timeout(5):
                    while not predicate(): await asyncio.sleep(.01)
            secret='synthetic-installed-credential-privacy-test'
            try:
                with create_pipe_input() as pipe:
                    session=PromptSession(input=pipe,output=DummyOutput(),history=history)
                    task=asyncio.create_task(repl(app,session=session))
                    try:
                        await until(lambda:session.app.is_running)
                        pipe.send_text('/connect asana\n')
                        await until(lambda:'access token' in ''.join(t[1] for t in to_formatted_text(session.message)))
                        assert session.is_password
                        pipe.send_text(secret+'\n')
                        await until(lambda:'asana' in app.connectors.connections())
                        pipe.send_text('/quit\n')
                        await asyncio.wait_for(task,3)
                    finally:
                        if not task.done(): task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                assert secret not in (root/'setup-history').read_text()
                assert secret not in '\n'.join(session.history.get_strings())
                assert secret not in json.dumps(app.db.events())
                assert secret not in json.dumps(app.audit.entries())
                assert app.audit.verify()['valid']
                report['generated_asana_tools']=len(app.connectors.connections()['asana']['tools'])
                report['hidden_credential_setup']=True
            finally:
                await app.close()

            mismatch=[name for name,sha in files.items() if hashlib.sha256((payload/name).read_bytes()).hexdigest()!=sha]
            assert not mismatch,mismatch
            report.update(manifest_mismatches=mismatch,passed=True)
    except Exception as exc:
        report.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        if process and process.returncode is None:
            process.send_signal(signal.SIGINT)
            try: await asyncio.wait_for(process.wait(),8)
            except TimeoutError:
                process.kill()
                await process.wait()
        report['observed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)
    return 0 if report.get('passed') else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--monkey',type=Path,help='Exact installed console to qualify; never inferred from the source version')
    parser.add_argument('--payload',type=Path,help='Installed directory containing the monkey package')
    parser.add_argument('--manifest',type=Path,help='Selected installation or deployment-bundle manifest')
    args=parser.parse_args()
    sys.exit(asyncio.run(main(args.report,args.monkey,args.payload,args.manifest)))
