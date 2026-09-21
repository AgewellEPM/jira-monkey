import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monkey.common import Refused
from monkey.project_tools import ProjectFiles


@unittest.skipUnless(os.name=='posix','Native descriptor-based project tools')
class ProjectFileBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.scope=self.base/'scope';self.scope.mkdir()
        self.root=self.scope/'project';self.root.mkdir()
        (self.root/'source.txt').write_text('original project')
        self.files=ProjectFiles(self.root)

    def test_replaced_root_or_linked_ancestor_is_refused(self):
        outside=self.base/'outside';outside.mkdir()
        (outside/'project').mkdir()
        (outside/'project'/'source.txt').write_text('outside secret')
        self.scope.rename(self.base/'original-scope')
        self.scope.symlink_to(outside,target_is_directory=True)
        with self.assertRaises((Refused,OSError)):
            self.files.read('source.txt')
        with self.assertRaises((Refused,OSError)):
            self.files.listing()
        self.scope.unlink();self.scope.mkdir();self.root.mkdir()
        (self.root/'source.txt').write_text('replacement directory')
        with self.assertRaises(Refused):
            self.files.write('source.txt','must not write',None)

    def test_directory_swapped_for_symlink_during_walk_never_leaks_names(self):
        nested=self.root/'nested';nested.mkdir()
        (nested/'safe.txt').write_text('safe')
        outside=self.base/'outside';outside.mkdir()
        (outside/'outside-secret-name.txt').write_text('outside')
        original=os.open
        changed=False
        def raced(path,flags,*args,**kwargs):
            nonlocal changed
            if path=='nested' and not changed:
                changed=True
                nested.rename(self.root/'moved-original')
                nested.symlink_to(outside,target_is_directory=True)
            return original(path,flags,*args,**kwargs)
        with patch('monkey.project_tools.os.open',raced):
            result=self.files.listing()
        self.assertTrue(changed)
        self.assertTrue(result['truncated'])
        self.assertGreater(result['unreadable_entries'],0)
        self.assertFalse(any('outside-secret' in name for name in result['files']))

    def test_listing_is_bounded_even_for_directories_without_files(self):
        for index in range(350): (self.root/('directory-'+str(index))).mkdir()
        result=self.files.listing()
        self.assertTrue(result['truncated'])
        self.assertEqual(result['inspected_directories'],300)
        self.assertLessEqual(result['inspected_entries'],10001)

    def test_regular_paths_work_and_protected_or_linked_subtrees_are_excluded(self):
        (self.root/'source').mkdir()
        (self.root/'source'/'calc.py').write_text('a=1')
        (self.root/'.env').write_text('synthetic secret')
        (self.root/'linked').symlink_to(self.base,target_is_directory=True)
        result=self.files.listing()
        self.assertEqual(sorted(result['files']),['source.txt','source/calc.py'])
        self.assertFalse(result['truncated'])
        before=self.files.read('source/calc.py')
        after=self.files.write('source/calc.py','a=2',before['sha256'])
        self.assertEqual(after['text'],'a=2')
