"""Confined MCP subprocess transport. Protocol negotiation remains in the official SDK."""
import asyncio
import ctypes
from contextlib import asynccontextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import sys
import time

from .common import Refused, identity, require
from .project_tools import PROTECTED, runtime_executables
from .security import strict_json
from .sml import environment, stop

MESSAGE_LIMIT = 1500000
SESSION_LIMIT = 8000000
MEMORY_LIMIT = 384 * 1024 * 1024
SESSION_SECONDS = 45


def resident_bytes(pid):
    # macOS sys/proc_info.h: PROC_PIDTASKINFO=4, two leading uint64_t sizes.
    library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    library.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
    library.proc_pidinfo.restype=ctypes.c_int
    buffer=ctypes.create_string_buffer(1024)
    count=library.proc_pidinfo(pid,4,0,buffer,len(buffer))
    return struct.unpack_from('QQ',buffer.raw)[1] if count>=16 else 0


def sandbox_config(value, state):
    require(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file(),
        'A verified macOS sandbox is required for local MCP programs; use an HTTP MCP endpoint on other systems')
    require(resident_bytes(os.getpid())>0,'MCP process memory observation is unavailable; local program launch is blocked')
    value = value or {}
    require(type(value) is dict and not set(value) - {'read', 'write', 'network'}, 'Use explicit sandbox read, write and network grants')
    result = {'read': [], 'write': [], 'network': []}
    state = Path(state).resolve()
    forbidden = [Path.home(), state, Path.home()/'.local/share/jira-monkey', Path(__file__).resolve().parents[1], Path(sys.prefix).resolve()]
    protected = [Path.home()/name for name in PROTECTED]
    for kind in ('read', 'write'):
        paths = value.get(kind, [])
        require(type(paths) is list and len(paths) <= 16, 'Bound MCP filesystem grants to 16 exact paths')
        for path in paths:
            require(type(path) is str and len(path) < 2000 and '\x00' not in path, 'Invalid MCP filesystem grant')
            p = Path(path)
            require(p.is_absolute() and str(p) == str(p.resolve()) and p.parent.is_dir(), 'MCP grants need exact paths without linked ancestors')
            require(not any(p == root or root.is_relative_to(p) for root in forbidden)
                and not p.is_relative_to(state) and not any(p.is_relative_to(root) for root in protected), 'MCP grants cannot expose host state, credentials or the whole home')
            require(not any(part in PROTECTED or part.startswith('.env.') for part in p.parts), 'Protected credential or guidance path in MCP grant')
            if kind == 'write':
                require(not any(p.is_relative_to(root) for root in [*forbidden[2:],Path('/System'),Path('/usr'),Path('/Library'),Path('/opt/homebrew')]), 'MCP programs cannot modify Monkey or installed runtimes')
            if p.exists():
                require(p.is_dir() or (p.is_file() and p.stat().st_nlink == 1), 'MCP grant must be an unlinked file or directory')
                require(kind!='write' or p.is_file(), 'MCP writes require exact files; directory-wide writes have no enforceable total storage bound')
            result[kind].append(str(p))
    endpoints = value.get('network', [])
    require(type(endpoints) is list and len(endpoints) <= 16, 'Bound MCP network grants to 16 explicit host:port endpoints')
    for endpoint in endpoints:
        require(type(endpoint) is str and re.fullmatch(r'[A-Za-z0-9.-]+:[0-9]{1,5}', endpoint), 'Use an exact network host:port; no wildcards')
        host, port = endpoint.rsplit(':', 1)
        require(1 <= int(port) <= 65535, 'Invalid MCP network port')
        require(host in {'localhost','127.0.0.1'}, 'macOS exposes a loopback-port sandbox filter, not arbitrary destination IP filters; use HTTP MCP for remote services')
        # Resolve once into the sealed connection recipe. Reconnect after an address change.
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)})
        require(0 < len(addresses) <= 16, 'Unexpected network resolution size')
        require(all(ipaddress.ip_address(address).is_loopback for address in addresses),'Local MCP endpoint resolved outside loopback')
        result['network'].extend({'host': host, 'port': int(port), 'address': str(ipaddress.ip_address(address))} for address in addresses)
    return result


def profile(config, scratch):
    quote = json.dumps
    rules = ['(version 1)', '(deny default)', '(allow sysctl-read)', '(allow file-read-metadata)',
        '(allow file-read* (literal "/"))', '(allow signal (target self))']
    for executable in runtime_executables(config['command']):
        rules.append('(allow process-exec (literal '+quote(str(executable))+'))')
    # Runtime libraries only. No user home, sibling projects, host state or shared temp.
    runtime = ['/System', '/usr', '/Library/Apple', '/Library/Developer', '/opt/homebrew',
        '/private/var/db/dyld', str(Path(sys.prefix).resolve()), str(scratch)]
    for path in runtime:
        rules.append('(allow file-read* (subpath '+quote(path)+'))')
    for path in ['/dev/null', '/dev/random', '/dev/urandom', config['command'], *config.get('program_files', [])]:
        rules.append('(allow file-read* (literal '+quote(path)+'))')
    for kind in ('read', 'write'):
        for path in config['sandbox'][kind]:
            selector = 'subpath' if Path(path).is_dir() else 'literal'
            rules.append('(allow file-read* ('+selector+' '+quote(path)+'))')
            if kind == 'write':
                rules.append('(allow file-write* ('+selector+' '+quote(path)+'))')
    # The private home is read-only. Each of at most 16 explicit write files has
    # an 8 MiB kernel file-size limit; peers cannot create unlimited temp files.
    rules += ['(allow file-write* (literal "/dev/null"))']
    for name in PROTECTED:
        pattern='(^|/)'+re.escape(name).replace('\\.','[.]')+'(/|$)'
        rules.append('(deny file-read* (regex #'+quote(pattern)+'))')
    rules.append('(deny file-read* (regex #"(^|/)[.]env[.]"))')
    if config['sandbox']['network']:
        # Peers must use loopback addresses: granting the system resolver
        # would also allow arbitrary DNS queries outside the endpoint scope.
        for port in sorted({row['port'] for row in config['sandbox']['network']}):
            rules.append('(allow network-outbound (remote tcp '+quote('localhost:'+str(port))+'))')
    return '\n'.join(rules)


