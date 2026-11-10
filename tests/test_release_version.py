import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('release_version',Path(__file__).resolve().parents[1]/'scripts/release_version.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ReleaseVersion(unittest.TestCase):
    def test_first_release_uses_declared_version(self):self.assertEqual(module.choose_version('1.6.0',[],[]),'1.6.0')
    def test_changed_source_advances_patch_even_without_a_manual_tag(self):self.assertEqual(module.choose_version('1.6.0',['v1.6.0','v1.6.2'],[]),'1.6.3')
    def test_feature_version_takes_precedence(self):self.assertEqual(module.choose_version('1.7.0',['v1.6.9'],[]),'1.7.0')
    def test_retry_reuses_this_commits_version(self):self.assertEqual(module.choose_version('1.6.0',['v1.6.3'],['v1.6.3']),'1.6.3')


if __name__=='__main__':unittest.main()
