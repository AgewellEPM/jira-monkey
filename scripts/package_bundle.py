"""Build an offline wheel bundle for one explicit platform; never execute wheels.

Packaging is not native platform qualification. The manifest records that fact.
Downloads are restricted to pinned requirements and PyPI binary distributions.
"""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.tags import cpython_tags, compatible_tags, mac_platforms
from pip._vendor.packaging.utils import parse_wheel_filename

ROOT=Path(__file__).resolve().parents[1]
TARGETS={
    'macos-arm64-py311':('3.11','darwin','Darwin','arm64',['macosx_13_0_arm64']),
    'linux-x64-py311':('3.11','linux','Linux','x86_64',['manylinux_2_28_x86_64','manylinux_2_17_x86_64','manylinux2014_x86_64']),
    'linux-arm64-py311':('3.11','linux','Linux','aarch64',['manylinux_2_28_aarch64','manylinux_2_17_aarch64','manylinux2014_aarch64']),
    'windows-x64-py311':('3.11','win32','Windows','AMD64',['win_amd64']),
    'windows11-arm64-x64-py311':('3.11','win32','Windows','AMD64',['win_amd64']),
    'windows-arm64-py313':('3.13','win32','Windows','ARM64',['win_arm64']),
}


def canonical(name):
    return re.sub('[-_.]+','-',name).lower()


def environment(target):
    version,system,platform,machine,_=TARGETS[target]
    return {**default_environment(),'python_version':version,'python_full_version':version+'.0',
        'sys_platform':system,'platform_system':platform,'platform_machine':machine,
        'os_name':'nt' if system=='win32' else 'posix','extra':''}