@asynccontextmanager
async def confined_stdio(config, root, audit=None):
    import anyio
    from mcp.shared.message import SessionMessage
    from mcp.types import jsonrpc_message_adapter

    scratch = Path(root)/'connector-runs'/identity('session_')
    from jira_monkey import private_directory
    private_directory(scratch)
    env = environment()
    env.update(config.get('env', {}))
    env.update(HOME=str(scratch), TMPDIR=str(scratch), PYTHONNOUSERSITE='1', PYTHONSAFEPATH='1', PYTHONDONTWRITEBYTECODE='1')
    process = None
    tasks = []
    peak = 0
    memory_limit, session_seconds = MEMORY_LIMIT, SESSION_SECONDS
    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)
    try:
        if audit: audit.observe('mcp_process.spawn.requested',{'executable':config['command'],'program_files':config.get('program_files',[]),'sandbox':config['sandbox'],
            'scratch':str(scratch),'argv_hash':hashlib.sha256(json.dumps(config.get('args',[])).encode()).hexdigest(),
            'memory_limit_bytes':memory_limit,'session_limit_seconds':session_seconds})
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', str(Path(__file__).with_name('stdio_child.py')),
            '-p', profile(config, scratch), config['command'], *config.get('args', []),
            cwd=config.get('cwd') or scratch, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=MESSAGE_LIMIT,
            start_new_session=True)
        if audit: audit.observe('mcp_process.started',{'pid':process.pid,'scratch':str(scratch)})
        async def receive():
            total = 0
            try:
                async with read_writer:
                    while raw := await process.stdout.readline():
                        total += len(raw)
                        require(total <= SESSION_LIMIT, 'MCP session output exceeds the retained bound')
                        data = strict_json(raw, limit=MESSAGE_LIMIT)
                        message = jsonrpc_message_adapter.validate_python(data, by_name=False)
                        await read_writer.send(SessionMessage(message))
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass
            except Exception:
                # Never copy a peer's malformed line or token into diagnostics.
                await stop(process)
        async def transmit():
            try:
                async with write_reader:
                    async for message in write_reader:
                        data = message.message.model_dump_json(by_alias=True, exclude_unset=True).encode()+b'\n'
                        require(len(data) <= MESSAGE_LIMIT, 'MCP request exceeds the transport bound')
                        process.stdin.write(data)
                        await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                await read_writer.aclose()
        async def watchdog():
            nonlocal peak
            started=time.monotonic()
            missing=0
            while process.returncode is None:
                size=resident_bytes(process.pid)
                peak=max(peak,size)
                missing=missing+1 if not size else 0
                elapsed = time.monotonic()-started
                reason = ('resident_memory_limit' if size>memory_limit else
                          'session_deadline' if elapsed>session_seconds else
                          'lost_process_observation' if missing>=3 else None)
                if reason:
                    if audit: audit.observe('mcp_process.limit',{'pid':process.pid,'resident_bytes':size,'reason':reason,
                        'memory_limit_bytes':memory_limit,'session_limit_seconds':session_seconds,
                        'elapsed_seconds':elapsed,'missing_observations':missing})
                    await stop(process)
                    return
                await asyncio.sleep(.05)
        tasks = [asyncio.create_task(receive()), asyncio.create_task(transmit()),asyncio.create_task(watchdog())]
        yield read_stream, write_stream
    finally:
        # SDK negotiation failure can unwind under an active AnyIO cancellation
        # scope. Shield cleanup so that cancellation cannot skip process reaping
        # or leave the private scratch directory behind.
        with anyio.CancelScope(shield=True):
            try:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if process:
                    # No process-fork or unapproved exec is admitted by the sandbox.
                    await stop(process)
            finally:
                try:
                    for stream in (read_writer, read_stream, write_stream, write_reader):
                        await stream.aclose()
                finally:
                    shutil.rmtree(scratch)
            if process and audit:
                audit.observe('mcp_process.stopped',{'pid':process.pid,'exit_code':process.returncode,'peak_observed_resident_bytes':peak,
                    'scratch_removed':True,'memory_limit_bytes':memory_limit,'session_limit_seconds':session_seconds,
                    'memory_limit_kind':'RSS polling watchdog, not a kernel memory quota'})
