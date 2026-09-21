"""Own a bounded build environment for the lifetime of one foreground Monkey."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

from .common import Refused, encoded, require
from .platform_files import private_directory, read_regular, write_private
from .security import strict_json
from .sml import stop

BASE_IMAGE = 'debian@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def descriptor_shape(value):
    require(type(value) is dict and set(value) == {'schema_version','id','root','profile','colima','colima_sha256','docker','docker_sha256'},
            'Invalid managed environment descriptor')
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and value['profile'] == 'monkey',
            'Unsupported managed build environment')
    require(all(type(v) is str and 0 < len(v) < 2000 for k,v in value.items() if k != 'schema_version'), 'Invalid environment fields')
    require(all(Path(value[k]).is_absolute() for k in ('root','colima','docker')), 'Use captured absolute environment paths')
    require(Path(value['root']).name.startswith('.monkey-build') and '..' not in Path(value['root']).parts,
            'Use a private .monkey-build folder, reserved from project tools')
    require(all(len(value[k])==64 and all(c in '0123456789abcdef' for c in value[k]) for k in ('colima_sha256','docker_sha256')),
            'Invalid captured environment executable hash')
    require(len(str(Path(value['root'])/'colima/_lima/colima-monkey/ssh.sock').encode()) <= 100,
            'The environment folder is too long for local Unix sockets; select a shorter --root')


def verify_guard(directory, *, expected_fingerprint=None):
    """Verify the helper's independent record chain and terminal receipt."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    directory = Path(directory)
    public = strict_json(read_regular(directory/'guard-key.json',4000,private=True))
    raw_key = base64.b64decode(public['public_key'],validate=True)
    require(sha(raw_key) == public['fingerprint'] and (expected_fingerprint is None or public['fingerprint'] == expected_fingerprint), 'Guardian signing identity changed')
    key = Ed25519PublicKey.from_public_bytes(raw_key)
    raw = read_regular(directory/'guard.jsonl',5_000_000,private=True)
    previous, rows = '0'*64, []
    for index,line in enumerate(raw.splitlines(),1):
        row = strict_json(line,limit=100000)
        signed = {k:v for k,v in row.items() if k not in {'signature','hash'}}
        body = json.dumps(signed,sort_keys=True,separators=(',',':')).encode()
        require(row['seq']==index and row['previous']==previous and sha(body)==row['hash'], 'Guardian journal changed')
        key.verify(base64.b64decode(row['signature'],validate=True),body)
        if row['kind']=='guard.command.returned':
            for name in ('stdout','stderr'):
                artifact=row['value'][name]
                path=Path(artifact['path'])
                require(path.parent==directory,'Guardian output escaped its receipt directory')
                data=read_regular(path,2_000_000,private=True)
                require(sha(data)==artifact['sha256'] and len(data)==artifact['bytes'],'Guardian command evidence changed')
        rows.append(row); previous=row['hash']
    receipt = strict_json(read_regular(directory/'guard-receipt.json',16000,private=True))
    fields={k:v for k,v in receipt.items() if k!='signature'}
    key.verify(base64.b64decode(receipt['signature'],validate=True),json.dumps(fields,sort_keys=True,separators=(',',':')).encode())
    count=receipt['entries_before_receipt']
    require(type(count) is int and count+1==len(rows) and rows[count-1]['hash']==receipt['journal_head'] and rows[-1]['value']==receipt,
            'Guardian receipt is not bound to its journal')
    return {'valid':True,'entries':len(rows),'head':previous,'fingerprint':public['fingerprint'],
            'stopped':receipt['stopped'],'receipt':receipt,'journal_sha256':sha(raw)}


