"""Win32 state files: protected ACLs, held directory handles, no reparse points.

This module is selected only on Windows. Paths are local drive paths. Holding
every ancestor without FILE_SHARE_DELETE prevents replacement during an operation.
The current user's SID and SYSTEM are the only admitted ACL trustees.
"""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from functools import lru_cache
import os
from pathlib import Path
import re
import uuid

from jira_monkey import require

require(os.name == 'nt', 'Win32 file support requires Windows')
import msvcrt

k = c.WinDLL('kernel32', use_last_error=True)
a = c.WinDLL('advapi32', use_last_error=True)
P = c.c_void_p
H = w.HANDLE
INVALID = c.c_void_p(-1).value


class SECURITY_ATTRIBUTES(c.Structure):
    _fields_ = [('length', w.DWORD), ('descriptor', P), ('inherit', w.BOOL)]


class FILE_INFO(c.Structure):
    _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME),
                ('accessed', w.FILETIME), ('written', w.FILETIME),
                ('volume', w.DWORD), ('size_high', w.DWORD), ('size_low', w.DWORD),
                ('links', w.DWORD), ('id_high', w.DWORD), ('id_low', w.DWORD)]


class ACL(c.Structure):
    _fields_ = [('revision', w.BYTE), ('reserved', w.BYTE), ('size', w.WORD),
                ('count', w.WORD), ('reserved2', w.WORD)]


def api(dll, name, result, parameters):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, parameters
    return fn


CreateFile = api(k, 'CreateFileW', H, [w.LPCWSTR, w.DWORD, w.DWORD, P, w.DWORD, w.DWORD, H])
CloseHandle = api(k, 'CloseHandle', w.BOOL, [H])
GetFileInfo = api(k, 'GetFileInformationByHandle', w.BOOL, [H, c.POINTER(FILE_INFO)])
CreateDirectory = api(k, 'CreateDirectoryW', w.BOOL, [w.LPCWSTR, P])
MoveFile = api(k, 'MoveFileExW', w.BOOL, [w.LPCWSTR, w.LPCWSTR, w.DWORD])
LocalFree = api(k, 'LocalFree', H, [H])
GetProcess = api(k, 'GetCurrentProcess', H, [])
DriveType = api(k, 'GetDriveTypeW', w.UINT, [w.LPCWSTR])
GetVolume = api(k, 'GetVolumeInformationW', w.BOOL,
                [w.LPCWSTR, w.LPWSTR, w.DWORD, P, P, c.POINTER(w.DWORD), w.LPWSTR, w.DWORD])
OpenToken = api(a, 'OpenProcessToken', w.BOOL, [H, w.DWORD, c.POINTER(H)])
GetToken = api(a, 'GetTokenInformation', w.BOOL, [H, c.c_int, P, w.DWORD, c.POINTER(w.DWORD)])
SidString = api(a, 'ConvertSidToStringSidW', w.BOOL, [P, c.POINTER(w.LPWSTR)])
ParseDescriptor = api(a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', w.BOOL,
                      [w.LPCWSTR, w.DWORD, c.POINTER(P), P])
GetSecurity = api(a, 'GetSecurityInfo', w.DWORD,
                  [H, c.c_int, w.DWORD, c.POINTER(P), P, c.POINTER(P), P, c.POINTER(P)])
GetControl = api(a, 'GetSecurityDescriptorControl', w.BOOL, [P, c.POINTER(w.WORD), c.POINTER(w.DWORD)])
GetAce = api(a, 'GetAce', w.BOOL, [P, w.DWORD, c.POINTER(P)])


def checked(value):
    if not value:
        raise c.WinError(c.get_last_error())
    return value


def sid_text(pointer):
    text = w.LPWSTR()
    checked(SidString(pointer, c.byref(text)))
    try:
        return text.value
    finally:
        LocalFree(c.cast(text, H))


@lru_cache(maxsize=1)
def user_sid():
    token = H()
    checked(OpenToken(GetProcess(), 0x0008, c.byref(token)))
    try:
        size = w.DWORD()
        GetToken(token, 1, None, 0, c.byref(size))
        require(0 < size.value <= 16384, 'Invalid Windows token size')
        data = c.create_string_buffer(size.value)
        checked(GetToken(token, 1, data, size, c.byref(size)))
        return sid_text(c.cast(data, c.POINTER(P))[0])
    finally:
        CloseHandle(token)


@contextmanager
def security_attributes(directory=False):
    sid = user_sid()
    flags = 'OICI' if directory else ''
    descriptor = P()
    checked(ParseDescriptor(f'O:{sid}D:P(A;{flags};FA;;;{sid})(A;{flags};FA;;;SY)',
                            1, c.byref(descriptor), None))
    attributes = SECURITY_ATTRIBUTES(c.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
    try:
        yield c.byref(attributes)
    finally:
        LocalFree(descriptor)


def check_security(handle, *, directory=False):
    owner, dacl, descriptor = P(), P(), P()
    error = GetSecurity(handle, 1, 0x0001 | 0x0004,
                        c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor))
    if error:
        raise c.WinError(error)
    try:
        require(sid_text(owner) == user_sid(), 'Private state must be owned by the current Windows user')
        require(bool(dacl), 'Private state cannot have a null DACL')
        control, revision = w.WORD(), w.DWORD()
        checked(GetControl(descriptor, c.byref(control), c.byref(revision)))
        require(bool(control.value & 0x0004), 'Private state must have an explicit DACL')
        # Existing child directories may inherit this exact owner/SYSTEM ACL.
        # Every access validates its effective trustees; newly created state
        # directories and key files receive a protected descriptor explicitly.
        acl = c.cast(dacl, c.POINTER(ACL)).contents
        require(1 <= acl.count <= 2, 'Private state ACL must grant only its owner and SYSTEM')
        trustees = set()
        for index in range(acl.count):
            ace = P()
            checked(GetAce(dacl, index, c.byref(ace)))
            require(c.c_ubyte.from_address(ace.value).value == 0, 'Unsupported private state ACL entry')
            trustees.add(sid_text(P(ace.value + 8)))
        require(user_sid() in trustees and trustees <= {user_sid(), 'S-1-5-18'},
                'Private state ACL grants access outside its owner and SYSTEM')
    finally:
        LocalFree(descriptor)


