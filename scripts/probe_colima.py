"""Qualify one owned headless Linux backend and always stop its guest afterward."""
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

ROOT=Path.home()/'.local/share/jira-monkey-validation'
PROFILE='monkey'
COLIMA='/opt/homebrew/bin/colima'
DOCKER='/usr/local/bin/docker'
ENV={k:os.environ[k] for k in ('HOME','USER','LOGNAME','TMPDIR','PATH') if k in os.environ}


def guests():
    raw=subprocess.check_output(['ps','-A','-o','pid=,comm='],text=True)
    result=[]
    for row in raw.splitlines():
        pid,executable=row.strip().split(None,1)
        name=Path(executable).name
        if name.startswith('qemu-system-') or name=='krunkit': result.append({'pid':int(pid),'executable':executable})
        elif name=='limactl':
            arguments=subprocess.check_output(['ps','-p',pid,'-o','args='],text=True).strip()
            if ' hostagent ' in arguments: result.append({'pid':int(pid),'executable':executable})
    return result


async def command(argv,timeout=300):
    process=await asyncio.create_subprocess_exec(*argv,env=ENV,stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
    try:
        raw,_=await asyncio.wait_for(process.communicate(),timeout)
        text=raw.decode(errors='replace')
        print(text[-10000:],flush=True)
        return {'argv':argv,'exit_code':process.returncode,'output':text[-50000:]}
    finally:
        if process.returncode is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: await asyncio.wait_for(process.wait(),3)
            except TimeoutError:
                os.killpg(process.pid,signal.SIGKILL);await process.wait()


async def main():
    ROOT.mkdir(exist_ok=True,parents=True)
    config=ROOT/'runner-client';config.mkdir(mode=0o700,exist_ok=True)
    report={'started_at':time.time(),'profile':PROFILE,'preexisting_guests':guests(),'commands':[]}
    if report['preexisting_guests']: raise RuntimeError('A guest already exists; no duplicate launch')
    prior=await command([COLIMA,'list','--json'],10)
    if any(json.loads(line).get('status')=='Running' for line in prior['output'].splitlines() if line.startswith('{')):
        raise RuntimeError('A Colima guest is already running; no duplicate launch')
    try:
        value=await command([COLIMA,'start',PROFILE,'--cpus','2','--memory','2','--disk','8','--root-disk','8',
            '--runtime','docker','--vm-type','vz','--mount','none','--activate=false','--ssh-config=false',
            '--ssh-agent=false','--port-forwarder','none','--binfmt=false'])
        report['commands'].append(value)
        if value['exit_code']: raise RuntimeError('Owned backend startup failed; see retained log')
        endpoint='unix://'+str(Path.home()/'.colima'/PROFILE/'docker.sock')
        value=await command([DOCKER,'--config',str(config),'--host',endpoint,'version','--format','json'],20)
        report['commands'].append(value)
        if value['exit_code']: raise RuntimeError('The owned Docker endpoint did not respond')
        report['engine']=json.loads(value['output'])
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)
    finally:
        stopped=await command([COLIMA,'stop',PROFILE,'--force'],60)
        report['commands'].append(stopped)
        report['remaining_guests']=guests()
        report['finished_at']=time.time()
        (ROOT/'colima-probe.json').write_text(json.dumps(report,indent=2)+'\n')
        if stopped['exit_code'] or report['remaining_guests']:
            raise RuntimeError('Owned backend cleanup requires attention; inspect colima-probe.json')
    print(json.dumps({'ready':bool(report.get('engine')),'cleanup_verified':not report['remaining_guests'],
        'report':str(ROOT/'colima-probe.json')}),flush=True)


if __name__=='__main__': asyncio.run(main())
