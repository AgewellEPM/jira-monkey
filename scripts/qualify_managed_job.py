"""One installed worker run that must start its own captured build environment.

Only model decisions are fixtures. Environment startup, file tools, the actual
multiprocess command, source imports and shutdown are real application paths.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monkey.app import App
from monkey.audit import verify_file
from monkey.builder_guard import vm_processes
from monkey.build_environment import verify_guard
from qualify_build_runner import agent_case


async def qualify(args):
    root=args.output.resolve();root.mkdir(mode=0o700,exist_ok=False)
    check=json.loads(args.session_check.read_text())
    assert time.time()-args.session_check.stat().st_mtime<120
    assert any(row.get('text')=='[]' for row in check.get('content',[]))
    assert not vm_processes(),'Another guest or helper is active'
    (root/'session-check.json').write_text(json.dumps(check,indent=2)+'\n')
    current=asyncio.current_task()
    for signum in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(signum,current.cancel)
    class Models:
        fixture=True
        async def catalog(self,config):return []
    app=App(root/'state',models=Models())
    result={'passed':False,'model_decisions':'scripted fixture','installed_runtime':str(Path(__file__).resolve().parents[1])}
    try:
        descriptor=json.loads((args.environment/'environment.json').read_text())
        recipe=json.loads((args.environment/'recipe.json').read_text())
        assert app.build_environment is None
        async with app.audit.run(None,'managed-job-qualification'):
            result.update(await agent_case(app,root,recipe,descriptor))
            assert app.build_environment and app.build_environment.guard.returncode is None
            directory=app.build_environment.guard_directory
            fingerprint=app.build_environment.guard_fingerprint
            await app.build_environment.close()
            result['guardian_proof']=verify_guard(directory,expected_fingerprint=fingerprint)
            assert result['guardian_proof']['stopped']
        exported=app.audit.export()
        result['audit']={'export':exported,'verified':verify_file(exported['path'],exported['signer_fingerprint'])}
        result['passed']=True
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        try:await app.close()
        finally:
            result['remaining_guests']=await asyncio.to_thread(vm_processes)
            (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({k:v for k,v in result.items() if k not in {'audit','guardian_proof','status_samples_ms'}}),flush=True)
    assert not result['remaining_guests']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--session-check',required=True,type=Path)
    os.umask(0o077)
    asyncio.run(qualify(parser.parse_args()))
