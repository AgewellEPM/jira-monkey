"""Build and test the container backend inside one owned, bounded Colima run.

Requires a fresh MCP isolated-session check supplied by the calling operator.
This is qualification tooling, not a silent VM or dependency downloader.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from monkey.build_runner import BuildRunner, TOOLS, sha


class Journal:
    def __init__(self, path):
        self.path = path

    def observe(self, kind, value):
        with self.path.open('a') as stream:
            stream.write(json.dumps({'at': time.time(), 'kind': kind, 'value': value}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())


async def cases(root, recipe, audit=None):
    journal = Journal(root / 'operations.jsonl')
    if audit:
        plain = journal
        class SignedJournal:
            def observe(self, kind, value):
                plain.observe(kind, value)
                audit.observe(kind, value)
        journal = SignedJournal()
    runner = BuildRunner(recipe, root / 'operations', journal)
    results = []

    async def run(name, files, argv, timeout=15):
        workspace = root / name
        workspace.mkdir(mode=0o700)
        for path, text in files.items():
            (workspace / path).parent.mkdir(parents=True, exist_ok=True)
            (workspace / path).write_text(text)
        before = {p.name: p.read_bytes() for p in workspace.iterdir() if p.is_file()}
        result = await runner.run(workspace, argv, 'build_' + uuid.uuid4().hex, timeout=timeout)
        assert {p.name: p.read_bytes() for p in workspace.iterdir() if p.is_file()} == before, 'Command changed host source directly'
        results.append({'name': name, 'operation_id': result['operation_id'], 'exit_code': result['exit_code'],
                        'complete': result['complete'], 'stop_reason': result['stop_reason'],
                        'output': result['output'][:2000], 'changed_files': list(result['changes']),
                        'trace_sha256': result['trace_sha256'], 'cleanup': result['cleanup']})
        print(name, result['exit_code'], result['stop_reason'], flush=True)
        return result

    try:
        result = await run('c-build', {
            'calc.c': '#include <stdio.h>\nint main(void) { printf("43\\n"); return 6*7+1 == 43 ? 0 : 1; }\n',
            'Makefile': 'all:\n\tcc calc.c -o calc\n\t./calc\n'}, ['make'])
        assert result['complete'] and result['exit_code'] == 0 and '43' in result['output'] and 'calc' in result['changes']
        result = await run('subprocess', {
            'generate.js': "require('fs').writeFileSync('generated.py', 'answer = 43\\n');\n",
            'run.py': 'import subprocess\nsubprocess.run(["/usr/bin/node","generate.js"], check=True)\nprint(open("generated.py").read())\n'}, ['python', 'run.py'])
        assert result['complete'] and result['exit_code'] == 0 and 'generated.py' in result['changes']
        probe = '''import json, os, socket
from pathlib import Path
denied = []
for name in ("/evidence/report.json", "/input/request.json", "/etc/monkey-test", "/proc/1/root/evidence/forged"):
    try:
        with open(name,"w") as f: f.write("forged")
    except OSError: denied.append(name)
assert len(denied) == 4, denied
assert os.getuid() == 1000 and os.getgid() == 1000
caps = {r.split(":")[0]:r.split(":")[1].strip() for r in Path("/proc/self/status").read_text().splitlines() if r.startswith("Cap") or r.startswith("NoNewPrivs")}
assert all(int(v,16) == 0 for k,v in caps.items() if k.startswith("Cap")), caps
assert caps["NoNewPrivs"] == "1"
try: os.kill(1,0)
except PermissionError: pass
else: raise AssertionError("command can signal supervisor")
sock=socket.socket(); sock.settimeout(.5)
assert sock.connect_ex(("1.1.1.1",443)) != 0
sock.close()
print(json.dumps({"uid":os.getuid(),"caps":caps,"denied":denied,"network":"unreachable"}))
'''
        result = await run('isolation', {'probe.py': probe}, ['python', 'probe.py'])
        assert result['complete'] and result['exit_code'] == 0, result['output']
        result = await run('detached-child', {'wait.py': 'import subprocess\nsubprocess.Popen(["/usr/bin/python3","-c","import time; time.sleep(90)"],start_new_session=True)\nprint("parent exited",flush=True)\n'}, ['python', 'wait.py'], 2)
        assert result['timed_out'] and not result['complete'] and result['cleanup']['remaining'] == []
        result = await run('output-limit', {'flood.py': 'import os\nfor _ in range(1000): os.write(1,b"x"*4096)\n'}, ['python', 'flood.py'])
        assert not result['complete'] and result['stop_reason'] == 'output_limit'
        result = await run('symlink-output', {'link.py': 'import os\nos.symlink("/evidence/report.json", "escape")\n'}, ['python', 'link.py'])
        assert not result['complete'] and result['rejected_paths']
        result = await run('pid-limit', {'pids.py': '''import subprocess
children=[]
blocked=False
try:
    for _ in range(200):
        try: children.append(subprocess.Popen(["/bin/sleep","30"]))
        except BlockingIOError:
            blocked=True
            break
finally:
    for child in children: child.terminate()
    for child in children: child.wait()
assert blocked and len(children) < 128, len(children)
print("Process limit enforced at",len(children),"children")
'''}, ['python', 'pids.py'], 20)
        assert result['complete'] and result['exit_code'] == 0, result['output']
        try:
            result = await run('memory-limit', {'memory.py': 'payload=bytearray(1200000000)\nprint("allocation unexpectedly succeeded")\n'}, ['python', 'memory.py'], 20)
            assert result['exit_code'] != 0 and 'allocation unexpectedly succeeded' not in result['output']
        except Exception as exc:
            # A cgroup kill of PID 1 is a failed execution, not a passing report.
            # Only classify this case as verified if retained daemon state says OOM.
            operation = sorted((root / 'operations').glob('build_*/claim.json'), key=lambda p:p.stat().st_mtime)[-1].parent
            state = json.loads((operation / 'finished.json').read_text())['State']
            assert state['OOMKilled'] and not state['Running'], (str(exc),state)
            results.append({'name':'memory-limit','oom_killed':True,'operation_id':operation.name,'complete':False})
        # Kill the host driver after an observed container start, while its
        # command still runs. The child deliberately does not start `docker wait`.
        crash_dir = root / 'crash-child'
        crash_dir.mkdir()
        (crash_dir / 'wait.py').write_text('import time\nprint("running",flush=True)\ntime.sleep(60)\n')
        operation = 'build_' + uuid.uuid4().hex
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--crash-child',
            str(root), '--operation', operation], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 40)
            assert line.strip() == 'CONTAINER_STARTED', line
            child.kill()
            await asyncio.to_thread(child.wait, 5)
        finally:
            if child.poll() is None:
                child.kill(); child.wait()
            child.stdout.close(); child.stderr.close()
        recovered = await runner.recover(operation)
        assert recovered['replayed'] is False and recovered['cleanup']['remaining'] == []
        results.append({'name': 'host-crash-recovery', **recovered})
        if audit:
            journal.observe('build.crash_driver.retained', {'path': str(root / 'crash-driver.jsonl'),
                            'sha256': sha((root / 'crash-driver.jsonl').read_bytes())})
        return results
    finally:
        (root / 'cases.json').write_text(json.dumps(results, indent=2) + '\n')


async def signed_cases(root, recipe):
    from monkey.app import App
    from monkey.audit import verify_file
    class QualificationModels:
        fixture = True
        async def catalog(self, config): return []
    app = App(root / 'audit-state', models=QualificationModels())
    try:
        async with app.audit.run(None, 'container_build_qualification'):
            result = await cases(root, recipe, app.audit)
            result.append(await agent_case(app, root, recipe))
            (root / 'cases.json').write_text(json.dumps(result, indent=2) + '\n')
        exported = app.audit.export()
        verified = verify_file(exported['path'], exported['signer_fingerprint'])
        (root / 'audit-verification.json').write_text(json.dumps({'export': exported, 'verification': verified}, indent=2) + '\n')
        return result
    finally:
        await app.close()


async def agent_case(app, root, recipe, environment=None):
    """Script only model decisions; use the real REPL, worker and source imports."""
    from monkey.cli import line
    import shlex
    project = root / 'agent-workspace'; project.mkdir(mode=0o700)
    source = 'def add(a, b):\n    return a + b\n'
    generator = 'require("fs").writeFileSync("generated.py", ' + json.dumps(source) + ');\n'
    checker = 'from generated import add\nassert add(7,8)==15\nassert add(-4,9)==5\nprint("two addition checks passed")\n'
    actions = iter([
        ('plan', {'steps':['Create the generator and checker.','Run the real build.'], 'checks':['Both arithmetic checks pass.']}),
        ('write_file', {'path':'generate.js','content':generator}),
        ('write_file', {'path':'check.py','content':checker}),
        ('write_file', {'path':'Makefile','content':'all:\n\tnode generate.js\n\tpython3 check.py\n'}),
        ('run_command', {'argv':['make']}),
        ('reflect', {'findings':['The recorded build output contains both passed checks.'],
                     'adjustments':['Keep the generated source with the retained build evidence.'],
                     'procedure':['Generate source, run the checker, inspect its actual output.']}),
        ('finish', {'summary':'Generated and checked an addition module.',
                    'checks':['The recorded build passed both arithmetic cases.'],'source_ids':[]})])
    async def decision(c, model, pin, instruction, packet, shape=None, **kwargs):
        action, arguments = next(actions)
        return {'action':action,'arguments':arguments,'reason':'Scripted qualification decision; tool effects are real',
                'evidence_refs':packet['evidence_refs'][-3:]}, {'provider':'fixture','model':'scripted-decisions','fixture':True}
    app.models.local = decision
    if environment:
        await app.dispatch('setup',settings={'build_recipe':recipe,'build_environment':environment})
    else:
        await line(app, '/builder use --file ' + shlex.quote(str(root / 'recipe.json')))
    started = await line(app, '/build --path ' + shlex.quote(str(project)) + ' Generate an addition module and verify it through a real multiprocess build')
    job_id = started['job_id']
    await line(app, '/builder native')
    assert app.db.job(job_id)['agent_scope']['build_recipe'] == recipe
    if environment:assert app.db.job(job_id)['agent_scope']['build_environment']==environment
    latencies = []
    async with asyncio.timeout(180):
        while app.execution.task and not app.execution.task.done():
            begin = time.monotonic()
            await line(app, '/status')
            latencies.append((time.monotonic()-begin)*1000)
            await asyncio.sleep(.05)
        await app.execution.task
    job = app.db.job(job_id)
    assert job['agent_state'] == 'AWAITING_REVIEW', job['reason']
    assert (project / 'generated.py').read_text() == source
    record = app.execution.record(job, 'agent_result_id')
    assert record['files']['generated.py'] == sha(source.encode())
    assert job['agent_generation'] == job['agent_checked_generation']
    observed = app.agent.observations(job_id)
    run = next(r['result'] for r in observed if r['action']=='run_command')
    assert run['source_changes_applied'] and run['cleanup']['remaining'] == []
    assert run['effects'][0]['path'] == 'generated.py'
    measurement = {'name':'repl-agent-build','job_id':job_id,'model_decisions':'scripted fixture',
        'actual_multiprocess_execution':True,'source_import_verified':True,'captured_backend_preserved_after_global_change':True,
        'agent_state':job['agent_state'],'status_samples_ms':latencies,'status_max_ms':max(latencies),
        'result_hash':record['result_hash'],'signed_off':False}
    if environment:measurement['captured_managed_environment']=environment['id']
    print('REPL agent build and source import passed', flush=True)
    return measurement


async def crash_child(root, operation):
    recipe = json.loads((root / 'recipe.json').read_text())
    class InterruptedDriver(BuildRunner):
        async def docker(self, argv, **kwargs):
            if argv[0] == 'wait':
                print('CONTAINER_STARTED', flush=True)
                await asyncio.Event().wait()
            return await super().docker(argv, **kwargs)
    runner = InterruptedDriver(recipe, root / 'operations', Journal(root / 'crash-driver.jsonl'))
    await runner.run(root / 'crash-child', ['python', 'wait.py'], operation, timeout=90)


def main(args):
    root = Path(args.output).resolve()
    root.mkdir(mode=0o700, exist_ok=False)
    check = Path(args.session_check)
    assert time.time() - check.stat().st_mtime < 120, 'Obtain a fresh MCP session check before launching'
    sessions = json.loads(check.read_text())
    assert any(x.get('text') == '[]' for x in sessions.get('content', [])), 'Another isolated session exists'
    profile = 'monkey-validation-20260915'
    colima = shutil.which('colima')
    docker = str(Path(shutil.which('docker')).resolve())
    journal = Journal(root / 'host-commands.jsonl')
    env = {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
           'LANG': 'en_US.UTF-8', 'DOCKER_BUILDKIT': '0', 'DOCKER_CLI_HINTS': 'false'}
    # Colima needs the real operator home to resolve its existing, owned profile.
    host_env = {k: v for k, v in os.environ.items() if k in {'HOME', 'USER', 'LOGNAME', 'TMPDIR'}} | env

    def command(argv, *, maximum=180, data=None, private_env=False):
        journal.observe('host.command.started', {'argv': argv, 'input_sha256': sha(data) if data else None, 'timeout': maximum})
        with (root / 'setup.log').open('ab') as log:
            child = subprocess.Popen(argv, stdin=subprocess.PIPE if data else subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, env=env if private_env else host_env, start_new_session=True)
            try:
                child.communicate(data, timeout=maximum)
            except BaseException:
                os.killpg(child.pid, signal.SIGKILL); child.wait()
                raise
        journal.observe('host.command.finished', {'argv': argv, 'exit_code': child.returncode})
        assert child.returncode == 0, 'See setup.log for ' + ' '.join(argv[:3])

    status = subprocess.check_output([colima, 'list', '--json'], text=True, env=host_env)
    assert all(json.loads(line)['status'] == 'Stopped' for line in status.splitlines() if line.strip()), 'A Colima VM is active'
    processes = subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True)
    assert not any(any(x in line.lower() for x in ('qemu', 'krunkit', 'limactl', 'nativebrowser')) for line in processes.splitlines()), 'An isolated guest or app is active'
    (root / 'before-sessions.json').write_bytes(check.read_bytes())
    (root / 'before-colima.jsonl').write_text(status)
    attempted = False
    try:
        attempted = True
        command([colima, 'start', '--profile', profile, '--cpu', '2', '--memory', '2', '--disk', '8',
            '--mount', 'none', '--activate=false', '--ssh-config=false', '--ssh-agent=false', '--port-forwarder', 'none'], maximum=240)
        client = root / 'build-client'; client.mkdir(mode=0o700)
        socket_path = str(Path.home() / '.colima' / profile / 'docker.sock')
        base = [docker, '--config', str(client), '--host', 'unix://' + socket_path]
        def docker_read(argv):
            journal.observe('host.read.started', {'argv': [*base,*argv]})
            value = subprocess.check_output([*base, *argv], env=env, timeout=45)
            journal.observe('host.read.completed', {'argv': [*base,*argv], 'sha256':sha(value), 'bytes':len(value)})
            return value
        command([*base, 'pull', 'debian:bookworm-slim'], maximum=240, private_env=True)
        image = json.loads(docker_read(['image', 'inspect', 'debian:bookworm-slim']))[0]
        base_digest = next(x for x in image['RepoDigests'] if x.startswith('debian@sha256:'))
        context = root / 'builder-context'; context.mkdir(mode=0o700)
        for original, target in [('build_supervisor.py','build_supervisor.py'), ('builder.Dockerfile','Dockerfile')]:
            shutil.copyfile(SOURCE / 'monkey' / original, context / target)
        supervisor_hash = sha((context / 'build_supervisor.py').read_bytes())
        tag = 'monkey-builder:dev13-' + supervisor_hash[:12]
        print('Building pinned offline worker image', flush=True)
        command([*base, 'build', '--build-arg', 'BASE_IMAGE=' + base_digest, '--build-arg', 'SUPERVISOR_SHA256=' + supervisor_hash,
                 '-t', tag, str(context)], maximum=600, private_env=True)
        built = json.loads(docker_read(['image', 'inspect', tag]))[0]
        inventory_name = 'monkey-builder-inventory-' + uuid.uuid4().hex
        try:
            docker_read(['create','--pull=never','--name',inventory_name,'--network','none',
                         '--label','local.monkey.builder-inventory='+inventory_name,built['Id'],'/bin/false'])
            from monkey.build_runner import archive_file
            packages = archive_file(docker_read(['cp',inventory_name+':/opt/monkey-packages.txt','-']), 'monkey-packages.txt', 500000)
            (root/'packages.tsv').write_bytes(packages)
            names={line.split('\t')[0] for line in packages.decode().splitlines()}
            assert not names & {'chromium','chromium-common','chromium-driver','google-chrome-stable','firefox','firefox-esr','electron','node-puppeteer'}, 'A prohibited browser package is present'
        finally:
            command([*base,'rm','-f',inventory_name],private_env=True)
        engine = json.loads(docker_read(['info', '--format', '{{json .}}']))
        recipe = {'schema_version': 1, 'docker': docker, 'docker_sha256': sha(Path(docker).read_bytes()),
                  'socket': socket_path, 'engine_id': engine['ID'], 'image_id': built['Id'],
                  'supervisor_sha256': supervisor_hash, 'executables': TOOLS}
        (root / 'recipe.json').write_text(json.dumps(recipe, indent=2) + '\n')
        (root / 'image.json').write_text(json.dumps({'base_digest': base_digest, 'built': built, 'engine': engine}, indent=2) + '\n')
        results = asyncio.run(signed_cases(root, recipe))
        (root / 'qualification.json').write_text(json.dumps({'passed': True, 'case_count': len(results), 'image_id': built['Id'], 'source_changes_applied': False}, indent=2) + '\n')
        print('Container cases passed:', len(results), flush=True)
        remaining = docker_read(['ps','-aq']).decode().split()
        (root/'remaining-containers.json').write_text(json.dumps(remaining)+'\n')
        assert not remaining, 'Test containers remain before VM shutdown'
    finally:
        if attempted:
            command([colima, 'stop', '--profile', profile], maximum=120)
        status = subprocess.check_output([colima, 'list', '--json'], text=True, env=host_env)
        processes = subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True)
        remaining = [line for line in processes.splitlines() if any(x in line.lower() for x in ('qemu', 'krunkit', 'limactl', 'nativebrowser'))]
        (root / 'cleanup.json').write_text(json.dumps({'colima': status.splitlines(), 'remaining_guest_processes': remaining}, indent=2) + '\n')
        assert all(json.loads(line)['status'] == 'Stopped' for line in status.splitlines() if line.strip()) and not remaining, 'Guest cleanup incomplete'
        print('Owned VM stopped; guest/helper cleanup checked', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--session-check')
    parser.add_argument('--crash-child')
    parser.add_argument('--operation')
    options = parser.parse_args()
    if options.crash_child:
        asyncio.run(crash_child(Path(options.crash_child), options.operation))
    else:
        main(options)
