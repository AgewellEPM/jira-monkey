"""Offline startup integrity checks; OS strings are not native qualification."""
import importlib.util
import getpass
import json
import ntpath
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('monkey_bundle_start',
        Path(__file__).resolve().parents[1] / 'scripts/start_monkey.py')
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)
qualify_spec = importlib.util.spec_from_file_location('monkey_bundle_qualify',
        Path(__file__).resolve().parents[1] / 'scripts/qualify_cli.py')
qualify = importlib.util.module_from_spec(qualify_spec)
qualify_spec.loader.exec_module(qualify)


class BundleStartup(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Native Windows taskkill cleanup is qualified inside Windows')
    def test_console_timeout_records_failure_and_stops_its_child(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            child = ('from pathlib import Path; import time; '
                     'Path("started").write_text("ready"); print("child started", flush=True); '
                     'time.sleep(1.5); Path("survived").write_text("unexpected")')
            parent = ('import subprocess,sys; '
                      'subprocess.run([sys.executable,"-c",' + repr(child) + '])')
            result = qualify.checked_process([sys.executable, '-c', parent], cwd=root,
                                             env=qualify.qualification_environment(), timeout=0.7)
            self.assertEqual(result['exit_code'], 'timeout')
            self.assertIn('child started', result['stdout'])
            self.assertTrue((root / 'started').exists(), 'The child must actually have run')
            time.sleep(1)
            self.assertFalse((root / 'survived').exists())

    @unittest.skipIf(os.name == 'nt', 'Native Windows taskkill cleanup is qualified inside Windows')
    def test_setup_timeout_stops_the_installer_child_before_it_can_write(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            child = ('from pathlib import Path; import time; '
                     'Path("started").write_text("ready"); time.sleep(1.5); '
                     'Path("survived").write_text("unexpected")')
            parent = ('import subprocess,sys; '
                      'subprocess.run([sys.executable,"-c",' + repr(child) + '])')
            with (root / 'setup.log').open('wb') as log:
                with self.assertRaises(subprocess.TimeoutExpired):
                    start.setup_process([sys.executable, '-c', parent], cwd=root,
                                        env=start.installation_environment(), log=log, timeout=0.7)
            self.assertTrue((root / 'started').exists(), 'The child must actually have run')
            time.sleep(1)
            self.assertFalse((root / 'survived').exists())

    def test_windows_executable_stat_hint_does_not_hide_real_file_changes(self):
        # Native CPython 3.11 adds 0111 to path-based .exe metadata only.
        fields = dict(st_dev=31, st_ino=73, st_size=128, st_mtime_ns=100,
                      st_ctime_ns=90, st_mode=0o100666, st_nlink=1)
        handle = types.SimpleNamespace(**fields)
        path = types.SimpleNamespace(**{**fields, 'st_mode': 0o100777})
        self.assertEqual(start.file_identity(path, windows=True),
                         start.file_identity(handle, windows=True))
        self.assertNotEqual(start.file_identity(path, windows=False),
                            start.file_identity(handle, windows=False))
        for key, value in [('st_dev', 32), ('st_ino', 74), ('st_size', 129),
                           ('st_mtime_ns', 101), ('st_ctime_ns', 91),
                           ('st_mode', 0o100444), ('st_mode', 0o040666), ('st_nlink', 2)]:
            with self.subTest(field=key, value=value):
                changed = types.SimpleNamespace(**{**fields, key: value})
                self.assertNotEqual(start.file_identity(path, windows=True),
                                    start.file_identity(changed, windows=True))

    def test_windows_home_discovery_survives_filtered_child_environments(self):
        expected = r'C:\Users\MonkeyFixture'
        source = {'USERPROFILE': expected, 'HOMEDRIVE': 'C:',
                  'USERNAME': 'MonkeyFixture',
                  'HOMEPATH': r'\Users\MonkeyFixture', 'SystemRoot': r'C:\Windows',
                  'PATH': r'C:\Windows\System32', 'HOME': '/posix/ignored/by/windows',
                  'PYTHONPATH': '/untrusted/module/path', 'PYTHONHOME': '/untrusted/runtime',
                  'OPENAI_API_KEY': 'fixture-not-a-credential',
                  'PIP_INDEX_URL': 'https://example.invalid/private'}
        for helper in (start.installation_environment, qualify.qualification_environment):
            for profile_present in (True, False):
                with self.subTest(helper=helper.__name__, profile_present=profile_present):
                    original = dict(source)
                    if not profile_present:
                        original.pop('USERPROFILE')
                    with patch.dict(os.environ, original, clear=True):
                        child = helper()
                    with patch.dict(os.environ, child, clear=True):
                        self.assertEqual(ntpath.expanduser('~'), expected)
                        self.assertEqual(getpass.getuser(), 'MonkeyFixture')
                    for forbidden in ('PYTHONPATH', 'PYTHONHOME', 'OPENAI_API_KEY', 'PIP_INDEX_URL'):
                        self.assertNotIn(forbidden, child)
                    self.assertEqual(child['SystemRoot'], r'C:\Windows')

    def fixture(self, root):
        payload = {'source/jira_monkey.py': b'', 'source/monkey/__init__.py': b'',
                   'source/monkey/platform_files.py': b'', 'source/monkey/windows_files.py': b'',
                   'start_monkey.py': b'checked launcher', 'qualify_cli.py': b'checked fixture',
                   'wheels/monkey_workdesk-0.0.0-py3-none-any.whl': b'wheel fixture'}
        wheel = 'monkey_workdesk-0.0.0-py3-none-any.whl'
        wheel_hash = start.digest(payload['wheels/' + wheel])
        payload['requirements.txt'] = ('monkey-workdesk==0.0.0 --hash=sha256:' + wheel_hash + '\n').encode()
        manifest = {'schema': 'monkey.deployment-bundle.v1', 'target': 'macos-arm64-py311', 'python': '3.11',
                    'source_hashes': {name[7:]: start.digest(data) for name, data in payload.items() if name.startswith('source/')},
                    'launcher_hashes': {name: start.digest(payload[name]) for name in ('start_monkey.py', 'qualify_cli.py')},
                    'requirements_sha256': start.digest(payload['requirements.txt']),
                    'packages': [{'name': 'monkey-workdesk', 'version': '0.0.0', 'file': wheel, 'sha256': wheel_hash}]}
        for name, data in payload.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (root / 'manifest.json').write_text(json.dumps(manifest))
        return manifest

    def test_qualified_target_means_actual_interpreter_and_native_machine(self):
        with patch.object(start.sys, 'platform', 'win32'), \
                patch.object(start.sysconfig, 'get_platform', return_value='win-amd64'), \
                patch.object(start.platform, 'python_version', return_value='3.11.14'), \
                patch.object(start.platform, 'machine', return_value='AMD64'), \
                patch.object(start, 'windows_architecture', return_value={'process_machine': 0,
                    'native_machine': 0x8664, 'image_machine': 0x8664, 'windows_build': 19045}):
            start.host_matches('windows-x64-py311')
            with patch.object(start.platform, 'machine', return_value='ARM64'):
                with self.assertRaisesRegex(RuntimeError, 'no architecture substitution'):
                    start.host_matches('windows-x64-py311')
            with patch.object(start.platform, 'python_version', return_value='3.13.1'):
                with self.assertRaises(RuntimeError):
                    start.host_matches('windows-x64-py311')

    def test_windows_arm64_compatibility_requires_an_explicit_target_and_kernel_proof(self):
        architecture = {'process_machine': 0x8664, 'native_machine': 0xaa64,
                        'image_machine': 0x8664, 'windows_build': 26100}
        with patch.object(start.sys, 'platform', 'win32'), \
                patch.object(start.sysconfig, 'get_platform', return_value='win-amd64'), \
                patch.object(start.platform, 'python_version', return_value='3.11.9'), \
                patch.object(start.platform, 'machine', return_value='AMD64'), \
                patch.object(start, 'windows_architecture', return_value=architecture):
            start.host_matches('windows11-arm64-x64-py311')
            with self.assertRaisesRegex(RuntimeError, 'kernel/process'):
                start.host_matches('windows-x64-py311')
            for field, value in [('native_machine', 0x8664), ('image_machine', 0xaa64),
                                 ('process_machine', 0x14c), ('windows_build', 19045)]:
                with self.subTest(field=field), patch.object(start, 'windows_architecture',
                        return_value={**architecture, field: value}):
                    with self.assertRaisesRegex(RuntimeError, 'kernel/process'):
                        start.host_matches('windows11-arm64-x64-py311')

    def test_changed_wheel_launcher_and_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name, \
                patch.object(start, 'host_matches'):
            root = Path(name)
            self.fixture(root)
            _, original, _ = start.inspect_bundle(root)
            for filename in ('wheels/monkey_workdesk-0.0.0-py3-none-any.whl', 'start_monkey.py'):
                path = root / filename
                before = path.read_bytes()
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'artifact changed'):
                    start.inspect_bundle(root)
                path.write_bytes(before)
            (root / 'manifest.json').write_bytes((root / 'manifest.json').read_bytes() + b'\n')
            with self.assertRaisesRegex(RuntimeError, 'supplied hash'):
                start.inspect_bundle(root, original)

    def test_extra_wheel_and_requirement_directives_never_reach_pip(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name, \
                patch.object(start, 'host_matches'):
            root = Path(name)
            manifest = self.fixture(root)
            extra = root / 'wheels/unpinned.whl'
            extra.write_bytes(b'extra')
            with self.assertRaisesRegex(RuntimeError, 'pinned inventory'):
                start.inspect_bundle(root)
            extra.unlink()
            requirements = (root / 'requirements.txt').read_bytes() + b'--extra-index-url https://example.invalid\n'
            (root / 'requirements.txt').write_bytes(requirements)
            manifest['requirements_sha256'] = start.digest(requirements)
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, 'exactly the manifest'):
                start.inspect_bundle(root)

    def test_path_traversal_and_external_runtime_links_are_refused(self):
        for value in ('../escape', '/absolute', 'a/../b', 'a\\b', 'C:alternate', 'a//b'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                start.relative(value)
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name:
            root = Path(name)
            runtime = root / 'runtime'
            runtime.mkdir()
            external = root / 'external'
            external.write_bytes(b'outside')
            try:
                (runtime / 'link').symlink_to(external)
            except OSError:
                self.skipTest('This account cannot create symlinks')
            with self.assertRaisesRegex(RuntimeError, 'leaves its installation'):
                start.runtime_tree(runtime)

    def test_changed_installed_files_change_recorded_runtime(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name:
            root = Path(name)
            program = root / 'worker.py'
            program.write_bytes(b'original')
            before = start.runtime_tree(root)
            program.write_bytes(b'changed')
            self.assertNotEqual(before, start.runtime_tree(root))

    def test_failed_install_is_retained_and_never_reexecuted_implicitly(self):
        from monkey import platform_files
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name:
            root = Path(name)
            manifest = self.fixture(root)
            payload = {name:(root / name).read_bytes() for name in ('requirements.txt', 'qualify_cli.py')}
            wheel = next((root / 'wheels').iterdir())
            payload['wheels/' + wheel.name] = wheel.read_bytes()
            with patch.object(start, 'setup_process', return_value=7) as invoke:
                with self.assertRaisesRegex(RuntimeError, 'Installation check failed'):
                    start.install(root, manifest, '0' * 64, payload, platform_files)
                self.assertEqual(invoke.call_count, 1)
            log = (root / '.monkey-install/install.log').read_bytes()
            self.assertTrue(log)
            self.assertFalse((root / '.monkey-install/receipt.json').exists())
            with patch.object(start.subprocess, 'run') as invoke:
                with self.assertRaisesRegex(RuntimeError, 'incomplete installation'):
                    start.install(root, manifest, '0' * 64, payload, platform_files)
                invoke.assert_not_called()
            self.assertEqual(log, (root / '.monkey-install/install.log').read_bytes())

    def test_checked_launch_ignores_even_timestamp_matching_poisoned_bytecode(self):
        from monkey import platform_files
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name:
            root = Path(name)
            source = root / 'cache_fixture.py'
            source.write_text('value = "EVIL"\n')
            metadata = source.stat()
            py_compile.compile(str(source), doraise=True)
            source.write_text('value = "GOOD"\n')
            os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            env = dict(os.environ)
            env.pop('PYTHONPYCACHEPREFIX', None)
            env.pop('PYTHONPATH', None)
            command = [sys.executable, '-B', '-c', 'import cache_fixture; print(cache_fixture.value)']
            # Establish that this is a valid cache-poison fixture, not merely a
            # malformed .pyc that Python would reject without our cache policy.
            old = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=10, check=True)
            self.assertEqual(old.stdout.strip(), b'EVIL')
            with start.no_bytecode_cache(root, platform_files, env) as (checked_env, prefix):
                checked = subprocess.run(command, cwd=root, env=checked_env, capture_output=True, timeout=10, check=True)
                self.assertEqual(checked.stdout.strip(), b'GOOD')
            self.assertFalse(prefix.exists())
            recorded = start.runtime_tree(root)
            self.assertIn('cache_fixture.py', recorded)
            self.assertFalse(any(name.endswith('.pyc') for name in recorded))


if __name__ == '__main__':
    unittest.main()
