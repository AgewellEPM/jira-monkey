"""Native private-state behavior; run this same test on each qualified OS."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from monkey.common import Refused
from monkey.database import Database
from monkey.platform_files import StateLease, private_directory, read_regular, write_private, operator_identity


class PlatformFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='monkey-native-state-',dir=Path(tempfile.gettempdir()).resolve())
        self.root = Path(self.temp.name) / 'private'
        private_directory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_private_create_replace_bounds_and_exclusive_ownership(self):
        path = self.root / 'evidence.json'
        write_private(path,b'first')
        with self.assertRaises(FileExistsError):
            write_private(path,b'overwrite')
        write_private(path,b'second',replace=True)
        self.assertEqual(read_regular(path,6,private=True),b'second')
        with self.assertRaises(Refused):
            read_regular(path,5,private=True)
        first = StateLease(self.root / 'lock')
        try:
            code = 'from monkey.platform_files import StateLease; import sys\ntry: StateLease(sys.argv[1])\nexcept BlockingIOError: sys.exit(23)\nsys.exit(1)'
            process = subprocess.run([sys.executable,'-c',code,str(self.root/'lock')],capture_output=True,timeout=10)
            self.assertEqual(process.returncode,23,process.stderr.decode(errors='replace'))
        finally:
            first.close()
        next_owner = StateLease(self.root / 'lock')
        next_owner.close()

    def test_hardlinked_private_file_and_parent_traversal_are_refused(self):
        path = self.root/'private.key'
        write_private(path,b'not a real key')
        os.link(path,self.root/'alias.key')
        with self.assertRaises(Refused):
            read_regular(path,100,private=True)
        with self.assertRaises(Refused):
            write_private(self.root/'..'/'escaped.key',b'no')
        self.assertFalse((self.root.parent/'escaped.key').exists())

    @unittest.skipIf(os.name=='nt','Windows ACL checks are in the native Windows qualification test')
    def test_public_permissions_and_symlinks_are_refused(self):
        path = self.root/'private.key'
        write_private(path,b'not a real key')
        path.chmod(0o644)
        with self.assertRaises(Refused):
            read_regular(path,100,private=True)
        path.chmod(0o600)
        (self.root/'redirect').symlink_to(self.root,target_is_directory=True)
        with self.assertRaises((Refused,OSError)):
            read_regular(self.root/'redirect'/'private.key',100,private=True)

    def test_signed_database_restart_preserves_identity_and_configuration(self):
        database = Database(self.root/'state')
        try:
            database.configure({'model':'test-model'})
            fingerprint = database.audit.fingerprint
            self.assertTrue(operator_identity().startswith('sid:' if os.name=='nt' else 'uid:'))
            database.audit.verify()
        finally:
            database.close()
        reopened = Database(self.root/'state')
        try:
            self.assertEqual(reopened.config()['model'],'test-model')
            self.assertEqual(reopened.audit.fingerprint,fingerprint)
            reopened.audit.verify()
        finally:
            reopened.close()

    def test_only_committed_records_wake_the_event_feed(self):
        database=Database(self.root/'state')
        try:
            database.changed.clear()
            with self.assertRaises(ValueError):
                with database.transaction():
                    database._event(None,'fixture.rollback',{'message':'Never committed'})
                    raise ValueError('fixture rollback')
            self.assertFalse(database.changed.is_set())
            self.assertEqual(database.events(),[])
            database.global_event('fixture.committed',{'message':'Committed first'})
            self.assertTrue(database.changed.is_set())
            self.assertEqual(database.events()[-1]['kind'],'fixture.committed')
            database.audit.verify()
        finally:
            database.close()