def metadata(path):
    with zipfile.ZipFile(path) as archive:
        names=[name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(names)!=1:
            raise ValueError('Wheel must have exactly one metadata record')
        data=BytesParser().parsebytes(archive.read(names[0]))
    return {'name':canonical(data['Name']),'version':data['Version'],'file':path.name,
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,
        'license_metadata':data.get('License-Expression') or data.get('License') or 'Not declared',
        'requires_python':data.get('Requires-Python',''),
        'requires':data.get_all('Requires-Dist',[])}


def check_compatibility(packages,target):
    version,system,_,machine,platforms=TARGETS[target]
    if system=='darwin':
        minimum=tuple(int(part) for part in platforms[0].split('_')[1:3])
        platforms=list(mac_platforms(minimum,machine))
    python=tuple(int(part) for part in version.split('.'))
    interpreter='cp'+version.replace('.','')
    tags=set(cpython_tags(python,[interpreter],platforms))
    tags.update(compatible_tags(python,interpreter,platforms))
    for package in packages:
        _,_,_,wheel_tags=parse_wheel_filename(package['file'])
        if not tags.intersection(wheel_tags):
            raise ValueError('Wheel does not match target interpreter: '+package['file'])
        if version+'.0' not in SpecifierSet(package['requires_python']):
            raise ValueError('Package requires a different Python: '+package['name'])


def check_closure(packages,target):
    env=environment(target)
    indexed={p['name']:p for p in packages}
    extras={name:{''} for name in indexed}
    changed=True
    while changed:
        changed=False
        for package in packages:
            for raw in package['requires']:
                request=Requirement(raw)
                if request.marker and not any(request.marker.evaluate({**env,'extra':extra}) for extra in extras[package['name']]):
                    continue
                name=canonical(request.name)
                if name not in indexed or indexed[name]['version'] not in request.specifier:
                    raise ValueError('Incomplete pinned bundle: '+package['name']+' requires '+raw)
                addition=set(request.extras)-extras[name]
                if addition:
                    extras[name].update(addition);changed=True


def run(argv,folder,env):
    with (folder/'commands.jsonl').open('a') as stream:
        stream.write(json.dumps({'argv':argv})+'\n')
    process=subprocess.run(argv,cwd=folder,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
    with (folder/'build.log').open('ab') as stream:
        stream.write(process.stdout)
    if process.returncode:
        raise RuntimeError('Packaging command failed; inspect '+str(folder/'build.log'))


def build(target,folder):
    folder=Path(folder).expanduser().absolute()
    folder.mkdir(parents=True,exist_ok=False,mode=0o700)
    wheels=folder/'wheels';wheels.mkdir()
    source=folder/'source';source.mkdir()
    for name in ('jira_monkey.py','pyproject.toml','README.md','requirements.txt'):
        shutil.copyfile(ROOT/name,source/name)
    shutil.copytree(ROOT/'monkey',source/'monkey',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    source_hashes={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
    version,_,_,_,platforms=TARGETS[target]
    selected=[]
    for raw in (ROOT/'requirements-lock.txt').read_text().splitlines():
        if not raw.strip() or raw.startswith('#'): continue
        request=Requirement(raw)
        if request.marker and not request.marker.evaluate(environment(target)): continue
        if not re.fullmatch(r'==[A-Za-z0-9.]+',str(request.specifier)) or request.url:
            raise ValueError('Bundle requirements must be exact version pins')
        selected.append(request.name+str(request.specifier))
    pins=folder/'selected-requirements.txt';pins.write_text('\n'.join(selected)+'\n')
    netrc=folder/'empty.netrc';netrc.write_text('')
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','HOME','USER','USERNAME','LOGNAME','SYSTEMROOT','WINDIR','USERPROFILE','HOMEDRIVE','HOMEPATH','TEMP','TMP','TMPDIR','LANG'}}
    env.update(PIP_CONFIG_FILE=os.devnull,PIP_NO_INPUT='1',PIP_DISABLE_PIP_VERSION_CHECK='1',
        PIP_CACHE_DIR=str(folder/'cache'),PIP_KEYRING_PROVIDER='disabled',NETRC=str(netrc))
    try:
        run([sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation','--no-index',
            '--wheel-dir',str(wheels),str(source)],folder,env)
        arguments=[sys.executable,'-m','pip','download','--no-deps','--only-binary=:all:',
            '--index-url','https://pypi.org/simple','--python-version',version.replace('.',''),
            '--implementation','cp','--abi','cp'+version.replace('.',''),
            '--dest',str(wheels),'--requirement',str(pins)]
        for platform in platforms: arguments.extend(['--platform',platform])
        run(arguments,folder,env)
        packages=sorted((metadata(path) for path in wheels.glob('*.whl')),key=lambda p:p['name'])
        if len({p['name'] for p in packages})!=len(packages):
            raise ValueError('Duplicate package distributions in the bundle')
        check_compatibility(packages,target)
        check_closure(packages,target)
        hashes='\n'.join(p['name']+'=='+p['version']+' --hash=sha256:'+p['sha256'] for p in packages)+'\n'
        (folder/'requirements.txt').write_text(hashes)
        launcher_hashes={}
        for name in ('start_monkey.py','qualify_cli.py'):
            data=(ROOT/'scripts'/name).read_bytes()
            (folder/name).write_bytes(data)
            launcher_hashes[name]=hashlib.sha256(data).hexdigest()
        manifest={'schema':'monkey.deployment-bundle.v1','target':target,'python':version,
            'packages':packages,'source_hashes':source_hashes,'requirements_sha256':hashlib.sha256(hashes.encode()).hexdigest(),
            'launcher_hashes':launcher_hashes,
            'native_execution_qualified':False,'qualification':'Wheel availability and metadata closure only; native execution must be tested separately.'}
        if target == 'windows11-arm64-x64-py311':
            manifest['execution_mode'] = 'Windows 11 ARM64 with x64 Python emulation; explicitly selected compatibility target'
        (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        interpreter='py -'+version if target.startswith('windows') else 'python'+version
        start=interpreter+' -I '+('.\\start_monkey.py' if target.startswith('windows') else './start_monkey.py')
        (folder/'INSTALL.txt').write_text('Development candidate; native deployment qualification is separate.\n'
            + ('Windows 11 ARM64 / x64 Python emulation. This explicit target is not a native ARM64 Python build.\n'
               if target == 'windows11-arm64-x64-py311' else '')
            + 'From this bundle directory, with the specified Python already installed:\n\n'
            +start+'\n\n'
            'This checks the OS, native architecture, Python version and every bundled artifact,\n'
            'installs a private runtime once, checks dependencies, and opens Monkey. Repeat the\n'
            'same command to reopen it. Existing or changed runtimes are never overwritten.\n\n'
            'Read-only bundle check: '+start+' --check\n'
            'Install without opening the prompt: '+start+' --install-only\n'
            'Actual console/state/signed-audit fixture: '+start+' --qualify\n'
            'Exact command: '+start+' -- status\n\n'
            'The runtime, installation receipt and separate candidate state live in .monkey-install.\n'
            'Keep this bundle at the installed path; venv launchers contain absolute paths.\n'
            'Installation checks do not qualify native terminal UI or model quality.\n'
            'Hashes detect changed artifacts; publisher signing is a separate release gate.\n'
            'This installs no service, model weights, browser runtime or business credentials.\n')
        print(json.dumps({'bundle':str(folder),'packages':len(packages),'target':target,'native_execution_qualified':False}),flush=True)
        return manifest
    except BaseException as exc:
        (folder/'failure.json').write_text(json.dumps({'error':type(exc).__name__,'message':str(exc),'target':target})+'\n')
        raise
    finally:
        shutil.rmtree(folder/'cache',ignore_errors=True)
        netrc.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',choices=TARGETS,required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    build(args.target,args.output)
