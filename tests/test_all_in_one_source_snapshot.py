"""Offline source publication gate. No firmware, device or build required."""
import ast
import hashlib
import json
from pathlib import Path
import re
import tomllib
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'all-in-one'
MANIFEST=json.loads((SOURCE/'SOURCE_MANIFEST.json').read_text())

class SourceSnapshot(unittest.TestCase):
    def test_candidate_and_current_source_hashes(self):
        self.assertEqual(MANIFEST['candidate_sha256'],
            'bbc534fae45fd4044625edba07c12d4be8b24d8997ccab313e6af0dfccd43626')
        for name,record in MANIFEST['source_files'].items():
            self.assertEqual(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest(),
                             record['published_sha256'],name)

    def test_source_only_and_no_personal_paths(self):
        forbidden={'.syx','.bin','.elf','.o','.exe','.dll','.snap','.state','.wav','.m4a','.zip','.pdf'}
        private=re.compile(r'(?i)(?:[a-z]:[\\/]+Users[\\/]|/mnt/[a-z]/Users/|/home/(?:kari|oai)/|\.chatgpt-projects|g-p-[0-9a-f]{16})')
        for p in SOURCE.rglob('*'):
            if not p.is_file() or '__pycache__' in p.parts:continue
            self.assertNotIn(p.suffix.lower(),forbidden,str(p))
            self.assertFalse(p.name.endswith('.dko.json'),str(p))
            data=p.read_text(encoding='utf-8')
            self.assertNotIn('\0',data,str(p))
            self.assertIsNone(private.search(data),str(p))
            self.assertNotIn('experiments/chimera/',p.as_posix().lower())
            self.assertNotIn('experiments/arp_',p.as_posix().lower())

    def test_exact_digikit_notice_and_license_retained(self):
        text=(SOURCE/'NOTICE').read_text()
        self.assertIn('Attribution (section 7(b))',text)
        self.assertIn('https://github.com/ElekLabs/digikit',text)
        self.assertEqual((SOURCE/'NOTICE').read_bytes(),
            (SOURCE/'source/experiments/on_device_credits/NOTICE').read_bytes())
        self.assertEqual((SOURCE/'LICENSE').read_bytes(),
            (SOURCE/'source/experiments/on_device_credits/LICENSE').read_bytes())

    def test_native_credits_glue_is_exact(self):
        for name,digest in {'credits.c':'31c5b8e270e4e63cdfd99bea5937bd415b41ad801cdcba69bbeef2e1a96e389e',
                            'hook.s':'e16492ca36ad4270ba3adf3e549a10c290462c8ab0ed9102a4c2a3f1eec1367d'}.items():
            self.assertEqual(hashlib.sha256((SOURCE/'source/experiments/on_device_credits'/name).read_bytes()).hexdigest(),digest)
        self.assertEqual(MANIFEST['credits']['added_boot_frames'],0)
        self.assertTrue(MANIFEST['credits']['original_intro_exact'])

    def test_configurations_and_declared_sources(self):
        for p in SOURCE.rglob('*.py'):ast.parse(p.read_text(),filename=str(p))
        for p in SOURCE.rglob('*.toml'):tomllib.loads(p.read_text())
        for p in (SOURCE/'source/catalog/modules').rglob('module.toml'):
            for obj in tomllib.loads(p.read_text()).get('object',[]):
                if 'dt2' not in obj.get('devices',['dt2']):continue
                for name in obj['sources']:
                    self.assertTrue((p.parent/name).is_file(),(str(p),name))
        records=MANIFEST['compiled_object_inputs']
        for record in records:
            if record['arch']=='cf':
                data=b''.join((SOURCE/name).read_bytes() for name in record['inputs'])
                self.assertEqual(hashlib.sha256(data).hexdigest(),record['source_sha256'],record['module'])

if __name__=='__main__':unittest.main()
