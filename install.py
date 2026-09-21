#!/usr/bin/env python3
"""Install Jira Monkey's standalone foreground runtime with a reversible upgrade."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import sys
import tempfile
from monkey import VERSION
from monkey.common import Refused


def install(home, upgrade=False):
    from monkey.platform_files import private_directory, StateLease
    home=Path(home).absolute()
    state=home/'.jira-monkey'
    private_directory(state)
    try:
        lease=StateLease(state/'lock')
    except BlockingIOError:
        raise RuntimeError('Monkey is using this state directory. Use /quit in its prompt before upgrading; no installed payload was replaced.') from None
    try:
        return install_owned(home,upgrade)
    finally:
        lease.close()


def install_owned(home, upgrade=False):
    home = Path(home).absolute()
    source = Path(__file__).resolve().parent
    if sys.platform not in {'darwin','linux','win32'}:
        raise RuntimeError('This installer supports macOS, Linux and Windows; native qualification is reported separately')
    windows = sys.platform == 'win32'
    destination = home / ('AppData/Local/Monkey/runtime' if windows else '.local/share/jira-monkey')
    commands = home / ('AppData/Local/Monkey/bin' if windows else 'bin')
    executable = commands / ('jira-monkey.cmd' if windows else 'jira-monkey')
    monkey_command = commands / ('Monkey.cmd' if windows else 'Monkey')
    app = home / 'Applications/Jira Monkey.app' if sys.platform == 'darwin' else None
    for path in [destination, executable, monkey_command, *([app] if app else [])]:
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise RuntimeError("Refusing linked installation path: " + str(path))
        if path.exists() and not upgrade:
            raise RuntimeError("Installation already exists; preserve it and review an upgrade before replacing: " + str(path))
    if sys.version_info < (3, 11):
        raise RuntimeError("Jira Monkey requires Python 3.11+; install with its virtual-environment Python")
    import prompt_toolkit
    import httpx
    import mcp
    import jsonschema
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    backup = None
    if upgrade:
        manifest_path = destination / "installation.json"
        if not manifest_path.is_file():
            raise RuntimeError("Upgrade requires an existing Jira Monkey installation manifest")
        previous = json.loads(manifest_path.read_text())
        if previous.get('platform',sys.platform) != sys.platform:
            raise RuntimeError('Use the installer for the existing installation platform')
        if monkey_command.exists() and monkey_command.name not in previous.get("launchers", {}):
            raise RuntimeError("Monkey command already belongs to another installation; preserving " + str(monkey_command))
        for name, expected in previous.get("launchers", {}).items():
            if Path(name).name != name:
                raise RuntimeError('Invalid launcher manifest path')
            path = commands / name
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Installed launcher was modified; preserving " + str(path))
        for name, expected in previous["files"].items():
            path = destination / name
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Installed file was modified; preserve/review it before upgrade: " + str(path))
    for path in [destination.parent, commands, *([app.parent] if app else [])]:
        path.mkdir(parents=True, exist_ok=True)
    # All payloads are assembled before installing. Existing apps and worker journals are untouched.
    with tempfile.TemporaryDirectory(prefix=".jira-monkey-install-", dir=destination.parent) as folder:
        staged = Path(folder)
        payload = staged / "payload"
        payload.mkdir()
        for name in ("jira_monkey.py", "README.md", "install.py", 'pyproject.toml'):
            shutil.copyfile(source / name, payload / name)
        shutil.copyfile(source / "requirements.txt", payload / "requirements.txt")
        shutil.copyfile(source / "requirements-lock.txt", payload / "requirements-lock.txt")
        for name in ("monkey", "docs", "examples", "scripts", "tests"):
            if (source / name).is_dir():
                shutil.copytree(source / name, payload / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        runner = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(destination / "jira_monkey.py")) + " --state " + shlex.quote(str(home / ".jira-monkey")) + ' "$@"\n'
        monkey_runner = "#!/bin/sh\nexec " + shlex.quote(str(executable)) + ' "$@"\n'
        if windows:
            def cmd_path(path):
                value = str(path)
                if any(ch in value for ch in '%!^"\r\n'):
                    raise RuntimeError('Windows command installation paths must not contain cmd expansion characters')
                return '"' + value + '"'
            runner = '@echo off\nsetlocal DisableDelayedExpansion\n' + cmd_path(sys.executable) + ' ' + cmd_path(destination/'jira_monkey.py') + ' --state ' + cmd_path(home/'.jira-monkey') + ' %*\n'
            monkey_runner = runner
        bundle = None
        if app:
            command = payload / "Jira Monkey.command"
            command.write_text("#!/bin/sh\nexec " + shlex.quote(str(monkey_command)) + " repl\n")
            command.chmod(0o755)
            bundle = staged / "Jira Monkey.app"
            macos = bundle / "Contents/MacOS"
            macos.mkdir(parents=True)
            launcher = macos / "jira-monkey"
            launcher.write_text("#!/bin/sh\nexec /usr/bin/open -a Terminal " + shlex.quote(str(destination / "Jira Monkey.command")) + "\n")
            launcher.chmod(0o755)
            (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({
                "CFBundleExecutable": "jira-monkey", "CFBundleIdentifier": "com.perslis.jira-monkey.terminal",
                "CFBundleName": "Jira Monkey", "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": VERSION, "CFBundleVersion": "9", "LSUIElement": True}))
        manifest = {"version": VERSION, 'platform':sys.platform, "python": sys.executable, "source": str(source),
            "launchers": {monkey_command.name: hashlib.sha256(monkey_runner.encode()).hexdigest(), executable.name: hashlib.sha256(runner.encode()).hexdigest()},
            "files": {str(p.relative_to(payload)): hashlib.sha256(p.read_bytes()).hexdigest() for p in payload.rglob("*") if p.is_file()}}
        (payload / "installation.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # Exclusive creation prevents overwriting an unrelated command during concurrent installs.
        saved = []
        installed = []
        try:
            if upgrade:
                backup = Path(tempfile.mkdtemp(prefix="jira-monkey-backup-", dir=destination.parent))
                paths = [(destination,'payload'),(executable,executable.name),(monkey_command,monkey_command.name)]
                if app:
                    paths.append((app,'Jira Monkey.app'))
                for old, name in paths:
                    if old.exists():
                        archived = backup / name
                        os.rename(old, archived)
                        saved.append((old, archived))
            with executable.open("x",newline='\n') as stream:
                installed.append(executable)
                stream.write(runner)
            executable.chmod(0o755)
            with monkey_command.open("x",newline='\n') as stream:
                installed.append(monkey_command)
                stream.write(monkey_runner)
            monkey_command.chmod(0o755)
            os.rename(payload, destination)
            installed.append(destination)
            if bundle:
                os.rename(bundle, app)
                installed.append(app)
        except BaseException:
            # Roll back only paths this invocation created. Previous payloads
            # remain in the backup if another process occupies their destination.
            for index, target in enumerate(reversed(installed)):
                if target.exists():
                    os.rename(target, staged / ("rollback-new-" + str(index)))
            for target, archived in reversed(saved):
                if archived.exists() and not target.exists():
                    os.rename(archived, target)
            raise
    return {"command": str(monkey_command), "legacy_command": str(executable), "app": str(app) if app else None, "payload": str(destination),
            "backup": str(backup) if backup else None,
            'command_directory':str(commands), 'platform':sys.platform,
            "note": "Add the command directory to your user PATH, then type Monkey. The macOS app is also available on macOS. No services or models were started."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(), help="Installation home; also supports an isolated packaging test")
    parser.add_argument("--upgrade", action="store_true", help="Back up and replace the recognized local installation")
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.home, args.upgrade), indent=2))
    except (OSError, RuntimeError, Refused) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
