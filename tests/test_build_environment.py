import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from monkey.app import App
from monkey.build_environment import BuildEnvironment, descriptor_shape, sha, verify_guard
from monkey.build_runner import TOOLS
from monkey.builder_guard import bounded_command, vm_processes
from monkey.common import Refused, encoded
from monkey.platform_files import private_directory, write_private
from monkey.project_tools import ProjectFiles, relative


FAKE_COLIMA = '''
import json,os,sys,time
from pathlib import Path
root=Path(os.environ['COLIMA_HOME'])
state=root/'fake-status'
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(args)+'\\n')
if args[0]=='list':
    print(json.dumps({'name':'monkey','status':state.read_text() if state.exists() else 'Stopped','cpus':2,'memory':2147483648}))
elif args[0]=='stop':state.write_text('Stopped')
elif args[0]=='wait':
    print('retained before interruption',flush=True)
    time.sleep(60)
elif args[0]=='overflow':
    while True:os.write(1,b'x'*65536)
else:raise SystemExit(1)
'''


class EnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory(dir='/private/tmp')
        self.root=Path(self.folder.name)
        self.app=App(self.root/'state')
        self.env=BuildEnvironment(self.app)
        self.app.build_environment=self.env
        self.build=self.root/'.monkey-build'
        private_directory(self.build)
        private_directory(self.build/'colima')
        fake=self.root/'fake-colima'
        fake.write_text('#!'+sys.executable+'\n'+FAKE_COLIMA)
        fake.chmod(0o700)
        self.descriptor={'schema_version':1,'id':'fixture','root':str(self.build),'profile':'monkey',
            'colima':str(fake),'colima_sha256':sha(fake.read_bytes()),'docker':str(fake),'docker_sha256':sha(fake.read_bytes())}
        write_private(self.build/'environment.json',encoded(self.descriptor))
        self.recipe={'schema_version':1,'docker':str(fake),'docker_sha256':self.descriptor['docker_sha256'],
            'socket':str(self.build/'colima/monkey/docker.sock'),'engine_id':'fixture', 'image_id':'sha256:'+'a'*64,
            'supervisor_sha256':sha((Path(__file__).resolve().parents[1]/'monkey/build_supervisor.py').read_bytes()),'executables':TOOLS}

    async def asyncTearDown(self):
        try:
            if self.app:await self.app.close()
        finally:self.folder.cleanup()

    async def test_setup_returns_acceptance_and_stop_cancels_without_blocking_status(self):
        entered,interrupted=asyncio.Event(),asyncio.Event()
        async def setup(root):
            entered.set()
            try:await asyncio.Event().wait()
            finally:interrupted.set()
        with patch.object(self.env,'setup',setup):
            result=await self.app.dispatch('builder',action='setup')
            self.assertTrue(result['accepted'])
            await asyncio.wait_for(entered.wait(),1)
            status=await asyncio.wait_for(self.app.dispatch('status'),.5)
            self.assertTrue(status['build_environment']['busy'])
            result=await self.app.dispatch('builder',action='stop')
            self.assertTrue(result['accepted'])
            await asyncio.wait_for(self.env.task,3)
        self.assertTrue(interrupted.is_set())
        self.assertFalse(self.env.busy)
        self.assertFalse(self.env.status()['guardian_running'])

    async def test_cancelled_command_retains_partial_output_and_exit(self):
        task=asyncio.create_task(self.env.command([self.descriptor['colima'],'wait'],self.descriptor))
        await asyncio.sleep(.15)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,4)
        paths=list((self.app.db.root/'builder-diagnostics').glob('*.stdout'))
        self.assertEqual(len(paths),1)
        self.assertIn(b'retained before interruption',paths[0].read_bytes())
        events=self.app.audit.view()['trace']
        self.assertTrue(any(r['kind']=='builder.command.settled' for r in events))
        self.assertTrue(self.app.audit.verify()['valid'])

    async def test_command_output_limit_stops_process_and_retains_only_bound(self):
        with self.assertRaisesRegex(Refused,'output limit'):
            await asyncio.wait_for(self.env.command([self.descriptor['colima'],'overflow'],self.descriptor,limit=4096),4)
        paths=list((self.app.db.root/'builder-diagnostics').glob('*.stdout'))
        self.assertEqual(paths[0].stat().st_size,4096)

    async def test_guard_holds_global_lease_and_pipe_eof_stops_only_claimed_profile(self):
        (self.build/'colima/fake-status').write_text('Running')
        await self.env.acquire(self.descriptor)
        directory=self.env.guard_directory
        fingerprint=self.env.guard_fingerprint
        from monkey.builder_guard import __file__ as script
        duplicate=await asyncio.create_subprocess_exec(sys.executable,'-I',script,str(directory/'claim.json'),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            output,error=await asyncio.wait_for(duplicate.communicate(),3)
            self.assertEqual(duplicate.returncode,3,error)
            self.assertIn(b'Another Monkey',output)
            self.assertEqual((self.build/'colima/fake-status').read_text(),'Running')
        finally:
            if duplicate.returncode is None:duplicate.kill();await duplicate.wait()
        await self.env.release_guard()
        self.assertEqual((self.build/'colima/fake-status').read_text(),'Stopped')
        proof=verify_guard(directory,expected_fingerprint=fingerprint)
        self.assertTrue(proof['valid'] and proof['stopped'])
        self.assertIsNone(self.env.guard)
        calls=[json.loads(line) for line in (self.build/'colima/calls.jsonl').read_text().splitlines()]
        self.assertEqual([call for call in calls if call[0]=='stop'],[['stop','--profile','monkey']])
        artifact=next(directory.glob('command_*.stdout'))
        artifact.write_bytes(b'tampered')
        with self.assertRaisesRegex(Refused,'evidence changed'):verify_guard(directory,expected_fingerprint=fingerprint)

    async def test_setup_reuses_owned_environment_without_booting_another_guest(self):
        write_private(self.build/'recipe.json',encoded(self.recipe))
        await self.env.acquire(self.descriptor)
        with patch.object(self.env,'boot',AsyncMock()) as boot, patch('monkey.build_runner.BuildRunner.preflight',AsyncMock()):
            await self.env.setup(str(self.build))
        boot.assert_not_awaited()
        self.assertEqual(self.app.db.config()['build_environment'],self.descriptor)
        self.assertEqual(self.app.db.config()['build_recipe'],self.recipe)

    async def test_failed_job_environment_start_releases_ownership(self):
        with patch.object(self.env,'boot',AsyncMock(side_effect=Refused('fixture boot failure'))):
            with self.assertRaisesRegex(Refused,'fixture boot failure'):
                await self.env.ensure(self.descriptor,self.recipe)
        self.assertIsNone(self.env.guard)
        self.assertTrue(verify_guard(self.env.guard_directory)['stopped'])

    async def test_config_drift_is_refused_before_any_start(self):
        await self.env.acquire(self.descriptor)
        profile=self.build/'colima/monkey';profile.mkdir(mode=0o700)
        (profile/'colima.yaml').write_text('changed: yes\n')
        write_private(self.build/'configuration.json',encoded({'sha256':sha(b'original')}))
        with patch('monkey.builder_guard.vm_processes',return_value=[]):
            with self.assertRaisesRegex(Refused,'configuration changed'):await self.env.boot(self.descriptor)
        calls=[json.loads(line) for line in (self.build/'colima/calls.jsonl').read_text().splitlines()]
        self.assertTrue(all(call[0]!='start' for call in calls))

    async def test_shutdown_failure_still_releases_environment_and_database(self):
        await self.env.acquire(self.descriptor)
        app=self.app
        with patch.object(app.gateway,'close',AsyncMock(side_effect=RuntimeError('fixture listener failure'))):
            with self.assertRaises(BaseExceptionGroup):await app.close()
        self.app=None
        self.assertIsNone(self.env.guard)
        self.assertTrue(verify_guard(self.env.guard_directory)['stopped'])

    def test_environment_state_is_not_a_project_or_source_path(self):
        with self.assertRaises(Refused):ProjectFiles(self.build)
        for name in ('.monkey-build/image','nested/.monkey-build-qa/owner.json','.monkey-build-lock/lease'):
            with self.assertRaises(Refused):relative(name)
        changed=copy.deepcopy(self.descriptor);changed['root']=str(self.root/'ordinary-project')
        with self.assertRaises(Refused):descriptor_shape(changed)


class GuardCommandTests(unittest.TestCase):
    def test_guest_observation_includes_usernet_without_matching_a_sibling_root(self):
        text='11 /bin/limactl hostagent /private/.monkey-build/a\n12 /bin/limactl usernet /private/.monkey-build/b\n13 /bin/limactl hostagent /private/.monkey-build-other/a\n'
        with patch('monkey.builder_guard.subprocess.check_output',return_value=text):
            self.assertEqual([row['pid'] for row in vm_processes('/private/.monkey-build')],[11,12])

    def test_guard_commands_have_output_and_timeout_bounds(self):
        out,err,code,failure=bounded_command([sys.executable,'-c','import os\nwhile True: os.write(1,b"x"*65536)'],os.environ.copy(),2,4096)
        self.assertEqual(failure,'output_limit');self.assertEqual(len(out),4096);self.assertNotEqual(code,0)
        out,err,code,failure=bounded_command([sys.executable,'-c','import time;print("started",flush=True);time.sleep(60)'],os.environ.copy(),.2)
        self.assertEqual(failure,'timeout');self.assertIn(b'started',out);self.assertNotEqual(code,0)