def file_info(handle, *, directory=False):
    info = FILE_INFO()
    checked(GetFileInfo(handle, c.byref(info)))
    require(not info.attributes & 0x0400, 'Windows reparse points are refused')
    require(bool(info.attributes & 0x0010) == directory, 'Unexpected Windows file type')
    if not directory:
        require(info.links == 1, 'Hard-linked state files are refused')
    return info


def local_path(path):
    require('..' not in Path(path).parts,'Parent traversal in Windows path')
    path = Path(path).absolute()
    require(re.fullmatch('[A-Za-z]:', path.drive or '') and path.root == '\\',
            'Use an absolute local Windows drive path; network/device paths are unavailable')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    for part in path.parts[1:]:
        require(part not in {'.', '..'} and not part.endswith((' ', '.'))
                and not any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part)
                and part.split('.')[0].upper() not in reserved, 'Ambiguous or device Windows path refused')
    require(DriveType(path.anchor) == 3, 'Private state requires a fixed local Windows drive')
    flags = w.DWORD()
    checked(GetVolume(path.anchor, None, 0, None, None, c.byref(flags), None, 0))
    require(bool(flags.value & 0x00000008), 'State drive must support persistent file ACLs')
    return path


def open_handle(path, *, directory=False, write=False, create=False, always=False, share=1):
    with security_attributes(directory) as attributes:
        access = (0x00020000 | 0x00000080) if directory else (0x80000000 | (0x40000000 if write else 0))
        flags = 0x00200000 | (0x02000000 if directory else 0x00000080)
        handle = CreateFile(str(path), access, share, attributes,
                            4 if always else 1 if create else 3, flags, None)
    if handle == INVALID:
        raise c.WinError(c.get_last_error())
    try:
        file_info(handle, directory=directory)
        return handle
    except BaseException:
        CloseHandle(handle)
        raise


@contextmanager
def parent_guard(path, *, private=False, create=False):
    path = local_path(path)
    handles = []
    current = Path(path.anchor)
    try:
        handles.append(open_handle(current, directory=True, share=3))
        for part in path.parent.parts[1:]:
            current /= part
            try:
                handle = open_handle(current, directory=True, share=3)
            except FileNotFoundError:
                if not create:
                    raise
                with security_attributes(True) as attributes:
                    if not CreateDirectory(str(current), attributes) and c.get_last_error() != 183:
                        raise c.WinError(c.get_last_error())
                handle = open_handle(current, directory=True, share=3)
            handles.append(handle)
        if private:
            check_security(handles[-1], directory=True)
        yield current
    finally:
        for handle in reversed(handles):
            CloseHandle(handle)


def private_directory(path):
    with parent_guard(Path(path) / '.directory-check', private=True, create=True):
        pass


def read_regular(path, limit, *, private=False):
    path = local_path(path)
    with parent_guard(path, private=private):
        handle = open_handle(path)
        try:
            if private:
                check_security(handle)
            info = file_info(handle)
            require((info.size_high << 32) + info.size_low <= limit, 'File exceeds the supported bound')
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None
            with os.fdopen(fd, 'rb') as stream:
                data = stream.read(limit + 1)
            require(len(data) <= limit, 'File exceeds the supported bound')
            return data
        finally:
            if handle is not None:
                CloseHandle(handle)


def write_private(path, data, *, replace=False):
    path = local_path(path)
    with parent_guard(path, private=True, create=True):
        target = path.with_name('.private-' + uuid.uuid4().hex) if replace else path
        handle = open_handle(target, create=True, write=True, share=0)
        try:
            check_security(handle)
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            handle = None
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                checked(MoveFile(str(target), str(path), 0x0001 | 0x0008))
        finally:
            if handle is not None:
                CloseHandle(handle)
            if replace:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass


def check_state_file(path):
    path = local_path(path)
    with parent_guard(path, private=True):
        handle = open_handle(path, share=3)
        try:
            check_security(handle)
        finally:
            CloseHandle(handle)


class StateLease:
    def __init__(self, path):
        path = local_path(path)
        self.fd = None
        self.guard = parent_guard(path, private=True)
        self.guard.__enter__()
        handle = None
        try:
            handle = open_handle(path, always=True, write=True, share=3)
            check_security(handle)
            self.fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            handle = None
            try:
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError('Another Monkey owns this state directory') from exc
        except BaseException:
            if handle is not None:
                CloseHandle(handle)
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.guard is not None:
            self.guard.__exit__(None, None, None)
            self.guard = None
