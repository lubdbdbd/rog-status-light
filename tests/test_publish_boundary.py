import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('sync_local',Path(__file__).resolve().parents[1]/'scripts/sync_local.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class PublishBoundary(unittest.TestCase):
    def prepare(self,folder):
        source=folder/'outputs/ROG状态灯';source.mkdir(parents=True)
        archive=folder/'outputs/ROG状态灯-Windows.zip'
        with zipfile.ZipFile(archive,'w') as zipped:
            for name in (*module.SOURCES,*module.PACKAGING):
                data=b'{}' if name=='bluetooth-validation.json' else b'safe software file'
                p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
                zipped.writestr('ROG状态灯/'+name,data)
        return source,archive

    def test_unfinished_zip_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);source,archive=self.prepare(folder)
            self.assertIsNone(module.snapshot(folder,now=archive.stat().st_mtime+1))

    def test_files_edited_after_packaging_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);source,archive=self.prepare(folder)
            (source/'app.py').write_text('unfinished edit',encoding='utf-8')
            self.assertIsNone(module.snapshot(folder,now=archive.stat().st_mtime+121))

    def test_only_allowlisted_software_leaves_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);source,archive=self.prepare(folder)
            (source/'private.json').write_text('private data',encoding='utf-8')
            selected,fingerprint=module.snapshot(folder,now=archive.stat().st_mtime+121)
            self.assertNotIn('private.json',json.dumps(list(selected)))
            self.assertEqual(len(fingerprint),64)

    def test_private_material_blocks_publication(self):
        for value in (b'-----BEGIN PRIVATE KEY-----',b'ghp_abcdefghijklmnopqrstuvw',b'C:\\Users\\owner\\private'):
            with self.assertRaises(ValueError):module.guard_text(value,'app.py')


if __name__=='__main__':unittest.main()
