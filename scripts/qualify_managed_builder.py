"""Qualify one managed foreground environment through the installed Monkey CLI.

Each invocation may launch exactly one VM. A fresh IsolatedTester session check
is required; this script never starts a replacement guest after a failed check.
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
from monkey.build_environment import verify_guard
from monkey.builder_guard import vm_processes


async def qualify(args):
    output=args.output.resolve()
    output.mkdir(mode=0o700,exist_ok=False)
    check=json.loads(args.session_check.read_text())
    assert time.time()-args.session_check.stat().st_mtime<120,'Obtain a fresh MCP session check before launching'
    assert any(row.get('text')=='[]' for row in check.get('content',[])),'Another isolated session exists'
    assert not vm_processes(),'Another VM or guest helper is active'
    (output/'session-check.json').write_text(json.dumps(check,indent=2)+'\n')
    action='setup' if args.mode=='setup' else 'start'
    argv=[str(args.command),'--state',str(args.state),'--json','builder',action]
    if action=='setup':argv.extend(['--root',str(args.root)])
    process=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=2_000_000)
    owner_task=asyncio.current_task()
    for name in (signal.SIGTERM,signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(name,owner_task.cancel)
    rows=[];queue=asyncio.Queue();samples=[];phases=[];owner=None;proof=None
    report={'mode':args.mode,'argv':argv,'pid':process.pid,'model_generation':False,'passed':False}
    async def read():
        while line:=await process.stdout.readline():
            row=json.loads(line)
            rows.append(row);await queue.put(row)
        await queue.put(None)
    reader=asyncio.create_task(read())
    stderr=asyncio.create_task(process.stderr.read(1_000_000))
    async def until(predicate,timeout=15):
        async with asyncio.timeout(timeout):
            while True:
                row=await queue.get()
                if row is None:raise RuntimeError('Installed Monkey ended before the expected response')
                if predicate(row):return row.get('value')
    async def command(line,predicate):
        begin=time.perf_counter()
        process.stdin.write(line.encode()+b'\n');await process.stdin.drain()
        result=await until(lambda row:row.get('type')=='reply' and isinstance(row.get('value'),dict) and predicate(row['value']))
        return result,(time.perf_counter()-begin)*1000
    async def ready():
        async with asyncio.timeout(1100):
            while True:
                value,elapsed=await command('/builder status',lambda r:'phase' in r)
                samples.append(elapsed)
                if not phases or phases[-1]!=value['phase']:
                    phases.append(value['phase']);print(value['phase'],value['message'],flush=True)
                if value['phase']=='FAILED':raise RuntimeError(value['error'])
                if not value['busy']:
                    assert value['phase']=='READY',value
                    return value
                await asyncio.sleep(.5)
    try:
        accepted=await until(lambda row:row.get('type')=='builder')
        assert accepted['accepted']
        value=await ready()
        owner=json.loads((args.root/'owner.json').read_text())
        assert owner['parent_pid']==process.pid and owner['status']=='OWNED'
        directory=args.state/'builder-runs'/owner['claim_id']
        fingerprint=json.loads((directory/'guard-key.json').read_text())['fingerprint']
        guests=await asyncio.to_thread(vm_processes,args.root)
        assert guests,'The environment reported READY without an observed guest'
        report.update(image_id=value['image_id'],guardian_pid=owner['pid'],claim_id=owner['claim_id'])
        report.update(guardian_fingerprint=fingerprint,guest_processes=guests)
        if args.mode=='setup':
            # Repeating setup reuses this live owner; it must not start VM two.
            await command('/builder setup --root '+str(args.root),lambda r:r.get('accepted') is True)
            again=await ready()
            assert again['image_id']==value['image_id']
            await command('/builder native',lambda r:True)
            await command('/builder start',lambda r:r.get('accepted') is True)
            selected=await ready()
            assert selected['configured'] and selected['image_id']==value['image_id']
            assert await asyncio.to_thread(vm_processes,args.root)==guests,'Setup reuse replaced the observed guest'
            assert json.loads((args.root/'owner.json').read_text())['claim_id']==owner['claim_id'],'Setup reuse replaced the owner'
            report['owned_environment_reused']=True
        if args.mode=='crash':
            process.kill();await process.wait()
            report['foreground_exit']='SIGKILL'
        else:
            process.stdin.write(b'/quit\n');await process.stdin.drain()
            await asyncio.wait_for(process.wait(),195)
            assert process.returncode==0
            report['foreground_exit']='quit'
        async with asyncio.timeout(195):
            while True:
                receipt=directory/'guard-receipt.json'
                if receipt.exists() and not await asyncio.to_thread(vm_processes):
                    proof=await asyncio.to_thread(verify_guard,directory,expected_fingerprint=fingerprint)
                    assert proof['stopped'],proof
                    break
                await asyncio.sleep(.5)
        # The lease must be released, independently of the mutable owner file.
        import fcntl
        with (Path.home()/'.monkey-build-lock/lease').open('rb') as lease:
            async with asyncio.timeout(5):
                while True:
                    try:fcntl.flock(lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:await asyncio.sleep(.05)
        report['passed']=True
    except BaseException as exc:
        report['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        if process.returncode is None:
            process.stdin.write(b'/quit\n')
            try:await process.stdin.drain();await asyncio.wait_for(process.wait(),195)
            except (TimeoutError,BrokenPipeError,ConnectionResetError):
                process.kill();await process.wait()
        await asyncio.wait_for(reader,10)
        error=await asyncio.wait_for(stderr,10)
        report.update(exit_code=process.returncode,status_samples_ms=samples,status_max_ms=max(samples,default=None),
            phases=phases,guardian_proof=proof,remaining_guests=await asyncio.to_thread(vm_processes))
        (output/'terminal.json').write_text(json.dumps(rows,indent=2)+'\n')
        (output/'stderr.txt').write_bytes(error)
        (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in {'guardian_proof','status_samples_ms'}}),flush=True)
    assert not report['remaining_guests'],'Guest cleanup remains incomplete'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--session-check',type=Path,required=True)
    parser.add_argument('--mode',choices=['setup','restart','crash'],required=True)
    os.umask(0o077)
    asyncio.run(qualify(parser.parse_args()))
