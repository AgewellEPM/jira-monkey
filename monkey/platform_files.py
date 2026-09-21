"""Private state I/O and process identity, with native POSIX/Windows enforcement.

Windows support is loaded only on Windows. No chmod emulation, shared-directory
fallback, remote state drive, or reparse-point traversal is accepted there.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid

from jira_monkey import require


def operator_identity():
    import getpass
    if os.name == 'nt':
        from .windows_files import user_sid
        return 'sid:' + user_sid() + ':' + getpass.getuser()
    return 'uid:' + str(os.getuid()) + ':' + getpass.getuser()


def private_directory(path):
    require('..' not in Path(path).parts, 'Parent traversal in private path')
    path = Path(path).absolute()
    if os.name == 'nt':
        from .windows_files import private_directory as create
        return create(path)
    require(not any(p.is_symlink() for p in [path, *path.parents]), 'Refusing linked state path')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            'State directory must be owned by you with mode 0700')


@contextmanager
def parent_guard(path, *, private=False, create=False):
    require('..' not in Path(path).parts, 'Parent traversal in file path')
    path = Path(path).absolute()
    if os.name == 'nt':
        from .windows_files import parent_guard as guard
        with guard(path, private=private, create=create) as parent:
            yield parent, path.name
        return
    require('..' not in path.parts, 'Parent traversal in file path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=fd)
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        meta = os.fstat(fd)
        if private:
            require(meta.st_uid == os.getuid() and stat.S_IMODE(meta.st_mode) == 0o700,
                    'Private file directory must be owned by you with mode 0700')
        yield fd, path.name
    finally:
        os.close(fd)


def _check_file(fd, *, private=False):
    meta = os.fstat(fd)
    require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1, 'Select an unlinked regular file')
    if private:
        require(meta.st_uid == os.getuid() and stat.S_IMODE(meta.st_mode) == 0o600,
                'Private file must be owned by you with mode 0600')
    return meta


def read_regular(path, limit, *, private=False):
    if os.name == 'nt':
        from .windows_files import read_regular as read
        return read(path, limit, private=private)
    with parent_guard(path, private=private) as (parent, leaf):
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            meta = _check_file(stream.fileno(), private=private)
            require(meta.st_size <= limit, 'File exceeds the supported bound')
            data = stream.read(limit + 1)
            require(len(data) <= limit, 'File exceeds the supported bound')
            return data


def write_private(path, data, *, replace=False):
    require(type(data) is bytes, 'Private file writes require bytes')
    if os.name == 'nt':
        from .windows_files import write_private as write
        return write(path, data, replace=replace)
    with parent_guard(path, private=True, create=True) as (parent, leaf):
        target = '.private-' + uuid.uuid4().hex if replace else leaf
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(target, leaf, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            if replace:
                try:
                    os.unlink(target, dir_fd=parent)
                except FileNotFoundError:
                    pass


def check_state_file(path):
    """Validate an existing SQLite file before SQLite opens it under our lease."""
    if os.name == 'nt':
        from .windows_files import check_state_file as check
        return check(path)
    with parent_guard(path, private=True) as (parent, leaf):
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            meta = _check_file(fd)
            require(meta.st_uid == os.getuid(), 'Unsafe database owner')
        finally:
            os.close(fd)


class StateLease:
    """An exclusive process lease, held until the SQLite connection has closed."""
    def __init__(self, path):
        self.fd = None
        self.windows = None
        if os.name == 'nt':
            from .windows_files import StateLease as Lease
            self.windows = Lease(path)
            self.fd = self.windows.fd
            return
        import fcntl
        with parent_guard(path, private=True) as (parent, leaf):
            self.fd = os.open(leaf, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            _check_file(self.fd, private=True)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.windows:
            self.windows.close()
            self.windows = None
        elif self.fd is not None:
            os.close(self.fd)
        self.fd = None
