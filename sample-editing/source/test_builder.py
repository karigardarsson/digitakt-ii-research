"""Focused file-safety, composition and tamper tests; no device interaction."""
from pathlib import Path
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'builder-source'))
import builder_core as b

class BuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root,cls.manifest=b.checked_resources()
        local=HERE/'inspection/stock'/b.MAIN
        if local.is_file():cls.stock=local.read_bytes()
        else:
            stock=Path(os.environ.get('DT2_TEST_STOCK',''))
            if not stock.is_file() or b.sha256_file(stock)!=b.STOCK_SHA256:
                raise RuntimeError('Set DT2_TEST_STOCK to your official OS 1.17 .syx file.')
            with tempfile.TemporaryDirectory(dir=HERE) as tmp:
                files=b.extract(cls.root/'native/elektron-firmware-tool.exe',stock,Path(tmp)/'sections')
                cls.stock=files[b.MAIN].read_bytes()

    def reject_recipe(self,mutate):
        m=copy.deepcopy(self.manifest);mutate(m)
        with self.assertRaises(b.BuildError):b.apply_pack(self.stock,self.root,m)

    def test_exact_approved_composition(self):
        self.assertEqual(b.sha(b.apply_pack(self.stock,self.root,self.manifest)),
                         'a6d58c284ed7d8e7b74124b94dfe19b672671389371bb11c45b13864cffe6b5f')

    def test_wrong_stock_MAIN(self):
        with self.assertRaises(b.BuildError):b.apply_pack(self.stock[:-1]+b'X',self.root,self.manifest)

    def test_missing_module(self):
        self.reject_recipe(lambda m:m['modules'].pop())

    def test_moved_module(self):
        self.reject_recipe(lambda m:m['modules'][0].update(base=0x47ed0000))

    def test_BSS_collision(self):
        self.reject_recipe(lambda m:m['modules'][0].update(bss=0x200000))

    def test_patch_overlap(self):
        self.reject_recipe(lambda m:m['patches'].__setitem__(1,copy.deepcopy(m['patches'][0])))

    def test_patch_bounds(self):
        self.reject_recipe(lambda m:m['patches'][0].update(address=b.MAIN_BASE+len(self.stock)))

    def test_patch_preimage(self):
        self.reject_recipe(lambda m:m['patches'][0].update(before_sha256='0'*64))

    def test_bad_result(self):
        self.reject_recipe(lambda m:m.update(main_sha256='0'*64))

    def test_wrong_inputs_leave_no_output(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            folder=Path(temp);out=folder/'out.syx'
            for name,data in [('empty.syx',b''),('truncated.syx',b'\xf0\x00\x20\x3c'),('other-device.syx',b'not firmware'),('wrong.txt',b'')]:
                p=folder/name;p.write_bytes(data)
                with self.assertRaises(b.BuildError):b.build_firmware(p,out)
                self.assertFalse(out.exists())

    def test_overwrite_and_same_path(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            p=Path(temp)/'stock.syx';p.write_bytes(b'preserve')
            with self.assertRaises(b.BuildError):b.build_firmware(p,p)
            out=p.with_name('existing.syx');out.write_bytes(b'keep this')
            with self.assertRaises(b.BuildError):b.build_firmware(p,out)
            self.assertEqual(out.read_bytes(),b'keep this');self.assertEqual(p.read_bytes(),b'preserve')

    def test_tampered_each_resource(self):
        # Mutate isolated copies only; preserve captured source assets.
        import shutil
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            root=Path(temp)
            for rel in b.RESOURCE_HASHES:
                dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(self.root/rel,dst)
            with patch.object(b,'resource_root',return_value=root):
                for rel in b.RESOURCE_HASHES:
                    p=root/rel;original=p.read_bytes();p.write_bytes(original+b'X')
                    with self.assertRaises(b.BuildError):b.checked_resources()
                    p.write_bytes(original)

    def test_failed_verification_does_not_commit(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            p=Path(temp)/'bad.syx';p.write_bytes(b'wrong')
            out=p.with_name('out.syx')
            with self.assertRaises(b.BuildError):b.commit_output(p,out)
            self.assertFalse(out.exists());self.assertEqual(sorted(x.name for x in p.parent.iterdir()),['bad.syx'])

    def test_late_output_race(self):
        # Correct staged bytes must still not overwrite a late destination.
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            p=Path(temp)/'candidate.syx';p.write_bytes(b'candidate')
            out=p.with_name('out.syx');out.write_bytes(b'keep')
            with patch.object(b,'sha256_file',return_value=b.OUTPUT_SHA256):
                with self.assertRaises(b.BuildError):b.commit_output(p,out)
            self.assertEqual(out.read_bytes(),b'keep')

class DSPTests(unittest.TestCase):
    def setUp(self):
        self.root,self.manifest=b.checked_resources()
        stock=Path(os.environ['DT2_TEST_STOCK'])
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            files=b.extract(self.root/'native/elektron-firmware-tool.exe',stock,Path(tmp)/'sections')
            self.stock=files['section_7_blob.bin'].read_bytes()
    def test_exact_DSP(self):
        self.assertEqual(b.sha(b.apply_dsp(self.stock,self.root)),self.manifest['sections']['section_7_blob.bin']['output_sha256'])
    def test_wrong_DSP(self):
        with self.assertRaises(b.BuildError):b.apply_dsp(self.stock[:-1]+b'X',self.root)
    def test_DSP_copy_bounds_and_bad_hash(self):
        import shutil
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root=Path(tmp);shutil.copytree(self.root/'assets',root/'assets')
            path=root/'assets/dsp-recipe.json';original=json.loads(path.read_text())
            for offset in [-1,len(self.stock)]:
                doc=copy.deepcopy(original);doc['operations'][0]={'stock_offset':offset,'bytes':6};path.write_text(json.dumps(doc))
                with self.assertRaises(b.BuildError):b.apply_dsp(self.stock,root)
            original['output_sha256']='0'*64;path.write_text(json.dumps(original))
            with self.assertRaises(b.BuildError):b.apply_dsp(self.stock,root)
    def test_DSP_asset_tamper(self):
        import shutil
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root=Path(tmp);shutil.copytree(self.root/'assets',root/'assets')
            p=root/'assets/dsp-extension.bin';p.write_bytes(p.read_bytes()+b'X')
            with self.assertRaises(b.BuildError):b.apply_dsp(self.stock,root)

if __name__=='__main__':unittest.main(verbosity=2)
