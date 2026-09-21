"""Cross-target metadata checks; these do not run or qualify another OS."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('monkey_package_bundle',Path(__file__).resolve().parents[1]/'scripts/package_bundle.py')
bundle=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class Packaging(unittest.TestCase):
    def wheel(self,filename,python='>=3.11'):
        return {'file':filename,'requires_python':python,'name':'fixture','version':'1.0','requires':[]}

    def test_macos_minimum_os_tags_include_supported_older_wheels(self):
        bundle.check_compatibility([self.wheel('fixture-1.0-cp311-cp311-macosx_11_0_arm64.whl')],'macos-arm64-py311')
        bundle.check_compatibility([self.wheel('fixture-1.0-cp311-cp311-macosx_11_0_universal2.whl')],'macos-arm64-py311')
        with self.assertRaises(ValueError):
            bundle.check_compatibility([self.wheel('fixture-1.0-cp311-cp311-macosx_14_0_arm64.whl')],'macos-arm64-py311')

    def test_wrong_architecture_and_python_are_rejected(self):
        with self.assertRaises(ValueError):
            bundle.check_compatibility([self.wheel('fixture-1.0-cp313-cp313-win_amd64.whl')],'windows-arm64-py313')
        with self.assertRaises(ValueError):
            bundle.check_compatibility([self.wheel('fixture-1.0-py3-none-any.whl','>=3.14')],'windows-arm64-py313')
        bundle.check_compatibility([self.wheel('fixture-1.0-cp311-abi3-win_amd64.whl')],'windows-x64-py311')
        bundle.check_compatibility([self.wheel('fixture-1.0-cp311-abi3-win_amd64.whl')],'windows11-arm64-x64-py311')
        with self.assertRaises(ValueError):
            bundle.check_compatibility([self.wheel('fixture-1.0-cp311-cp311-win_arm64.whl')],'windows11-arm64-x64-py311')

    def test_target_markers_and_dependency_extras_are_checked(self):
        parent=self.wheel('fixture-1.0-py3-none-any.whl')
        parent['requires']=['win-only==1.0; sys_platform == "win32"']
        bundle.check_closure([parent],'macos-arm64-py311')
        with self.assertRaises(ValueError):
            bundle.check_closure([parent],'windows-x64-py311')
        parent['requires']=['child[crypto]==1.0']
        child={**self.wheel('child-1.0-py3-none-any.whl'),'name':'child','requires':['crypto==1.0; extra == "crypto"']}
        with self.assertRaises(ValueError):
            bundle.check_closure([parent,child],'macos-arm64-py311')
