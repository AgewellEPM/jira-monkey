import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("monkey_installer", Path(__file__).resolve().parents[1] / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class Installation(unittest.TestCase):
    def test_failed_upgrade_restores_all_original_entrypoints(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            first = installer.install(root)
            before = {str(p.relative_to(root)): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
            original = installer.os.rename
            def fail_bundle(source, target):
                if Path(source).name == "Jira Monkey.app" and Path(source).parent.name.startswith(".jira-monkey-install-"):
                    raise OSError("Fixture final bundle install failure")
                return original(source, target)
            with patch.object(installer.os, "rename", side_effect=fail_bundle):
                with self.assertRaises(OSError):
                    installer.install(root, upgrade=True)
            for name, content in before.items():
                self.assertEqual((Path(root) / name).read_bytes(), content, name)
            self.assertTrue(Path(first["command"]).stat().st_mode & 0o100)

    def test_upgrade_keeps_reviewable_backup_and_state(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            first = installer.install(root)
            state = Path(root) / ".jira-monkey/operator-evidence"
            state.parent.mkdir(mode=0o700,exist_ok=True)
            state.write_text("retained operator evidence")
            upgraded = installer.install(root, upgrade=True)
            backup = Path(upgraded["backup"])
            self.assertTrue((backup / "payload/installation.json").is_file())
            self.assertTrue((backup / "Jira Monkey.app/Contents/Info.plist").is_file())
            self.assertTrue((backup / "jira-monkey").is_file())
            self.assertEqual(state.read_text(), "retained operator evidence")
            self.assertIn(str(Path(root) / ".jira-monkey"), Path(first["legacy_command"]).read_text())
            self.assertIn("jira-monkey", Path(first["command"]).read_text())

    def test_running_monkey_blocks_upgrade_before_payload_changes(self):
        from monkey.database import Database
        with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
            installed=installer.install(root)
            manifest=Path(installed['payload'])/'installation.json'
            before=manifest.read_bytes()
            database=Database(Path(root)/'.jira-monkey')
            try:
                with self.assertRaisesRegex(RuntimeError,'Monkey is using this state directory'):
                    installer.install(root,upgrade=True)
                self.assertEqual(manifest.read_bytes(),before)
                self.assertFalse(list(manifest.parent.parent.glob('jira-monkey-backup-*')))
            finally:
                database.close()
            after=installer.install(root,upgrade=True)
            self.assertTrue(Path(after['backup']).is_dir())
