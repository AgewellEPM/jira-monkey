"""Bounded Docker build operations with durable claims and no host bind mounts.

This module does not start a VM, download an image or apply source changes.
It accepts an explicitly captured local engine/image recipe. Recovery only
inspects and removes the exact objects claimed by a prior operation.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import time

from .common import Refused, require
from .project_tools import ProjectFiles, relative, PROTECTED
from .security import strict_json
from .sml import put, stop

MAX_FILES = 4096
MAX_FILE = 2_000_000
MAX_TOTAL = 32_000_000
EXCLUDED = PROTECTED | {'node_modules', '.venv', '__pycache__', '.build', '.DS_Store'}
FORBIDDEN = ('chrome', 'chromium', 'chromedriver', 'selenium', 'playwright', 'puppeteer')
TOOLS = {'python': '/usr/bin/python3', 'python3': '/usr/bin/python3',
         'node': '/usr/bin/node', 'npm': '/usr/bin/npm', 'make': '/usr/bin/make',
         'cc': '/usr/bin/cc', 'gcc': '/usr/bin/gcc', 'c++': '/usr/bin/c++',
         'g++': '/usr/bin/g++', 'git': '/usr/bin/git', 'sh': '/bin/sh'}
LABEL = 'local.monkey.build-operation'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordinary(name):
    parts = relative(name)
    require(not any(p in EXCLUDED for p in parts), 'Generated, dependency or protected paths are outside the source snapshot')
    return parts


def snapshot(root, audit=None):
    """Capture bounded ordinary files through held, no-follow descriptors."""
    files = ProjectFiles(root, audit)
    records, content, excluded = {}, {}, []
    count = total = 0

    def visit(fd, prefix, depth):
        nonlocal count, total
        require(depth <= 32, 'Source directory depth limit exceeded')
        entries = []
        with os.scandir(fd) as scan:
            for entry in scan:
                count += 1
                require(count <= MAX_FILES * 2, 'Source entry limit exceeded')
                entries.append((entry.name, entry.stat(follow_symlinks=False)))
        if audit:
            audit.observe('build.directory.captured', {'path': str(files.root / prefix), 'entries': len(entries)})
        for leaf, meta in sorted(entries):
            name = prefix + '/' + leaf if prefix else leaf
            try:
                ordinary(name)
            except Refused:
                excluded.append({'path': name, 'reason': 'excluded from source transfer'})
                continue
            if stat.S_ISDIR(meta.st_mode):
                child = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, name, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(meta.st_mode):
                require(meta.st_nlink == 1 and meta.st_size <= MAX_FILE, 'Source has a linked or oversized file: ' + name)
                opened = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(opened, 'rb') as stream:
                    before = os.fstat(stream.fileno())
                    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'Source file type changed')
                    raw = stream.read(MAX_FILE + 1)
                    after = os.fstat(stream.fileno())
                attrs = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                require(attrs(meta) == attrs(before) == attrs(after), 'Source changed during capture: ' + name)
                total += len(raw)
                require(len(raw) <= MAX_FILE and total <= MAX_TOTAL and len(records) < MAX_FILES, 'Source snapshot limit exceeded')
                records[name] = {'sha256': sha(raw), 'bytes': len(raw), 'mode': stat.S_IMODE(before.st_mode) & 0o777}
                content[name] = raw
                if audit:
                    audit.observe('build.file.captured', {'path': str(files.root / name), **records[name]})
            else:
                raise Refused('Source transfer refuses links and special files: ' + name)
    with files.parent('.monkey-root-anchor') as (fd, _):
        visit(fd, '', 0)
    return {'files': records, 'content': content, 'excluded': excluded,
            'root_identity': list(files.root_identity), 'root': str(files.root)}


def input_archive(request, content):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        directories = {'source'}
        for name in content:
            directories.update(p.as_posix() for p in Path('source/' + name).parents if p.as_posix() != '.')
        for name in sorted(directories):
            if name == '.':
                continue
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(info)
        raw = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
        info = tarfile.TarInfo('request.json')
        info.size, info.mode = len(raw), 0o444
        archive.addfile(info, io.BytesIO(raw))
        for name, body in sorted(content.items()):
            ordinary(name)
            info = tarfile.TarInfo('source/' + name)
            info.size, info.mode = len(body), request['files'][name]['mode']
            archive.addfile(info, io.BytesIO(body))
    return raw, stream.getvalue()


def archive_file(raw, name, limit):
    """Never extract daemon or command-controlled tar paths onto the host."""
    require(len(raw) <= limit + 65536, 'Evidence archive exceeds its limit')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        item = archive.next()
        require(item is not None and item.name == name and item.isfile()
                and item.size <= limit and not item.linkname, 'Unexpected evidence archive member')
        stream = archive.extractfile(item)
        require(stream is not None, 'Evidence file missing')
        body = stream.read(limit + 1)
        require(len(body) == item.size and archive.next() is None, 'Unexpected evidence archive contents')
    return body


def validate_recipe(recipe):
    require(type(recipe) is dict and recipe.get('schema_version') == 1, 'Capture a builder recipe first')
    require(set(recipe) == {'schema_version', 'docker', 'docker_sha256', 'socket', 'engine_id',
                           'image_id', 'supervisor_sha256', 'executables'}, 'Unexpected builder recipe fields')
    require(type(recipe['docker']) is str and Path(recipe['docker']).is_absolute(), 'Use an exact Docker executable')
    require(Path(recipe['docker']).is_file() and sha(Path(recipe['docker']).read_bytes()) == recipe['docker_sha256'], 'Docker executable changed')
    require(type(recipe['socket']) is str and recipe['socket'].startswith('/') and '\0' not in recipe['socket'], 'Use a private local Unix engine socket')
    require(type(recipe['image_id']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', recipe['image_id']), 'Use an exact local builder image ID')
    require(type(recipe['engine_id']) is str and recipe['engine_id'], 'Capture the engine identity')
    require(recipe['executables'] == TOOLS, 'Builder executable catalog changed')
    require(recipe['supervisor_sha256'] == sha(Path(__file__).with_name('build_supervisor.py').read_bytes()), 'Builder supervisor differs from this runtime')


def validate_report(report, request, request_raw, trace):
    require(type(report) is dict and report.get('schema_version') == 1, 'Invalid builder report')
    require(report.get('operation_id') == request['operation_id'] and report.get('argv') == request['argv']
            and report.get('request_sha256') == sha(request_raw), 'Builder report does not match its request')
    require(report.get('source_before') == request['files'], 'Builder preimages differ from captured source')
    require(report.get('trace_sha256') == sha(trace) and report.get('trace_bytes') == len(trace)
            and 0 < len(trace) <= 8_000_000, 'Builder trace missing or changed')
    require(report.get('remaining_processes') == [], 'Builder descendant cleanup is unverified')
    require(type(report.get('complete')) is bool and type(report.get('rejected_paths')) is list, 'Missing completion evidence')
    require(report.get('exit_code') is None or type(report.get('exit_code')) is int, 'Invalid command exit status')
    if report['complete']:
        require(report.get('stop_reason') is None and type(report.get('exit_code')) is int
                and not report['rejected_paths'], 'Conflicting command completion evidence')
    output = base64.b64decode(report.get('output_data', ''), validate=True)
    require(sha(output) == report.get('output_sha256') and len(output) == report.get('output_bytes')
            and len(output) <= 256000 and output.decode('utf-8', errors='replace') == report.get('output'), 'Builder output hash mismatch')
    after, changes = report.get('source_after'), report.get('changes')
    require(type(after) is dict and len(after) <= MAX_FILES and type(changes) is dict, 'Invalid returned source inventory')
    total = 0
    for name, item in after.items():
        ordinary(name)
        require(type(item) is dict and set(item) == {'sha256', 'bytes', 'mode'}
                and type(item['bytes']) is int and 0 <= item['bytes'] <= MAX_FILE
                and type(item['mode']) is int and 0 <= item['mode'] <= 0o777
                and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']), 'Invalid returned file metadata')
        total += item['bytes']
    require(total <= MAX_TOTAL, 'Returned source byte limit exceeded')
    before = request['files']
    expected = {n for n in set(before) | set(after) if before.get(n) != after.get(n)}
    require(set(changes) == expected, 'Returned change list omits or invents file changes')
    for name, change in changes.items():
        ordinary(name)
        require(type(change) is dict and set(change) == {'before_sha256', 'after'}, 'Invalid source change')
        require(change['before_sha256'] == (before.get(name) or {}).get('sha256'), 'Returned preimage mismatch')
        if name not in after:
            require(change['after'] is None, 'Deletion has unexpected content')
            continue
        value = change['after']
        require(type(value) is dict and set(value) == {'sha256', 'bytes', 'mode', 'data'}, 'Invalid returned file content')
        require({k: v for k, v in value.items() if k != 'data'} == after[name], 'Returned file metadata mismatch')
        raw = base64.b64decode(value['data'], validate=True)
        require(len(raw) == value['bytes'] and sha(raw) == value['sha256'], 'Returned content hash mismatch')
    return report


def source_change_plan(workspace, result, current=None):
    """Validate the entire patch before ordinary per-file, journaled effects.

    Source imports are not an atomic multi-file transaction. Every applied file
    has a retained preimage and read-back, including when a later file fails.
    Generated binaries remain in the returned evidence, not as host executables.
    """
    require(result.get('complete') is True and not result.get('rejected_paths'), 'Incomplete build cannot apply source changes')
    current = current if current is not None else snapshot(workspace.root, workspace.audit)
    require(current['files'] == result['source_before'], 'Host source changed during the build; inspect retained changes before importing')
    edits, retained = [], []
    from .workspace import GUIDANCE
    for name, change in sorted(result['changes'].items()):
        parts = ordinary(name)
        require(parts[-1] not in GUIDANCE, 'A build cannot alter its own project guidance')
        after = change['after']
        if after is None:
            prior = current['content'][name]
            try: prior.decode('utf-8')
            except UnicodeError: raise Refused('A build cannot remove a binary host file') from None
            edits.append((name, None, change['before_sha256']))
            continue
        raw = base64.b64decode(after['data'], validate=True)
        require(sha(raw) == after['sha256'], 'Returned file content changed before import')
        try:
            content = raw.decode('utf-8')
            if '\0' in content or len(raw) > 250000:
                raise UnicodeError()
        except UnicodeError:
            require(name not in current['files'], 'A build cannot replace a host source file with a binary or oversized file')
            retained.append({'path': name, 'sha256': after['sha256'], 'bytes': len(raw), 'reason': 'Generated artifact retained in the build report'})
            continue
        if name in current['files'] and after['sha256'] == current['files'][name]['sha256']:
            retained.append({'path': name, 'reason': 'Mode-only change retained; source permissions were not changed'})
            continue
        edits.append((name, content, change['before_sha256']))
    return edits, retained


def apply_source_changes(workspace, result):
    edits, retained = source_change_plan(workspace, result)
    effects = [workspace.mutate(name, content, before, remove=content is None) for name, content, before in edits]
    return {'effects': effects, 'retained_artifacts': retained,
            'source_changes_applied': True, 'changed': any(e.get('changed') for e in effects)}


async def import_source_changes(workspace, result):
    workspace.audit.observe('build.import.read_requested', {'root': str(workspace.root), 'operation_id': result['operation_id']})
    current = await asyncio.to_thread(snapshot, workspace.root)
    workspace.audit.observe('build.import.read_completed', {'root': str(workspace.root),
        'inventory_sha256': sha(json.dumps(current['files'], sort_keys=True).encode()),
        'matches_captured_source': current['files'] == result['source_before'], 'operation_id': result['operation_id']})
    edits, retained = await asyncio.to_thread(source_change_plan, workspace, result, current)
    effects = []
    for name, content, before in edits:
        # Each effect keeps its own durable preimage and read-back. Yield between
        # files so exact status/control remains available during a larger import.
        await asyncio.sleep(0)
        effects.append(workspace.mutate(name, content, before, remove=content is None))
    return {'effects': effects, 'retained_artifacts': retained, 'source_changes_applied': True}


class BuildRunner:
    def __init__(self, recipe, root, audit):
        validate_recipe(recipe)
        self.recipe, self.root, self.audit = recipe, Path(root), audit
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.client = self.root / 'docker-client'
        self.client.mkdir(mode=0o700, exist_ok=True)
        require(not list(self.client.iterdir()), 'Builder Docker client directory must contain no credentials or plugins')
        self.active = False

    def observe(self, kind, value):
        if self.audit:
            self.audit.observe('build.' + kind, value)

    async def docker(self, argv, *, data=None, limit=2_000_000, timeout=30, check=True):
        await asyncio.to_thread(validate_recipe, self.recipe)
        require(not list(self.client.iterdir()), 'Builder Docker client configuration changed')
        command = [self.recipe['docker'], '--config', str(self.client),
                   '--host', 'unix://' + self.recipe['socket'], *argv]
        self.observe('engine.requested', {'argv': command, 'input_sha256': sha(data) if data is not None else None,
                                        'input_bytes': len(data) if data else 0, 'timeout': timeout})
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C.UTF-8', 'DOCKER_CLI_HINTS': 'false'}
        child = await asyncio.create_subprocess_exec(*command, env=env, stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self.observe('engine.started', {'pid': child.pid, 'argv': command})
        output, error = bytearray(), bytearray()

        async def collect(stream, into, maximum):
            while part := await stream.read(65536):
                into.extend(part)
                require(len(into) <= maximum, 'Docker response limit exceeded')

        async def send():
            if data is not None:
                child.stdin.write(data)
                await child.stdin.drain()
                child.stdin.close()

        tasks = [asyncio.create_task(collect(child.stdout, output, limit)),
                 asyncio.create_task(collect(child.stderr, error, 65536)), asyncio.create_task(send())]
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*tasks)
                await child.wait()
            self.observe('engine.returned', {'argv': command, 'exit_code': child.returncode,
                         'stdout_sha256': sha(output), 'stdout_bytes': len(output),
                         'stderr_sha256': sha(error), 'stderr_bytes': len(error)})
            require(not check or child.returncode == 0, 'Builder engine request failed: ' + error.decode(errors='replace')[:700])
            return bytes(output), child.returncode
        except BaseException as exc:
            self.observe('engine.interrupted', {'argv': command, 'error_type': type(exc).__name__,
                         'stdout_sha256': sha(output), 'stderr_sha256': sha(error), 'effect': 'Inspect the exact operation; do not replay'})
            raise
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await stop(child)

    async def preflight(self):
        info = strict_json((await self.docker(['info', '--format', '{{json .}}']))[0])
        require(info['ID'] == self.recipe['engine_id'] and info.get('OSType') == 'linux', 'Builder engine identity or OS changed')
        require(info.get('MemoryLimit') and info.get('PidsLimit') and info.get('CpuCfsQuota')
                and info.get('CgroupVersion') == '2', 'Engine cannot enforce the captured build limits')
        image = strict_json((await self.docker(['image', 'inspect', self.recipe['image_id']]))[0])[0]
        require(image['Id'] == self.recipe['image_id'] and image['Config']['Labels'].get('local.monkey.supervisor') == self.recipe['supervisor_sha256'], 'Builder image or supervisor label changed')
        return {'engine_id': info['ID'], 'image_id': image['Id'], 'server_version': info['ServerVersion'],
                'cgroup_version': info['CgroupVersion'], 'architecture': info['Architecture']}

    async def objects(self, operation):
        require(re.fullmatch('build_[0-9a-f]{32}', operation) is not None, 'Invalid build operation identity')
        containers = (await self.docker(['ps', '-aq', '--filter', 'label=' + LABEL + '=' + operation]))[0].decode().split()
        volumes = (await self.docker(['volume', 'ls', '-q', '--filter', 'label=' + LABEL + '=' + operation]))[0].decode().split()
        return containers, volumes

    async def cleanup(self, operation):
        containers, volumes = await self.objects(operation)
        for cid in containers:
            row = strict_json((await self.docker(['inspect', cid]))[0])[0]
            require(row['Config']['Labels'].get(LABEL) == operation, 'Container ownership changed')
            await self.docker(['rm', '-f', cid])
        for volume in volumes:
            row = strict_json((await self.docker(['volume', 'inspect', volume]))[0])[0]
            require(row['Labels'].get(LABEL) == operation, 'Volume ownership changed')
            await self.docker(['volume', 'rm', volume])
        remaining = await self.objects(operation)
        require(remaining == ([], []), 'Build container or volume cleanup is incomplete')
        self.observe('cleanup.completed', {'operation_id': operation, 'containers_removed': containers, 'volumes_removed': volumes})
        return {'containers_removed': containers, 'volumes_removed': volumes, 'remaining': []}

    async def retain_interrupted(self, operation, directory):
        """Stop a claimed worker and retain fixed evidence paths before removal."""
        containers, _ = await self.objects(operation)
        retained = []
        for cid in containers:
            row = strict_json((await self.docker(['inspect', cid]))[0])[0]
            require(row['Config']['Labels'].get(LABEL) == operation, 'Interrupted container ownership changed')
            if row.get('Name') != '/' + operation:
                continue
            if row['State']['Running']:
                await self.docker(['kill', cid])
                row = strict_json((await self.docker(['inspect', cid]))[0])[0]
            require(not row['State']['Running'], 'Interrupted container is still running')
            token = str(time.time_ns())
            put(directory / ('interrupted-container-' + token + '.json'), row)
            for name, limit in (('report.json', 45_000_000), ('syscalls.log', 8_000_000), ('failure.json', 4000)):
                raw, code = await self.docker(['cp', cid + ':/evidence/' + name, '-'], limit=limit + 65536, timeout=60, check=False)
                if code:
                    retained.append({'name': name, 'available': False})
                    continue
                try:
                    body = archive_file(raw, name, limit)
                    from .platform_files import write_private
                    destination = directory / ('interrupted-' + token + '-' + name)
                    write_private(destination, body)
                    retained.append({'name': name, 'path': str(destination), 'sha256': sha(body), 'bytes': len(body)})
                except Exception as exc:
                    retained.append({'name': name, 'available': False, 'error_type': type(exc).__name__})
        self.observe('interrupted_evidence.retained', {'operation_id': operation, 'files': retained})
        return retained

    async def recover(self, operation):
        require(type(operation) is str and re.fullmatch('build_[0-9a-f]{32}', operation), 'Invalid recovery operation')
        directory = self.root / operation
        claim = strict_json((directory / 'claim.json').read_bytes())
        require(claim['operation_id'] == operation and claim['recipe'] == self.recipe, 'Recovery claim or backend recipe changed')
        await self.preflight()
        containers, volumes = await self.objects(operation)
        observed = []
        for cid in containers:
            observed.extend(strict_json((await self.docker(['inspect', cid]))[0]))
        put(directory / 'recovery-observed.json', {'containers': observed, 'volumes': volumes})
        retained = await self.retain_interrupted(operation, directory)
        cleanup = await self.cleanup(operation)
        result = {'operation_id': operation, 'status': 'INTERRUPTED', 'replayed': False,
                  'source_changes_applied': False, 'cleanup': cleanup, 'retained_evidence': retained,
                  'note': 'Only the claimed objects were inspected and removed. No command was restarted.'}
        put(directory / 'recovery.json', result)
        self.observe('recovery.completed', result)
        return result

    async def run(self, root, argv, operation, *, timeout=60):
        require(not self.active, 'One container command is already active')
        require(type(operation) is str and re.fullmatch('build_[0-9a-f]{32}', operation), 'Use a fresh durable build operation ID')
        require(type(timeout) is int and 1 <= timeout <= 120, 'Choose a build timeout from 1 to 120 seconds')
        require(type(argv) is list and 1 <= len(argv) <= 32 and all(type(a) is str and len(a) < 3000 and '\0' not in a for a in argv), 'Use literal bounded build arguments')
        require(not any(word in ' '.join(argv).lower() for word in FORBIDDEN), 'Browser runtimes are not available')
        executable = TOOLS.get(argv[0], argv[0])
        require(executable in TOOLS.values(), 'Choose an installed builder executable')
        require(not any(a in {'install', 'add', 'update', 'upgrade', 'publish', 'push', 'deploy', 'login'} for a in argv[1:]), 'Dependency acquisition and publishing need separately reviewed operations')
        directory = self.root / operation
        directory.mkdir(mode=0o700, exist_ok=False)
        self.active = True
        claim = {'schema_version': 1, 'operation_id': operation, 'recipe': self.recipe,
                 'root': str(Path(root).resolve()), 'argv': [executable, *argv[1:]], 'timeout': timeout}
        # Persist before the first engine operation. A lost response cannot grant
        # permission to create a different command or reset this operation ID.
        put(directory / 'claim.json', claim)
        self.observe('claimed', claim)
        cleanup = None
        try:
            engine = await self.preflight()
            # SQLite audit writes stay on the application's event-loop thread.
            # The bounded read itself runs off-thread and returns its observations.
            observations = []
            class CaptureJournal:
                def observe(self, kind, value): observations.append((kind, value))
            self.observe('capture.requested', {'operation_id': operation, 'root': str(root)})
            try:
                captured = await asyncio.to_thread(snapshot, root, CaptureJournal())
            finally:
                for index, (kind, value) in enumerate(observations):
                    if self.audit: self.audit.observe(kind, value)
                    if index % 16 == 0: await asyncio.sleep(0)
            request = {'schema_version': 1, 'operation_id': operation, 'argv': claim['argv'],
                       'timeout': timeout, 'executables': TOOLS, 'files': captured['files']}
            request_raw, archive = await asyncio.to_thread(input_archive, request, captured['content'])
            put(directory / 'capture.json', {k: v for k, v in captured.items() if k != 'content'})
            (directory / 'input.tar').write_bytes(archive)
            os.chmod(directory / 'input.tar', 0o600)
            with (directory / 'input.tar').open('rb') as stream: os.fsync(stream.fileno())
            put(directory / 'request.json', request)
            self.observe('input.saved', {'operation_id': operation, 'path': str(directory / 'input.tar'), 'sha256': sha(archive), 'bytes': len(archive)})
            label = LABEL + '=' + operation
            input_volume, evidence_volume = operation + '-input', operation + '-evidence'
            for volume in (input_volume, evidence_volume):
                # A same-name existing volume must never be adopted.
                prior, code = await self.docker(['volume', 'inspect', volume], check=False)
                require(code != 0, 'A build volume already exists; recover instead of reusing it')
                await self.docker(['volume', 'create', '--label', label, volume])
            image = self.recipe['image_id']
            helper = (await self.docker(['create', '--pull=never', '--name', operation + '-input', '--label', label, '--network', 'none',
                '--mount', 'type=volume,source=' + input_volume + ',target=/input', image, '/bin/false']))[0].decode().strip()
            await self.docker(['cp', '-', helper + ':/input/'], data=archive, timeout=60)
            args = ['create', '--pull=never', '--name', operation, '--label', label, '--network', 'none', '--ipc', 'private', '--cgroupns', 'private', '--read-only',
                    '--memory', '768m', '--memory-swap', '768m', '--cpus', '1.5', '--pids-limit', '128',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                    '--restart', 'no', '--log-driver', 'none', '--shm-size', '16m',
                    '--tmpfs', '/work:rw,exec,nosuid,nodev,size=128m,mode=755',
                    '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777',
                    '--mount', 'type=volume,source=' + input_volume + ',target=/input,readonly',
                    '--mount', 'type=volume,source=' + evidence_volume + ',target=/evidence']
            for cap in ('CHOWN', 'DAC_OVERRIDE', 'SETUID', 'SETGID', 'SETPCAP', 'SYS_PTRACE', 'KILL'):
                args += ['--cap-add', cap]
            args += [image, '/usr/bin/python3', '-I', '/opt/monkey/build_supervisor.py']
            cid = (await self.docker(args))[0].decode().strip()
            inspected = strict_json((await self.docker(['inspect', cid]))[0])[0]
            put(directory / 'container.json', inspected)
            self.validate_container(inspected, operation, input_volume, evidence_volume)
            await self.docker(['start', cid])
            self.observe('command.started', {'operation_id': operation, 'container_id': cid, 'argv': claim['argv']})
            await self.docker(['wait', cid], timeout=timeout + 30)
            finished = strict_json((await self.docker(['inspect', cid]))[0])[0]
            put(directory / 'finished.json', finished)
            state = finished['State']
            require(not state['Running'] and not state['OOMKilled'] and state['ExitCode'] == 0,
                    'Builder supervisor stopped without a complete report; inspect the retained container state')
            report_raw = archive_file((await self.docker(['cp', cid + ':/evidence/report.json', '-'], limit=46_000_000, timeout=60))[0], 'report.json', 45_000_000)
            trace = archive_file((await self.docker(['cp', cid + ':/evidence/syscalls.log', '-'], limit=8_100_000))[0], 'syscalls.log', 8_000_000)
            report = await asyncio.to_thread(lambda: validate_report(
                strict_json(report_raw, limit=45_000_000, nodes=150000), request, request_raw, trace))
            from .platform_files import write_private
            write_private(directory / 'syscalls.log', trace)
            write_private(directory / 'report.json', report_raw)
            cleanup = await self.cleanup(operation)
            put(directory / 'cleanup.json', cleanup)
            result = {'operation_id': operation, 'argv': report['argv'], 'exit_code': report['exit_code'],
                      'output': report['output'], 'output_sha256': report['output_sha256'],
                      'timed_out': report['stop_reason'] == 'timeout', 'complete': report['complete'],
                      'stop_reason': report['stop_reason'], 'changes': report['changes'],
                      'source_before': report['source_before'], 'source_after': report['source_after'],
                      'rejected_paths': report['rejected_paths'], 'source_changes_applied': False,
                      'evidence': str(directory), 'report_sha256': sha(report_raw), 'trace_sha256': sha(trace),
                      'engine': engine, 'cleanup': cleanup, 'execution_boundary': report['boundary']}
            result['resource_before'], result['resource_after'] = report['resource_before'], report['resource_after']
            put(directory / 'result.json', result)
            self.observe('command.completed', {k: v for k, v in result.items() if k not in {'changes', 'source_before', 'source_after', 'output'}})
            return result
        except BaseException as exc:
            self.observe('command.interrupted', {'operation_id': operation, 'error_type': type(exc).__name__,
                         'source_changes_applied': False, 'replay_permitted': False})
            put(directory / 'interrupted.json', {'operation_id': operation, 'error_type': type(exc).__name__, 'replayed': False})
            raise
        finally:
            if cleanup is None:
                try:
                    await self.retain_interrupted(operation, directory)
                    cleanup = await self.cleanup(operation)
                    put(directory / 'cleanup.json', cleanup)
                except BaseException as exc:
                    self.observe('cleanup.unresolved', {'operation_id': operation, 'error_type': type(exc).__name__, 'recovery_required': True})
                    put(directory / 'cleanup-unresolved.json', {'operation_id': operation, 'error_type': type(exc).__name__})
            self.active = False

    def validate_container(self, row, operation, input_volume, evidence_volume):
        host, config = row['HostConfig'], row['Config']
        require(config['Image'] == self.recipe['image_id'] and config['Labels'].get(LABEL) == operation
                and row['Image'] == self.recipe['image_id'], 'Created container identity changed')
        require(host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] and not host['Privileged']
                and host['Memory'] == 768 * 1024 * 1024 and host['MemorySwap'] == host['Memory']
                and host['NanoCpus'] == 1_500_000_000 and host['PidsLimit'] == 128,
                'Created container resource or isolation configuration differs')
        require(host.get('PidMode', '') == '' and host.get('IpcMode') == 'private' and host.get('CgroupnsMode') == 'private'
                and not host.get('PortBindings') and not host.get('Binds') and not host.get('Devices'), 'Unexpected host sharing')
        caps = lambda values: {value.removeprefix('CAP_') for value in values}
        require(host['RestartPolicy']['Name'] == 'no' and caps(host['CapDrop']) == {'ALL'}
                and caps(host['CapAdd']) == {'CHOWN', 'DAC_OVERRIDE', 'SETUID', 'SETGID', 'SETPCAP', 'SYS_PTRACE', 'KILL'}
                and host['SecurityOpt'] in (['no-new-privileges:true'], ['no-new-privileges']),
                'Created container privilege configuration differs: ' + json.dumps({k: host[k] for k in ('CapDrop','CapAdd','SecurityOpt','RestartPolicy')}))
        require(host.get('Tmpfs') == {'/work': 'rw,exec,nosuid,nodev,size=128m,mode=755',
                                    '/tmp': 'rw,nosuid,nodev,noexec,size=32m,mode=1777'}, 'Temporary workspace limits changed')
        actual = {(m['Type'], m.get('Name'), m['Destination'], m['RW']) for m in row['Mounts'] if m['Type'] == 'volume'}
        require(actual == {('volume', input_volume, '/input', False), ('volume', evidence_volume, '/evidence', True)}
                and all(m['Type'] in {'volume', 'tmpfs'} for m in row['Mounts']), 'Unexpected container mounts')
