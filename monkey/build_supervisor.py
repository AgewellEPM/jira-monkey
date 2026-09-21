"""Trusted PID 1 for Monkey's pinned offline builder image; stdlib only.

The command runs as UID 1000 with no capabilities. Only this supervisor and
its tracer can write evidence. Host code independently checks every returned
path, preimage and content hash before considering source changes.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import signal
import stat
import subprocess
import time

MAX_FILES = 4096
MAX_FILE = 2_000_000
MAX_TOTAL = 32_000_000
MAX_OUTPUT = 256_000
MAX_TRACE = 8_000_000
EXCLUDED = {'.git', '.ssh', '.aws', '.codex', '.kist', '.jira-monkey',
            '.env', '.netrc', '.npmrc', 'node_modules', '.venv', '__pycache__'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    parts = name.split('/')
    return (isinstance(name, str) and 0 < len(name) < 1000 and '\\' not in name
            and all(ord(c) >= 32 and ord(c) != 127 for c in name)
            and all(p not in {'', '.', '..'} | EXCLUDED
                    and not p.startswith('.env.') for p in parts))


def inventory(root, *, content=False):
    """Run only before command start or after all other processes are dead."""
    files, rejected, directories = {}, [], []
    total = 0
    entries = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        names.sort()
        for leaf in list(dirs):
            p = Path(current) / leaf
            name = p.relative_to(root).as_posix()
            entries += 1
            if not safe_name(name):
                if content:
                    rejected.append({'path': name, 'reason': 'excluded generated directory'})
                dirs.remove(leaf)
            elif p.is_symlink():
                rejected.append({'path': name, 'reason': 'symlink directory'})
                dirs.remove(leaf)
            else:
                directories.append(name)
        for leaf in names:
            entries += 1
            if entries > MAX_FILES * 2:
                raise ValueError('Workspace entry limit exceeded')
            p = Path(current) / leaf
            name = p.relative_to(root).as_posix()
            if not safe_name(name):
                if content:
                    rejected.append({'path': name, 'reason': 'excluded generated file'})
                continue
            meta = p.lstat()
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                rejected.append({'path': name, 'reason': 'non-regular or linked file'})
                continue
            if meta.st_size > MAX_FILE or len(files) >= MAX_FILES:
                raise ValueError('Workspace file limit exceeded')
            raw = p.read_bytes()
            total += len(raw)
            if total > MAX_TOTAL:
                raise ValueError('Workspace byte limit exceeded')
            item = {'sha256': sha(raw), 'bytes': len(raw), 'mode': stat.S_IMODE(meta.st_mode)}
            if content:
                item['data'] = base64.b64encode(raw).decode('ascii')
            files[name] = item
        if entries > MAX_FILES * 2 or len(directories) > MAX_FILES:
            raise ValueError('Workspace directory limit exceeded')
    return files, rejected, directories


def kill_others():
    # PID 1 never matches kill(-1). This PID namespace is private to the worker.
    if os.getpid() != 1:
        raise RuntimeError('Supervisor must be container PID 1')
    for _ in range(40):
        remaining = [int(p.name) for p in Path('/proc').iterdir()
                     if p.name.isdigit() and int(p.name) != 1]
        if not remaining:
            return []
        for pid in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        time.sleep(.025)
    return [int(p.name) for p in Path('/proc').iterdir()
            if p.name.isdigit() and int(p.name) != 1]


def write_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())


def limits():
    root = Path('/sys/fs/cgroup')
    return {name: (root / name).read_text().strip() for name in
            ('memory.max', 'memory.swap.max', 'memory.events', 'pids.max', 'pids.current', 'pids.events', 'cpu.max')}


def run():
    evidence = Path('/evidence')
    os.chmod(evidence, 0o700)
    input_root, root = Path('/input'), Path('/work')
    request_raw = (input_root / 'request.json').read_bytes()
    request = json.loads(request_raw)
    assert request['schema_version'] == 1 and os.getpid() == 1
    argv = request['argv']
    assert isinstance(argv, list) and 1 <= len(argv) <= 32
    assert all(isinstance(a, str) and len(a) < 3000 and '\0' not in a for a in argv)
    assert argv[0] in request['executables'].values()
    assert type(request['timeout']) is int and 1 <= request['timeout'] <= 120
    resource_before = limits()
    assert resource_before['memory.max'] == str(768 * 1024 * 1024)
    assert resource_before['memory.swap.max'] == '0' and resource_before['pids.max'] == '128'
    quota, period = map(int, resource_before['cpu.max'].split())
    assert quota / period == 1.5
    before = request['files']
    captured, rejected, directories = inventory(input_root / 'source')
    assert not rejected and captured == before, 'Input manifest mismatch'
    for name in directories:
        (root / name).mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chown(root / name, 1000, 1000)
    for name, info in before.items():
        assert safe_name(name)
        target = root / name
        raw = (input_root / 'source' / name).read_bytes()
        with target.open('xb') as stream:
            stream.write(raw)
        # Source executability is retained, but privileged mode bits never are.
        os.chmod(target, info['mode'] & 0o777)
        os.chown(target, 1000, 1000)
    os.chown(root, 1000, 1000)
    Path('/tmp/monkey').mkdir(mode=0o700)
    os.chown('/tmp/monkey', 1000, 1000)
    trace = evidence / 'syscalls.log'
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_TRACE, MAX_TRACE))
    command = ['/usr/bin/strace', '-f', '-ttt', '-T', '-yy', '-s', '4096',
               '-e', 'trace=%file,%process,%network,%creds', '-o', str(trace),
               '/usr/bin/setpriv', '--reuid=1000', '--regid=1000', '--clear-groups',
               '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all',
               '--no-new-privs', '--', *argv]
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp/monkey', 'TMPDIR': '/tmp/monkey',
           'LANG': 'C.UTF-8', 'TZ': 'UTC', 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONNOUSERSITE': '1', 'PIP_NO_INDEX': '1', 'npm_config_offline': 'true'}
    started = time.monotonic()
    child = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    output = bytearray()
    reason = None
    exit_code = None
    try:
        while True:
            if time.monotonic() - started >= request['timeout']:
                reason = 'timeout'
                break
            if trace.exists() and trace.stat().st_size >= MAX_TRACE - 65536:
                reason = 'trace_limit'
                break
            for event, _ in selector.select(.05):
                part = os.read(event.fileobj.fileno(), 8192)
                if not part:
                    selector.unregister(event.fileobj)
                else:
                    room = MAX_OUTPUT - len(output)
                    output.extend(part[:room])
                    if len(part) > room:
                        reason = 'output_limit'
            if reason:
                break
            exit_code = child.poll()
            if exit_code is not None and not selector.get_map():
                break
    finally:
        selector.close()
        remaining = kill_others()
        child.stdout.close()
    elapsed = time.monotonic() - started
    if remaining:
        raise RuntimeError('Descendant cleanup failed')
    after, rejected, directories = inventory(root, content=True)
    changes = {}
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old and new and old['sha256'] == new['sha256'] and old['mode'] == new['mode']:
            continue
        changes[name] = {'before_sha256': old['sha256'] if old else None, 'after': new}
    raw_trace = trace.read_bytes() if trace.exists() else b''
    if not raw_trace or b'execve(' not in raw_trace:
        reason = reason or 'trace_unavailable'
    write_json(evidence / 'report.json', {
        'schema_version': 1, 'operation_id': request['operation_id'],
        'request_sha256': sha(request_raw), 'argv': argv, 'exit_code': exit_code,
        'complete': reason is None and not rejected, 'stop_reason': reason,
        'elapsed_seconds': elapsed, 'output': output.decode('utf-8', errors='replace'),
        'output_data': base64.b64encode(output).decode('ascii'),
        'output_sha256': sha(output), 'output_bytes': len(output),
        'trace_sha256': sha(raw_trace), 'trace_bytes': len(raw_trace),
        'source_before': before, 'source_after': {n: {k: v for k, v in f.items() if k != 'data'}
                                                for n, f in after.items()},
        'changes': changes, 'rejected_paths': rejected, 'remaining_processes': remaining,
        'resource_before': resource_before, 'resource_after': limits(),
        'boundary': 'Private PID namespace; command UID 1000, no capabilities or network; no host mounts. Syscall strings are bounded to 4096 bytes.'})


if __name__ == '__main__':
    try:
        run()
    except BaseException as exc:
        # No successful receipt survives an input, tracing, collection or cleanup
        # failure. A supervisor killed by the cgroup may leave no report at all.
        try:
            kill_others()
            write_json(Path('/evidence/failure.json'), {'error_type': type(exc).__name__, 'message': str(exc)[:1000]})
        finally:
            raise
