#!/usr/bin/env python3
"""Check an offline Monkey bundle, install it once, and start its private REPL.

Run with the target Python and -I. No elevated privileges, package-index access,
shell evaluation, model download, service installation, or global PATH edit.
Bundle hashes detect changed artifacts; they are not a publisher signature.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import stat
import subprocess
import sys
import sysconfig
import time
import types
import uuid


TARGETS = {
    'macos-arm64-py311': ('darwin', 'macosx', 'arm64', '3.11'),
    'linux-x64-py311': ('linux', 'linux', 'x86_64', '3.11'),
    'linux-arm64-py311': ('linux', 'linux', 'aarch64', '3.11'),
    'windows-x64-py311': ('win32', 'win', 'amd64', '3.11'),
    'windows11-arm64-x64-py311': ('win32', 'win', 'amd64', '3.11'),
    'windows-arm64-py313': ('win32', 'win', 'arm64', '3.13'),
}
MAX_FILE = 160 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}\Z')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def installation_environment():
    # Windows Path.home() needs USERPROFILE, or HOMEDRIVE plus HOMEPATH,
    # even when a CLI call supplies an explicit --state path.
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'HOME', 'USER', 'USERNAME', 'LOGNAME', 'SYSTEMROOT', 'WINDIR',
        'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'TEMP', 'TMP', 'TMPDIR', 'LANG'}}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_KEYRING_PROVIDER='disabled', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    return env


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON field')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda value: require(False, 'Invalid JSON number'))


def file_identity(info, *, windows=None):
    # CPython 3.11 adds synthetic execute bits to Windows path stat results
    # for .exe/.bat/.cmd/.com, but not to fstat on the same open handle.
    # These bits are filename hints, not ACL authority. Preserve file identity,
    # timestamps, readonly/type bits and link count in the comparison.
    windows = os.name == 'nt' if windows is None else windows
    mode = info.st_mode & ~0o111 if windows else info.st_mode
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, mode, info.st_nlink)


def read(path, limit=MAX_FILE):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'Linked or reparse bundle path refused: ' + str(item))
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and before.st_size <= limit, 'Invalid or oversized bundle file: ' + str(path))
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    require(len(data) <= limit and file_identity(before) == file_identity(opened)
            == file_identity(after) == file_identity(path.stat()),
            'Bundle file changed while reading: ' + str(path))
    return data


def relative(value):
    require(type(value) is str and value and '\\' not in value and ':' not in value,
            'Invalid manifest path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and '..' not in path.parts,
            'Manifest path must stay inside the bundle')
    return path


def windows_architecture():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    current = kernel.GetCurrentProcess
    current.restype, current.argtypes = wintypes.HANDLE, []
    inspect = kernel.IsWow64Process2
    inspect.restype = wintypes.BOOL
    inspect.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT)]
    process, native = wintypes.USHORT(), wintypes.USHORT()
    require(inspect(current(), ctypes.byref(process), ctypes.byref(native)),
            'Windows process architecture could not be established')
    executable = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    data = read(executable)
    require(data[:2] == b'MZ' and len(data) >= 64, 'Python executable has no PE header')
    offset = int.from_bytes(data[60:64], 'little')
    require(offset + 6 <= len(data) and data[offset:offset + 4] == b'PE\0\0',
            'Python executable has an invalid PE header')
    return {'process_machine': process.value, 'native_machine': native.value,
            'image_machine': int.from_bytes(data[offset + 4:offset + 6], 'little'),
            'windows_build': sys.getwindowsversion().build}


def host_matches(target):
    require(target in TARGETS, 'Unknown deployment target')
    system, tag, machine, version = TARGETS[target]
    built = sysconfig.get_platform().lower().replace('-', '_')
    actual_machine = platform.machine().lower()
    compatibility = target == 'windows11-arm64-x64-py311'
    require(sys.platform == system and built.startswith(tag)
            and built.endswith(machine) and (actual_machine in {'amd64', 'arm64'} if compatibility
                                             else actual_machine == machine)
            and platform.python_version().startswith(version + '.'),
            f'This bundle requires {target}; current Python is '
            f'{platform.python_version()} / {sysconfig.get_platform()} / {platform.machine()}. '
            'Select the matching bundle and interpreter; no architecture substitution is automatic.')
    if system == 'win32':
        architecture = windows_architecture()
        expected_image = 0x8664 if machine == 'amd64' else 0xaa64
        expected_host = 0xaa64 if compatibility else expected_image
        require(architecture['image_machine'] == expected_image
                and architecture['native_machine'] == expected_host
                and architecture['process_machine'] in ({0, 0x8664} if compatibility else {0})
                and (not compatibility or architecture['windows_build'] >= 22000),
                'Windows kernel/process architecture does not match this explicit deployment target')
        return architecture


def inspect_bundle(root, expected=None):
    root = Path(root).absolute()
    raw = read(root / 'manifest.json', 2 * 1024 * 1024)
    require(expected is None or digest(raw) == expected, 'Bundle manifest does not match the supplied hash')
    manifest = decode(raw)
    require(manifest.get('schema') == 'monkey.deployment-bundle.v1', 'Unsupported bundle schema')
    host_matches(manifest.get('target'))
    require(manifest.get('python') == TARGETS[manifest['target']][3], 'Manifest Python mismatch')
    payload = {}
    groups = [('source/' + name, value) for name, value in manifest['source_hashes'].items()]
    helpers = manifest.get('launcher_hashes', {})
    require(set(helpers) == {'start_monkey.py', 'qualify_cli.py'}, 'Bundle has no checked startup tools')
    groups.extend(helpers.items())
    groups.append(('requirements.txt', manifest['requirements_sha256']))
    packages = manifest['packages']
    require(type(packages) is list and 1 <= len(packages) <= 100, 'Invalid package inventory')
    requirements = []
    names, filenames = set(), set()
    for package in packages:
        name, version, filename = package['name'], package['version'], package['file']
        require(type(name) is str and re.fullmatch('[a-z0-9][a-z0-9-]*', name)
                and type(version) is str and re.fullmatch('[A-Za-z0-9.]+', version), 'Invalid package pin')
        require(Path(filename).name == filename and filename.endswith('.whl')
                and name not in names and filename not in filenames, 'Duplicate or invalid package file')
        names.add(name); filenames.add(filename)
        groups.append(('wheels/' + filename, package['sha256']))
        requirements.append(name + '==' + version + ' --hash=sha256:' + package['sha256'])
    require(names and 'monkey-workdesk' in names, 'Monkey wheel is absent')
    require({p.name for p in (root / 'wheels').iterdir()} == filenames,
            'Wheel directory differs from the pinned inventory')
    for name, expected_hash in groups:
        relative(name)
        require(type(expected_hash) is str and HEX.fullmatch(expected_hash), 'Invalid artifact hash')
        data = read(root / name)
        require(digest(data) == expected_hash, 'Bundle artifact changed: ' + name)
        payload[name] = data
    require(payload['requirements.txt'] == ('\n'.join(requirements) + '\n').encode(),
            'Requirements must contain exactly the manifest package hashes')
    for name in ('jira_monkey.py', 'monkey/__init__.py', 'monkey/platform_files.py', 'monkey/windows_files.py'):
        require('source/' + name in payload, 'Private installation support is absent: ' + name)
    return manifest, digest(raw), payload


def filesystem(payload):
    # Execute the already-read, hash-checked standard-library-only modules. Do
    # not reopen source paths or import the uninstalled app/dependency tree.
    names = [('jira_monkey', 'jira_monkey.py'), ('monkey', 'monkey/__init__.py')]
    if os.name == 'nt':
        names.append(('monkey.windows_files', 'monkey/windows_files.py'))
    names.append(('monkey.platform_files', 'monkey/platform_files.py'))
    for name, source in names:
        require(name not in sys.modules, 'Start the bundle in a fresh isolated Python process')
        module = types.ModuleType(name)
        module.__file__ = '<checked-bundle>/' + source
        module.__package__ = name.rpartition('.')[0]
        if name == 'monkey':
            module.__path__ = []
        sys.modules[name] = module
        exec(compile(payload['source/' + source], module.__file__, 'exec'), module.__dict__)
    return sys.modules['monkey.platform_files']


def runtime_tree(root):
    result = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            require(path.resolve().is_relative_to(root.resolve()), 'Runtime link leaves its installation')
            result[name] = {'link': os.readlink(path)}
        elif path.is_file():
            # The checked launcher redirects Python's cache lookup to a fresh
            # empty location. Default __pycache__ entries are never consumed;
            # ordinary direct console use may create them without changing code.
            if '__pycache__' in path.parts and path.suffix == '.pyc':
                require(info.st_nlink == 1 and not getattr(info, 'st_file_attributes', 0) & 0x400,
                        'Invalid runtime cache file')
                continue
            result[name] = {'sha256': digest(read(path)), 'mode': stat.S_IMODE(info.st_mode)}
        else:
            require(path.is_dir() and not getattr(info, 'st_file_attributes', 0) & 0x400,
                    'Unexpected runtime entry')
    return result


def setup_process(argv, *, cwd, env, log, timeout):
    # Keep the trusted installer parent alive until timeout cleanup can address
    # its descendants. subprocess.run(timeout=...) kills only that parent.
    options = {'start_new_session': True} if os.name != 'nt' else {
        'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log,
                               stderr=subprocess.STDOUT, **options)
    try:
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            if process.poll() is None:
                if os.name == 'nt':
                    taskkill = Path(os.environ['SystemRoot']) / 'System32' / 'taskkill.exe'
                    cleanup = subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                                             stdout=log, stderr=subprocess.STDOUT, timeout=30)
                    require(cleanup.returncode == 0,
                            'Installer process-tree cleanup failed; inspect install.log before retrying')
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=30)
            raise
    finally:
        # A failed tree-cleanup command remains an error, but must not leave
        # this foreground setup command waiting without a bound for its parent.
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def install(root, manifest, manifest_hash, payload, fs, setup_timeout=600):
    require(type(setup_timeout) is int and 1 <= setup_timeout <= 1800, 'Invalid bounded setup timeout')
    private = root / '.monkey-install'
    fs.private_directory(private)
    runtime = private / 'runtime'
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    monkey = runtime / ('Scripts/Monkey.exe' if os.name == 'nt' else 'bin/Monkey')
    receipt = private / 'receipt.json'
    base = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    binding = {'bundle_sha256': manifest_hash, 'target': manifest['target'],
               'base_python': str(base), 'base_python_sha256': digest(read(base)),
               'python_version': platform.python_version()}
    with_lease = fs.StateLease(private / 'install.lock')
    try:
        if receipt.exists():
            previous = decode(fs.read_regular(receipt, 8 * 1024 * 1024, private=True))
            require(previous.get('schema') == 'monkey.bundle-installation.v2'
                    and previous.get('binding') == binding, 'Existing installation belongs to different bundle bytes or Python')
            print('Monkey setup: verifying installed file hashes', file=sys.stderr, flush=True)
            require(previous['runtime'] == runtime_tree(runtime),
                    'Installed runtime changed; preserve it and install a fresh bundle')
            return private, python, monkey, previous
        require(not runtime.exists(), 'An incomplete installation exists. Inspect .monkey-install/install.log and use a fresh bundle directory.')
        work = private / 'packages'
        require(not work.exists(), 'Previous installation files exist; use a fresh bundle directory')
        fs.private_directory(work)
        for name, data in payload.items():
            if name.startswith('wheels/'):
                fs.write_private(work / Path(name).name, data)
        fs.write_private(private / 'requirements.txt', payload['requirements.txt'])
        fs.write_private(private / 'qualify_cli.py', payload['qualify_cli.py'])
        fs.write_private(private / 'install.log', b'')
        env = installation_environment()
        commands = []
        def run(argv):
            started = time.monotonic()
            print('Monkey setup: ' + ('creating private Python environment' if 'venv' in argv else
                  'installing pinned packages' if 'install' in argv else 'checking installed runtime'),
                  file=sys.stderr, flush=True)
            with (private / 'install.log').open('ab') as log:
                log.write(encode({'argv': argv, 'timeout_seconds': setup_timeout})); log.flush()
                code = setup_process(argv, cwd=private, env=env, log=log, timeout=setup_timeout)
                log.flush(); os.fsync(log.fileno())
            commands.append({'argv': argv, 'exit_code': code, 'timeout_seconds': setup_timeout,
                             'elapsed_ms': round((time.monotonic() - started) * 1000, 2)})
            require(code == 0, 'Installation check failed; inspect .monkey-install/install.log')
        fs.private_directory(runtime)
        run([sys.executable, '-I', '-B', '-m', 'venv', '--copies', str(runtime)])
        run([str(python), '-I', '-B', '-m', 'pip', 'install', '--isolated', '--no-index', '--no-cache-dir',
             '--no-compile', '--find-links', str(work), '--require-hashes', '-r', str(private / 'requirements.txt')])
        run([str(python), '-I', '-B', '-m', 'pip', 'check'])
        run([str(monkey), '--version'])
        print('Monkey setup: recording installed file hashes', file=sys.stderr, flush=True)
        record = {'schema': 'monkey.bundle-installation.v2', 'binding': binding, 'commands': commands,
                  'runtime': runtime_tree(runtime), 'physical_terminal_ui_tested': False,
                  'bytecode_policy':'Default __pycache__ ignored; checked launches use an empty private cache prefix and disable writes',
                  'model_generation_tested': False, 'business_writes': 0}
        if os.name == 'nt':
            record['windows_architecture'] = windows_architecture()
            record['execution_mode'] = ('Windows 11 ARM64 with x64 Python emulation' if
                manifest['target'] == 'windows11-arm64-x64-py311' else 'native Windows interpreter')
        fs.write_private(receipt, encode(record))
        return private, python, monkey, record
    finally:
        with_lease.close()


@contextmanager
def no_bytecode_cache(private, fs, environment):
    # A distinct, previously absent prefix also prevents reading a poisoned or
    # stale default cache. -B alone prevents writes, but still permits reads.
    prefix = private / ('cache-empty-' + uuid.uuid4().hex)
    require(not prefix.exists(), 'Cache prefix must be new')
    fs.private_directory(prefix)
    env = dict(environment)
    env.update(PYTHONPYCACHEPREFIX=str(prefix), PYTHONDONTWRITEBYTECODE='1')
    try:
        yield env, prefix
    finally:
        # Do not recursively erase unexpected files. A nonempty prefix is
        # retained, and cleanup failure is visible to the operator.
        prefix.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Check bundle bytes and target without installing')
    mode.add_argument('--install-only', action='store_true')
    mode.add_argument('--qualify', action='store_true', help='Run console/state/audit checks with a private fixture')
    parser.add_argument('--manifest-sha256', help='Expected SHA-256 supplied separately with the bundle')
    parser.add_argument('arguments', nargs=argparse.REMAINDER, help='Monkey arguments after --')
    args = parser.parse_args(argv)
    root = Path(__file__).absolute().parent
    manifest, manifest_hash, payload = inspect_bundle(root, args.manifest_sha256)
    if args.check:
        print(json.dumps({'bundle_sha256': manifest_hash, 'target': manifest['target'],
                          'artifacts_checked': len(payload), 'installed': False}))
        return 0
    require(sys.flags.isolated, 'Use Python -I when starting the deployment bundle')
    fs = filesystem(payload)
    private, python, monkey, receipt = install(root, manifest, manifest_hash, payload, fs)
    if args.install_only:
        print(json.dumps({'command': str(monkey), 'state': str(private / 'state'),
                          'receipt': str(private / 'receipt.json'), 'target': manifest['target']}))
        return 0
    env = dict(os.environ)
    env.pop('PYTHONHOME', None); env.pop('PYTHONPATH', None)
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    with no_bytecode_cache(private, fs, env) as (env, prefix):
        if args.qualify:
            require(fs.read_regular(private / 'qualify_cli.py', 1024 * 1024, private=True)
                    == payload['qualify_cli.py'], 'Installed qualification script changed')
            output = private / ('qualification-' + uuid.uuid4().hex)
            return subprocess.call([str(python), '-I', '-B', '-X', 'pycache_prefix=' + str(prefix),
                                    str(private / 'qualify_cli.py'), '--monkey', str(monkey),
                                    '--python', str(python), '--output', str(output)], env=env)
        forwarded = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
        # Foreground child owns its state lease; closing this terminal never starts
        # a service. The bundle's candidate state is explicit on every launch.
        print('Monkey candidate · ' + manifest['target'] + '\nState: ' + str(private / 'state'), flush=True)
        return subprocess.call([str(monkey), '--state', str(private / 'state'), *forwarded], env=env)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print('Monkey setup: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
