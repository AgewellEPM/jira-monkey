"""Foreground lifetime guard for one owned Colima environment.

Launched with a private claim file and an inherited stdin pipe. The guard holds
the environment lease and stops only its captured profile when the pipe closes,
including when the parent is killed. It never creates or starts a VM.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def private(path, *, directory=False):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise RuntimeError('Linked environment path')
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise RuntimeError('Environment files must be private and owned by this operator')
    return path


def save(path, value, *, replace=False):
    private(path.parent, directory=True)
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    target = path.with_name('.pending-' + uuid.uuid4().hex) if replace else path
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if replace:
            os.replace(target, path)
    finally:
        if replace:
            try: target.unlink()
            except FileNotFoundError: pass
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def environment(root):
    # Forward the actual operator home only to locate installed executables and
    # system cache paths. All Colima/Docker state is explicitly private here.
    result = {k: v for k, v in os.environ.items() if k in {'HOME', 'USER', 'LOGNAME', 'TMPDIR'}}
    result.update(PATH='/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                  LANG='en_US.UTF-8', COLIMA_HOME=str(root / 'colima'),
                  DOCKER_CONFIG=str(root / 'colima-client'))
    return result


def vm_processes(root=None, *, timeout=10):
    text = subprocess.check_output(['/bin/ps', '-axo', 'pid=,command='], text=True, timeout=timeout)
    found = []
    for row in text.splitlines():
        fields = row.strip().split(None, 1)
        if len(fields) != 2:
            continue
        pid, command = int(fields[0]), fields[1]
        executable = Path(command.split()[0]).name
        is_guest = (executable.startswith('qemu-system-') or executable in {'krunkit', 'lima-hostagent'}
                    or executable == 'limactl' and any(role in command.split()[1:] for role in ('hostagent','usernet')))
        if is_guest and (root is None or str(root) + '/' in command):
            found.append({'pid': pid, 'command': command})
    return found


def bounded_command(argv, env, timeout, limit=2_000_000):
    """Bound both retained output and wall time, including inherited pipe holders."""
    child = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    outputs = [bytearray(), bytearray()]
    failure = None
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((child.stdout, child.stderr)):
                selector.register(stream, selectors.EVENT_READ, index)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = 'timeout'; break
                for ready, _ in selector.select(min(remaining, .25)):
                    part = os.read(ready.fd, 65536)
                    if not part:
                        selector.unregister(ready.fileobj); continue
                    output = outputs[ready.data]
                    room = limit - len(output)
                    output.extend(part[:room])
                    if len(part) > room:
                        failure = 'output_limit'; break
                if failure: break
        if not failure:
            try: child.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired: failure = 'timeout'
    finally:
        # Always settle the child before reporting. Timeout/output overflow must
        # also kill descendants that keep a pipe open after their parent exits.
        if failure or child.poll() is None:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        child.wait(timeout=5)
        child.stdout.close(); child.stderr.close()
    return bytes(outputs[0]), bytes(outputs[1]), child.returncode, failure


def main(claim_path):
    claim_path = private(claim_path)
    claim = json.loads(claim_path.read_text())
    root = private(Path(claim['root']), directory=True)
    directory = private(claim_path.parent, directory=True)
    descriptor = json.loads(private(root / 'environment.json').read_text())
    if descriptor != claim['descriptor'] or descriptor['profile'] != 'monkey':
        raise RuntimeError('Environment claim does not match its owned profile')
    colima = descriptor['colima']
    if sha(Path(colima).read_bytes()) != descriptor['colima_sha256']:
        raise RuntimeError('Colima executable changed')
    lock_root = private(Path(claim['lock_root']), directory=True)
    if lock_root != Path.home() / '.monkey-build-lock':
        raise RuntimeError('Use the shared Monkey environment lease')
    fd = os.open(lock_root / 'lease', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    journal, seq, previous = directory / 'guard.jsonl', 0, '0' * 64

    def emit(kind, value):
        nonlocal seq, previous
        seq += 1
        row = {'seq': seq, 'at': time.time(), 'kind': kind, 'value': value, 'previous': previous}
        raw = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
        previous = sha(raw)
        row['hash'] = previous
        row['signature'] = base64.b64encode(key.sign(raw)).decode()
        with journal.open('a') as stream:
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(journal, 0o600)
        try: print(json.dumps(row, separators=(',', ':')), flush=True)
        except (BrokenPipeError, OSError):
            # The foreground parent may have been killed. Keep recording to
            # disk, and prevent Python's final stdout flush from failing exit.
            sink=os.open(os.devnull,os.O_WRONLY)
            try:os.dup2(sink,sys.stdout.fileno())
            finally:os.close(sink)

    save(directory / 'guard-key.json', {'public_key': base64.b64encode(public).decode(), 'fingerprint': sha(public)})
    owner = {'claim_id': claim['claim_id'], 'pid': os.getpid(), 'parent_pid': claim['parent_pid'],
             'root': str(root), 'profile': descriptor['profile'], 'status': 'OWNED', 'started_at': time.time()}
    save(root / 'owner.json', owner, replace=True)
    env = environment(root)
    cleanup_deadline = None

    def remaining(maximum):
        value = min(maximum, cleanup_deadline - time.monotonic()) if cleanup_deadline else maximum
        if value <= 0: raise RuntimeError('Environment cleanup deadline exhausted')
        return value

    def command(args, timeout):
        if sha(Path(colima).read_bytes()) != descriptor['colima_sha256']:
            raise RuntimeError('Captured Colima executable changed during cleanup')
        argv = [colima, *args]
        emit('guard.command.started', {'argv': argv, 'timeout': timeout})
        raw, error, code, failure = bounded_command(argv, env, remaining(timeout))
        artifacts = {}
        for name, data in (('stdout', raw), ('stderr', error)):
            path = directory / ('command_' + str(seq) + '.' + name)
            with path.open('xb') as stream:
                os.chmod(path, 0o600)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            artifacts[name] = {'path': str(path), 'sha256': sha(data), 'bytes': len(data),
                               'tail': data.decode(errors='replace')[-2000:]}
        emit('guard.command.returned', {'argv': argv, 'exit_code': code, 'failure': failure, **artifacts})
        return raw, code if not failure else -1

    def observed():
        raw, code = command(['list', '--json'], 15)
        if code:
            raise RuntimeError('Could not determine owned profile state')
        rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        return [r for r in rows if r['name'] == descriptor['profile']]

    # SIGTERM also requests cleanup. Closing the inherited pipe is the normal
    # request, and EOF is authoritative when the Monkey parent is killed.
    def interrupted(signum, frame):
        raise InterruptedError('Foreground owner requested shutdown')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        emit('guard.ready', {'pid': os.getpid(), 'claim_id': claim['claim_id'], 'fingerprint': sha(public)})
        sys.stdin.buffer.readline()
    except (InterruptedError, KeyboardInterrupt):
        pass
    finally:
        # A second signal cannot interrupt the bounded cleanup sequence.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        success = False
        error = None
        cleanup_deadline = time.monotonic() + 170
        def owned_processes():
            return vm_processes(root, timeout=remaining(10))
        emit('guard.stopping', {'claim_id': claim['claim_id'], 'reason': 'foreground pipe closed or shutdown requested'})
        try:
            rows = observed()
            if rows and rows[0]['status'] != 'Stopped' or owned_processes():
                command(['stop', '--profile', descriptor['profile']], 80)
                rows = observed()
                if rows and rows[0]['status'] != 'Stopped' or owned_processes():
                    command(['stop', '--force', '--profile', descriptor['profile']], 40)
                    rows = observed()
            for _ in range(40):
                if not owned_processes(): break
                time.sleep(remaining(.25))
            processes = owned_processes()
            success = (not rows or rows[0]['status'] == 'Stopped') and not processes
            if not success:
                raise RuntimeError('Owned guest/helper cleanup remains incomplete')
        except BaseException as exc:
            error = type(exc).__name__ + ': ' + str(exc)[:800]
        receipt = {'claim_id': claim['claim_id'], 'stopped': success, 'error': error,
                   'finished_at': time.time(), 'profile': descriptor['profile'], 'root': str(root),
                   'journal_head': previous, 'entries_before_receipt': seq, 'signer_fingerprint': sha(public)}
        raw = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode()
        receipt['signature'] = base64.b64encode(key.sign(raw)).decode()
        save(directory / 'guard-receipt.json', receipt)
        save(root / 'owner.json', {**owner, 'status': 'STOPPED' if success else 'CLEANUP_REQUIRED',
                                 'receipt': str(directory / 'guard-receipt.json')}, replace=True)
        emit('guard.stopped' if success else 'guard.cleanup_required', receipt)
        os.close(fd)
        if not success:
            raise SystemExit(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('claim', type=Path)
    try:
        main(parser.parse_args().claim)
    except BlockingIOError:
        print(json.dumps({'error': 'Another Monkey foreground session owns the build environment'}), flush=True)
        raise SystemExit(3)