class BuildEnvironment:
    def __init__(self, app):
        self.app,self.db,self.audit=app,app.db,app.audit
        self.task=None
        self.guard=None
        self.pump=None
        self.guard_directory=None
        self.guard_fingerprint=None
        self.descriptor=None
        self.phase='IDLE'
        self.detail='Build environment has not been started in this session'
        self.started_at=None
        self.ready=None
        self.error=None
        self.action=None
        self.stderr_pump=None
        self._release_lock=asyncio.Lock()

    @property
    def busy(self):
        return bool(self.task and not self.task.done())

    def event(self,phase,message,**data):
        self.phase,self.detail=phase,message
        self.db.global_event('builder.'+phase.lower(),{'message':message,**data})

    def status(self):
        c=self.db.config()
        descriptor=c.get('build_environment') or self.descriptor
        return {'managed':bool(descriptor),'configured':bool(c.get('build_recipe')),
            'phase':self.phase,'message':self.detail,'busy':self.busy,
            'guardian_running':bool(self.guard and self.guard.returncode is None),
            'image_id':(c.get('build_recipe') or {}).get('image_id'),
            'elapsed_seconds':round(time.monotonic()-self.started_at,1) if self.started_at and self.busy else None,
            'error':self.error,'root':descriptor['root'] if descriptor else None}

    def launch(self,action,root=None,*,job=None,call_id=None):
        require(not self.busy or action=='stop' and self.action!='stop','A build-environment operation is already running')
        require(not self.app.execution.active and not self.app.worker.active,'Wait for or cancel active work before changing its environment')
        require(action in {'setup','start','stop','recover'},'Unsupported environment action')
        previous=self.task
        self.action=action
        self.started_at=time.monotonic();self.error=None
        self.event('REQUESTED','Build environment '+action+' requested; the prompt remains available',action=action)
        async def work():
            async with self.audit.run(None,'builder.'+action):
                try:
                    if action=='stop':
                        if previous and not previous.done():
                            previous.cancel()
                            await asyncio.gather(previous,return_exceptions=True)
                        await self.release_guard()
                        if not self.guard:self.event('STOPPED','No build environment is owned by this foreground session')
                    elif action=='setup': await self.setup(root)
                    elif action=='recover':
                        scope=job['agent_scope']
                        if scope.get('build_environment'):await self.ensure(scope['build_environment'],scope['build_recipe'])
                        from .build_runner import BuildRunner
                        runner=await asyncio.to_thread(BuildRunner,scope['build_recipe'],self.db.root/'build-runs'/job['id'],self.audit)
                        result=await runner.recover(call_id)
                        self.event('RECOVERED','Build operation reconciled; no command was replayed',job_id=job['id'],operation=call_id,result=result)
                    else:
                        c=self.db.config()
                        recipe=c.get('build_recipe')
                        if not recipe and c.get('build_environment'):
                            recipe=strict_json(read_regular(Path(c['build_environment']['root'])/'recipe.json',16000,private=True))
                        await self.ensure(c.get('build_environment'),recipe)
                        # Explicit start selects the inspected recipe for new work.
                        c.update(build_recipe=recipe);self.db.configure(c)
                except asyncio.CancelledError:
                    self.event('INTERRUPTED','Environment operation interrupted; owned cleanup is running')
                    await self.release_guard()
                    raise
                except Exception as exc:
                    self.error=str(exc) if isinstance(exc,Refused) else type(exc).__name__+': '+str(exc)[:500]
                    try:await self.release_guard()
                    except Exception as cleanup:
                        self.error+='; cleanup: '+str(cleanup)[:500]
                    self.event('FAILED',self.error)
        self.task=asyncio.create_task(work(),name='monkey-builder-'+action)
        return {'accepted':True,'action':action,'message':'Build environment '+action+' requested. Use /builder status to inspect its actual state.'}

    def env(self,descriptor):
        from .builder_guard import environment
        result=environment(Path(descriptor['root']))
        result.update(DOCKER_BUILDKIT='0',DOCKER_CLI_HINTS='false')
        return result

    async def command(self,argv,descriptor,*,timeout=30,limit=2_000_000,check=True):
        # No shell, inherited credentials, Docker context or login startup files.
        require(argv and argv[0] in {descriptor['colima'],descriptor['docker']},'Use a captured environment executable')
        expected=descriptor['colima_sha256'] if argv[0]==descriptor['colima'] else descriptor['docker_sha256']
        require(sha(await asyncio.to_thread(Path(argv[0]).read_bytes))==expected,'Environment executable changed')
        self.audit.observe('builder.command.requested',{'argv':argv,'timeout':timeout})
        child=await asyncio.create_subprocess_exec(*argv,env=self.env(descriptor),stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
        self.audit.observe('builder.command.started',{'argv':argv,'pid':child.pid})
        out,error=bytearray(),bytearray()
        overflow=asyncio.get_running_loop().create_future()
        async def collect(stream,into):
            while part:=await stream.read(65536):
                room=limit-len(into);into.extend(part[:room])
                if len(part)>room and not overflow.done():overflow.set_result(True)
        tasks=[asyncio.create_task(collect(child.stdout,out)),asyncio.create_task(collect(child.stderr,error))]
        async def completed():
            await asyncio.gather(*tasks)
            await child.wait()
        settled=asyncio.create_task(completed())
        outcome='interrupted'
        try:
            async with asyncio.timeout(timeout):
                await asyncio.wait([settled,overflow],return_when=asyncio.FIRST_COMPLETED)
                require(not overflow.done(),'Environment command output limit exceeded')
                await settled
            outcome='returned'
            require(not check or child.returncode==0,'Environment command failed: '+error.decode(errors='replace')[-1000:])
            return bytes(out),child.returncode
        except BaseException as exc:
            outcome=type(exc).__name__
            raise
        finally:
            # Keep draining while stopping the child, so inherited pipe pressure
            # cannot deadlock cancellation. Retain the bounded partial output.
            try:await stop(child)
            finally:
                if not settled.done():settled.cancel()
                await asyncio.gather(settled,*tasks,return_exceptions=True)
                overflow.cancel()
            directory=self.guard_directory or self.db.root/'builder-diagnostics'
            private_directory(directory)
            identity=uuid.uuid4().hex
            write_private(directory/(identity+'.stdout'),bytes(out))
            write_private(directory/(identity+'.stderr'),bytes(error))
            self.audit.observe('builder.command.settled',{'argv':argv,'pid':child.pid,'exit_code':child.returncode,'outcome':outcome,
                'stdout_sha256':sha(out),'stderr_sha256':sha(error),'stdout_path':str(directory/(identity+'.stdout')),
                'stderr_path':str(directory/(identity+'.stderr'))})

    async def profiles(self,descriptor):
        raw,_=await self.command([descriptor['colima'],'list','--json'],descriptor)
        return [strict_json(line) for line in raw.splitlines() if line.strip()]

    async def acquire(self,descriptor):
        descriptor_shape(descriptor)
        if self.guard and self.guard.returncode is None:
            require(self.descriptor==descriptor,'This session already owns another environment')
            return
        root=Path(descriptor['root']);private_directory(root)
        require(strict_json(read_regular(root/'environment.json',16000,private=True))==descriptor,'Managed environment identity changed')
        self.descriptor=descriptor
        lock_root=Path.home()/'.monkey-build-lock';private_directory(lock_root)
        directory=self.db.root/'builder-runs'/('env_'+uuid.uuid4().hex);private_directory(directory)
        self.guard_directory=directory
        self.guard_fingerprint=None
        claim={'schema_version':1,'claim_id':directory.name,'root':str(root),'descriptor':descriptor,
               'lock_root':str(lock_root),'parent_pid':os.getpid(),'session_id':self.app.session_id}
        write_private(directory/'claim.json',encoded(claim))
        self.audit.observe('builder.guard.requested',claim)
        script=Path(__file__).with_name('builder_guard.py')
        guard_code=await asyncio.to_thread(script.read_bytes)
        write_private(directory/'guard.py',guard_code)
        env={k:v for k,v in os.environ.items() if k in {'HOME','USER','LOGNAME','TMPDIR'}}
        env.update(PATH='/usr/bin:/bin',LANG='en_US.UTF-8')
        self.guard=await asyncio.create_subprocess_exec(sys.executable,'-I',str(directory/'guard.py'),str(directory/'claim.json'),
            env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,limit=150000)
        self.ready=asyncio.get_running_loop().create_future()
        guard=self.guard
        async def collect_stderr():
            result=bytearray()
            while part:=await guard.stderr.read(65536):result.extend(part[:max(0,1_000_000-len(result))])
            return bytes(result)
        self.stderr_pump=asyncio.create_task(collect_stderr())
        async def pump():
            try:
                while line:=await guard.stdout.readline():
                    row=strict_json(line,limit=150000)
                    if row.get('error'):
                        if not self.ready.done():self.ready.set_exception(Refused(row['error']))
                        continue
                    self.audit.observe('builder.guard.event',row)
                    if row['kind']=='guard.ready':
                        self.guard_fingerprint=row['value']['fingerprint']
                        if not self.ready.done():self.ready.set_result(row['value'])
                    if row['kind']=='guard.stopping':self.event('STOPPING','Stopping the owned build environment')
                if not self.ready.done():self.ready.set_exception(Refused('Build environment guard did not acquire ownership'))
            except BaseException as exc:
                if not self.ready.done():self.ready.set_exception(exc)
                raise
        self.pump=asyncio.create_task(pump())
        try:
            await asyncio.wait_for(asyncio.shield(self.ready),15)
        except BaseException:
            await self.release_guard()
            if self.ready.done() and not self.ready.cancelled():self.ready.exception()
            raise
        self.audit.observe('builder.guard.started',{'pid':self.guard.pid,'script_sha256':sha(guard_code),
            'claim_id':directory.name,'fingerprint':self.guard_fingerprint})

    async def release_guard(self):
        async with self._release_lock:
            await self._release_guard()

    async def _release_guard(self):
        if not self.guard:return
        guard,pump,directory=self.guard,self.pump,self.guard_directory
        if guard.stdin and not guard.stdin.is_closing():guard.stdin.close()
        self.event('STOPPING','Waiting for the owned environment to stop')
        try:
            await asyncio.wait_for(guard.wait(),190)
            if pump:await asyncio.gather(pump,return_exceptions=True)
            error=await self.stderr_pump if self.stderr_pump else b''
            if error:
                write_private(directory/'guard.stderr',error[:1000000])
            if (directory/'guard-receipt.json').exists():
                proof=await asyncio.to_thread(verify_guard,directory,expected_fingerprint=self.guard_fingerprint)
                self.audit.observe('builder.guard.verified',proof)
                require(proof['stopped'] and guard.returncode==0,'Build environment cleanup is incomplete; inspect its signed receipt')
            else:
                require(guard.returncode==3 and not self.guard_fingerprint,'Guardian ended without a verified cleanup receipt')
            if self.guard_fingerprint:self.event('STOPPED','Owned build environment stopped; its disk and recipe are retained')
            else:self.event('IDLE','This session did not acquire the environment; its current owner keeps control')
        finally:
            if guard.returncode is not None:
                self.guard,self.pump,self.stderr_pump=None,None,None

    async def boot(self,descriptor):
        from .builder_guard import vm_processes
        rows=await self.profiles(descriptor)
        own=[r for r in rows if r['name']=='monkey']
        processes=await asyncio.to_thread(vm_processes)
        require(not processes,'Another guest or VM helper is active; keep one VM at a time')
        require(not own or own[0]['status']=='Stopped','The managed profile is already active; recover its foreground ownership before starting work')
        root=Path(descriptor['root'])
        config=root/'colima/monkey/colima.yaml'
        pin=root/'configuration.json'
        if config.exists():
            require(pin.exists(),'The existing environment configuration has no captured hash; inspect it before reuse')
            expected=strict_json(read_regular(pin,16000,private=True))
            require(sha(await asyncio.to_thread(config.read_bytes))==expected['sha256'],'Managed environment configuration changed; inspect it before reuse')
        self.event('STARTING','Starting the private build environment',root=descriptor['root'])
        args=[descriptor['colima'],'start','--profile','monkey','--cpu','2','--memory','2','--disk','8',
            '--root-disk','8','--runtime','docker','--vm-type','vz','--mount','none','--activate=false',
            '--ssh-config=false','--ssh-agent=false','--port-forwarder','none','--template=false','--binfmt=false']
        try:
            await self.command(args,descriptor,timeout=300,limit=5_000_000)
        finally:
            if config.exists():
                raw=await asyncio.to_thread(config.read_bytes)
                write_private(self.guard_directory/'colima.yaml',raw)
                write_private(pin,encoded({'sha256':sha(raw)}),replace=pin.exists())
        rows=await self.profiles(descriptor)
        own=[r for r in rows if r['name']=='monkey']
        require(len(own)==1 and own[0]['status']=='Running' and own[0]['cpus']==2 and own[0]['memory']==2*1024**3,
                'Build environment did not start with its captured limits')
        # Retain the generated configuration; every launch also supplies all
        # confinement flags rather than accepting an inherited template.
        raw=await asyncio.to_thread(config.read_bytes)
        self.audit.observe('builder.vm.running',{'profile':'monkey','configuration_sha256':sha(raw),'root':descriptor['root']})

    async def setup(self,root=None):
        require(sys.platform=='darwin','Managed Colima setup is currently implemented for macOS; other platforms still require qualification')
        root=Path(root).expanduser().absolute() if root else Path.home()/'.monkey-build'
        require(root!=Path.home() and root==root.resolve(),'Select an ordinary private environment directory')
        require(root.name.startswith('.monkey-build'),'Use a .monkey-build folder, reserved from project tools')
        descriptor_file=root/'environment.json'
        if descriptor_file.exists():
            descriptor=strict_json(read_regular(descriptor_file,16000,private=True))
        else:
            require(not root.exists() or not list(root.iterdir()),'Preserve the existing directory; choose an empty environment location')
            colima,docker=shutil.which('colima'),shutil.which('docker')
            require(colima and docker,'Install Colima and the Docker CLI before preparing the managed environment')
            colima,docker=str(Path(colima).resolve()),str(Path(docker).resolve())
            descriptor={'schema_version':1,'id':uuid.uuid4().hex,'root':str(root),'profile':'monkey',
                'colima':colima,'colima_sha256':sha(await asyncio.to_thread(Path(colima).read_bytes)),
                'docker':docker,'docker_sha256':sha(await asyncio.to_thread(Path(docker).read_bytes))}
            descriptor_shape(descriptor);private_directory(root)
            for name in ('colima','colima-client','docker-client'):private_directory(root/name)
            write_private(descriptor_file,encoded(descriptor))
        already=bool(self.guard and self.guard.returncode is None)
        await self.acquire(descriptor)
        if not already:await self.boot(descriptor)
        recipe_file=root/'recipe.json'
        if recipe_file.exists():
            recipe=strict_json(read_regular(recipe_file,16000,private=True))
        else:
            recipe=await self.build_image(descriptor)
            write_private(recipe_file,encoded(recipe))
        from .build_runner import BuildRunner
        runner=await asyncio.to_thread(BuildRunner,recipe,self.db.root/'builder-preflight',self.audit)
        await runner.preflight()
        c=self.db.config();c.update(build_recipe=recipe,build_environment=descriptor)
        self.db.configure(c)
        self.event('READY','Build environment is ready for new jobs; it will close with this Monkey session',image_id=recipe['image_id'])

    async def ensure(self,descriptor,recipe):
        require(descriptor and recipe,'Prepare a managed build environment with /builder setup first')
        try:
            already=bool(self.guard and self.guard.returncode is None)
            await self.acquire(descriptor)
            if not already:await self.boot(descriptor)
            from .build_runner import BuildRunner
            require(recipe['socket']==str(Path(descriptor['root'])/'colima/monkey/docker.sock') and
                    recipe['docker']==descriptor['docker'] and recipe['docker_sha256']==descriptor['docker_sha256'],
                    'Captured recipe does not belong to this managed environment')
            runner=await asyncio.to_thread(BuildRunner,recipe,self.db.root/'builder-preflight',self.audit)
            await runner.preflight()
            self.event('READY','Captured build environment is running',image_id=recipe['image_id'])
        except BaseException:
            await self.release_guard()
            raise

    async def build_image(self,descriptor):
        from .build_runner import BuildRunner,TOOLS
        root=Path(descriptor['root'])
        base=[descriptor['docker'],'--config',str(root/'docker-client'),'--host','unix://'+str(root/'colima/monkey/docker.sock')]
        self.event('DOWNLOADING','Downloading the pinned public base image for build tools',image=BASE_IMAGE)
        await self.command([*base,'pull',BASE_IMAGE],descriptor,timeout=300,limit=5_000_000)
        context=self.guard_directory/'image-context';private_directory(context)
        for source,target in (('build_supervisor.py','build_supervisor.py'),('builder.Dockerfile','Dockerfile')):
            write_private(context/target,await asyncio.to_thread(Path(__file__).with_name(source).read_bytes))
        supervisor=sha((context/'build_supervisor.py').read_bytes())
        tag='monkey-builder:managed-'+supervisor[:16]
        self.event('INSTALLING','Installing Python, Node and C/C++ build tools in the private image')
        await self.command([*base,'build','--force-rm','--build-arg','BASE_IMAGE='+BASE_IMAGE,'--build-arg','SUPERVISOR_SHA256='+supervisor,
            '-t',tag,str(context)],descriptor,timeout=600,limit=12_000_000)
        image=strict_json((await self.command([*base,'image','inspect',tag],descriptor))[0])[0]
        info=strict_json((await self.command([*base,'info','--format','{{json .}}'],descriptor))[0])
        recipe={'schema_version':1,'docker':descriptor['docker'],'docker_sha256':descriptor['docker_sha256'],
            'socket':str(root/'colima/monkey/docker.sock'),'engine_id':info['ID'],'image_id':image['Id'],
            'supervisor_sha256':supervisor,'executables':TOOLS}
        self.event('CHECKING','Checking the actual builder before selecting it')
        project=self.guard_directory/'probe';private_directory(project)
        write_private(project/'probe.py',b'import os,subprocess\nfrom pathlib import Path\nassert os.getuid()==1000\nsubprocess.run(["/usr/bin/node","-e","console.log(6*7)"],check=True)\nprint(Path("/opt/monkey-packages.txt").read_text(),end="")\n')
        runner=await asyncio.to_thread(BuildRunner,recipe,self.db.root/'builder-qualification',self.audit)
        result=await runner.run(project,['python','probe.py'],'build_'+uuid.uuid4().hex,timeout=15)
        require(result['complete'] and result['exit_code']==0 and result['output'].startswith('42\n'),'Builder qualification failed; no new recipe selected')
        packages=result['output'].split('\n',1)[1].encode()
        names={line.split('\t')[0] for line in packages.decode().splitlines()}
        require({'python3','nodejs','gcc','g++','make','strace'}<=names,'Builder package inventory is incomplete')
        require(not names & {'chromium','chromium-common','chromium-driver','google-chrome-stable','firefox','firefox-esr','electron','node-puppeteer'},'Unexpected browser package in build environment')
        write_private(root/'packages.tsv',packages,replace=(root/'packages.tsv').exists())
        write_private(root/'qualification.json',encoded({'recipe':recipe,'probe':{k:v for k,v in result.items() if k!='changes'},
            'base_image':BASE_IMAGE,'image_metadata':image,'packages_sha256':sha(packages)}),replace=(root/'qualification.json').exists())
        self.audit.observe('builder.image.qualified',{'image_id':image['Id'],'base_image':BASE_IMAGE,'packages_sha256':sha(packages),
            'qualification':str(root/'qualification.json')})
        return recipe

    async def close(self):
        current=asyncio.current_task()
        if self.task and self.task is not current and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        await self.release_guard()
