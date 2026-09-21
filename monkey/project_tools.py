"""Descriptor-confined file tools and a bounded, read-only macOS test runner."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import re

from .common import Refused, require, identity
from .sml import environment, stop
from .platform_files import parent_guard

PROTECTED = {".git", ".kist", ".jira-monkey", ".ssh", ".aws", ".codex", ".env", ".netrc", ".npmrc"}


def runtime_executables(executable):
    resolved = Path(executable).resolve()
    paths = [resolved]
    if resolved==Path(sys.executable).resolve():
        base=Path(getattr(sys,'_base_executable',sys.executable)).resolve()
        if base!=resolved: paths.append(base)
    # Homebrew framework Python's tiny launcher replaces itself with this exact
    # framework binary. Grant that measured target, not arbitrary process-exec.
    for runtime in list(paths):
        native = runtime.parent.parent / 'Resources/Python.app/Contents/MacOS/Python'
        if runtime.name.startswith('python') and 'Python.framework' in runtime.parts and native.is_file():
            paths.append(native.resolve())
    return list(dict.fromkeys(paths))


def runtime_files(executable):
    paths=runtime_executables(executable)
    if Path(executable).resolve()==Path(sys.executable).resolve():
        config=Path(sys.prefix)/'pyvenv.cfg'
        if config.is_file(): paths.append(config.resolve())
    return list(dict.fromkeys(paths))


def relative(value):
    require(type(value) is str and value and len(value) < 1000 and all(ord(c)>=32 and ord(c)!=127 for c in value) and "\\" not in value, "Invalid project path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(p not in {"", ".", ".."} and p not in PROTECTED and not p.startswith((".env.",".monkey-build")) for p in value.split("/")),
            "Path escapes scope or names protected host/secret data")
    return path.parts


class ProjectFiles:
    def __init__(self, root, audit=None):
        self.audit = audit
        self.root = Path(root).expanduser().absolute()
        require(not any(p.startswith('.monkey-build') for p in self.root.parts),'Managed build state is not a project workspace')
        require(str(self.root) == str(self.root.resolve()) and self.root.is_dir(), "Select a real project directory, without linked ancestors")
        info=self.root.stat()
        self.root_identity=(info.st_dev,info.st_ino)

    @contextlib.contextmanager
    def parent(self, name):
        parts = relative(name)
        require(os.name=='posix','Native Windows project file tools are not implemented')
        with parent_guard(self.root/'.monkey-root-anchor') as (root,_):
            info=os.fstat(root)
            require((info.st_dev,info.st_ino)==self.root_identity,'Project root was replaced')
            fd=os.dup(root)
            try:
                for part in parts[:-1]:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = nxt
                yield fd, parts[-1]
            finally:
                os.close(fd)

    def read(self, name):
        if self.audit:
            self.audit.observe('file.read.requested',{'path':str(self.root/name)})
        try:
            result=self._read(name)
        except BaseException as exc:
            if self.audit: self.audit.observe('file.read.failed',{'path':str(self.root/name),'error_type':type(exc).__name__})
            raise
        if self.audit: self.audit.observe('file.read.completed',{'path':str(self.root/name),'sha256':result['sha256'],'mode':result['mode'],'missing':result['text'] is None})
        return result

    def _read(self, name):
        with self.parent(name) as (parent, leaf):
            try:
                fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                return {"path": name, "sha256": None, "text": None, "mode": None}
            with os.fdopen(fd, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and metadata.st_size <= 65536, "Project file must be a bounded, unlinked regular file")
                raw = stream.read(65537)
                after = os.fstat(stream.fileno())
                require(len(raw) <= 65536 and (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) ==
                        (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns), "File changed during capture")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise Refused("Choose UTF-8 source files; binary editing is unavailable") from None
            return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "text": text, "mode": stat.S_IMODE(metadata.st_mode)}

    def write(self, name, text, expected):
        if self.audit:
            self.audit.observe('file.write.requested',{'path':str(self.root/name),'before_sha256':expected,
                'proposed_sha256':hashlib.sha256(text.encode()).hexdigest() if type(text) is str else None})
        try:
            result=self._write(name,text,expected)
        except BaseException as exc:
            if self.audit: self.audit.observe('file.write.failed',{'path':str(self.root/name),'error_type':type(exc).__name__,'outcome':'inspect retained reads; a local error does not undo prior effects'})
            raise
        if self.audit: self.audit.observe('file.write.completed',{'path':str(self.root/name),'after_sha256':result['sha256']})
        return result

    def _write(self, name, text, expected):
        require(type(text) is str and len(text.encode()) <= 65536, "Replacement file exceeds tool limit")
        require(self.read(name)["sha256"] == expected, "File changed since the approved plan: " + name)
        with self.parent(name) as (parent, leaf):
            temp = ".monkey-" + identity()
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(text.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                current = self.read(name)
                require(current["sha256"] == expected, "Concurrent edit detected; replacement stopped")
                os.chmod(temp, current["mode"] or 0o644, dir_fd=parent, follow_symlinks=False)
                os.rename(temp, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temp, dir_fd=parent)
        actual = self.read(name)
        require(actual["text"] == text, "Write read-back mismatch")
        return actual

    def listing(self):
        rows=[]
        counts={'entries':0,'directories':0,'skipped':0}
        truncated=False
        excluded=PROTECTED | {'node_modules','__pycache__','.venv','.build'}
        def visit(fd,prefix,depth):
            nonlocal truncated
            if depth>24 or counts['directories']>=300 or counts['entries']>=10000:
                truncated=True;return
            counts['directories']+=1
            entries=[]
            with os.scandir(fd) as scan:
                for entry in scan:
                    counts['entries']+=1
                    if counts['entries']>10000:
                        truncated=True;break
                    try: mode=entry.stat(follow_symlinks=False).st_mode
                    except OSError:
                        counts['skipped']+=1;truncated=True;continue
                    entries.append((entry.name,mode))
            if self.audit:
                self.audit.observe('directory.listed',{'path':str(self.root/prefix),'entry_count':len(entries),
                    'root_identity':list(self.root_identity),'bounded':True})
            for leaf,mode in sorted(entries):
                name=prefix+'/'+leaf if prefix else leaf
                try: relative(name)
                except Refused: continue
                if stat.S_ISREG(mode):
                    rows.append(name)
                    if len(rows)>=300:
                        truncated=True;return
                elif stat.S_ISDIR(mode) and leaf not in excluded:
                    try: child=os.open(leaf,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    except OSError:
                        counts['skipped']+=1;truncated=True;continue
                    try: visit(child,name,depth+1)
                    finally: os.close(child)
                    if len(rows)>=300: return
        with self.parent('.monkey-root-anchor') as (root,_):
            visit(root,'',0)
        return {'files':rows,'truncated':truncated,'inspected_entries':counts['entries'],
            'inspected_directories':counts['directories'],'unreadable_entries':counts['skipped']}


async def run_test(root, argv, scratch, *, timeout=45, audit=None):
    require(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "A verified macOS sandbox is required for project tests")
    require(type(argv) is list and 1 <= len(argv) <= 24 and all(type(a) is str and len(a) < 2000 and "\x00" not in a for a in argv), "Invalid bounded test arguments")
    require(Path(argv[0]).is_absolute() and Path(argv[0]).is_file(), "Verification executable must be an exact installed absolute path")
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=False, mode=0o700)
    root = Path(root).resolve()
    quote = json.dumps
    # Deny by default: no network, host state, process inspection, shell access to
    # other workspaces or shared temp. The selected project is READ-ONLY during
    # verification, so tests cannot change their subject or their own evidence.
    allowed = ["/System", "/usr", "/bin", "/sbin", "/Library/Developer", "/Library/Apple", "/opt/homebrew", "/private/var/db/dyld", "/dev/null", "/dev/urandom", "/dev/random"]
    lines = ["(version 1)", "(deny default)", "(allow sysctl-read)",
             "(allow file-read-metadata)", "(allow file-read* (literal \"/\"))", "(allow signal (target self))"]
    lines += ["(allow process-exec (literal " + quote(str(p)) + "))" for p in runtime_executables(argv[0])]
    for path in [*allowed, str(root), str(scratch), str(Path(argv[0]).resolve().parent)]:
        lines.append("(allow file-read* (subpath " + quote(path) + "))")
    # A copied virtual-environment interpreter reads this exact configuration
    # during startup. It is part of the pinned runtime, never an exec grant.
    for path in runtime_files(argv[0]):
        lines.append('(allow file-read* (literal '+quote(str(path))+'))')
    for name in PROTECTED:
        pattern='(^|/)'+re.escape(name).replace('\\.','[.]')+'(/|$)'
        lines.append('(deny file-read* (regex #'+quote(pattern)+'))')
    lines.append('(deny file-read* (regex #"(^|/)[.]env[.]"))')
    lines += ["(allow file-write* (subpath " + quote(str(scratch)) + "))", "(allow file-write* (literal \"/dev/null\"))"]
    env = environment()
    env.update(HOME=str(scratch), TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    if audit: audit.observe('verifier.spawn.requested',{'executable':argv[0],'argv_hash':hashlib.sha256(json.dumps(argv).encode()).hexdigest(),'project':str(root),'sandbox':'read-only project; no network or fork'})
    process = await asyncio.create_subprocess_exec("/usr/bin/sandbox-exec", "-p", "\n".join(lines), *argv,
        cwd=root, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    if audit: audit.observe('verifier.started',{'pid':process.pid,'timezone':'UTC','locale':'en_US.UTF-8'})
    chunks = bytearray()
    try:
        async with asyncio.timeout(timeout):
            while part := await process.stdout.read(4096):
                chunks.extend(part)
                require(len(chunks) <= 50000, "Test output limit exceeded; no passing receipt")
            await process.wait()
        if audit: audit.observe('verifier.completed',{'pid':process.pid,'exit_code':process.returncode,'output_sha256':hashlib.sha256(chunks).hexdigest(),'output_bytes':len(chunks)})
        return {"argv": argv, "exit_code": process.returncode, "output": chunks.decode(errors="replace"),
                "output_sha256": hashlib.sha256(chunks).hexdigest(), "sandbox": "macOS deny-default; project read-only; network and fork denied", "timeout_seconds": timeout,
                "environment":{"timezone":"UTC","locale":"en_US.UTF-8","home":"private scratch directory"}}
    except TimeoutError:
        await stop(process)
        evidence={'pid':process.pid,'exit_code':process.returncode,'output':chunks.decode(errors='replace'),
            'output_sha256':hashlib.sha256(chunks).hexdigest(),'output_bytes':len(chunks),'timed_out':True}
        if audit: audit.observe('verifier.timed_out',evidence)
        return {**evidence,'argv':argv,'timeout_seconds':timeout,'sandbox':'macOS deny-default; project read-only; network and fork denied',
            'environment':{'timezone':'UTC','locale':'en_US.UTF-8','home':'private scratch directory'},
            'note':'The verifier exceeded its own time limit. Partial output is retained; no successful check is claimed.'}
    finally:
        # Kill descendants even if the parent already exited. Detached sessions
        # are denied by the sandbox; no background tool capability is admitted.
        if process.returncode is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, 9)
        await stop(process)
        if audit: audit.observe('verifier.stopped',{'pid':process.pid,'exit_code':process.returncode})
