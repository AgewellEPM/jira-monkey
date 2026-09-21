"""Report implemented host capabilities separately from native qualification."""
import os
from pathlib import Path
import platform
import sys


def report():
    native=sys.platform=='darwin' and Path('/usr/bin/sandbox-exec').is_file()
    files=os.name=='posix'
    boundary='One process; project read-only; scratch writable; no network or fork'
    return {'system':platform.system(),'release':platform.release(),'machine':platform.machine(),
        'python':platform.python_version(),
        'private_state':{'implementation':'Win32 SID/ACL/held handles' if os.name=='nt' else 'POSIX ownership/modes/descriptors',
            'qualification':('Five Windows 10 x64 file/state checks passed on dev8; current candidate and Windows 11 qualification pending'
                             if os.name=='nt' else
                             'Dev8 macOS ARM64 and Linux ARM64 CLI/state fixtures passed; qualification is specific to those artifacts, not all POSIX hosts')},
        'project_files':{'available':files,'reason':None if files else 'Windows project file tools are not implemented'},
        'commands':{'available':native,'backend':'macOS sandbox-exec' if native else None,
            'boundary':boundary if native else 'No qualified command containment backend on this platform'},
        'mcp_stdio':{'available':native,'reason':None if native else 'Use a configured HTTP MCP endpoint; local process containment is unavailable'},
        'mcp_http':{'implemented':True,'native_qualification':'macOS transport fixtures recorded; Linux and Windows HTTP integration tests pending'},
        'windows_accessibility':{'configured_by_monkey':False,'required_stack':'Perslis/GhostBridge',
            'qualification':'Windows 10/11 native sessions have not been qualified for this release'},
        'enterprise_qualified':False}


def require_commands():
    from .common import require
    value=report()['commands']
    require(value['available'],value['boundary']+'; coding execution is unavailable. Ticket drafting, HTTP MCP/API and public research can be used separately.')
