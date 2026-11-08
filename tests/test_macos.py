"""Mac source adapter contract, isolated from any actual input method or account."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ginput import core
from ginput.cli import main
from ginput.dictionary import parse_words
from ginput.platforms import paths_for
from ginput.platforms import macos
from tools.package_macos import package
from tools import package_macos

ROOT = Path(__file__).resolve().parents[1]


class DictionaryContractTests(unittest.TestCase):
    def test_portable_v1_fixtures(self):
        contract = json.loads((ROOT / 'tests/fixtures/dictionary-v1.json').read_text())
        for case in contract['valid']:
            actual = [[w.text, w.pinyin, w.weight] for w in parse_words(case['source'])]
            self.assertEqual(actual, case['words'])
        for source in contract['invalid']:
            with self.subTest(source=source), self.assertRaises(core.InputError): parse_words(source)


class MacTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ginput mac test ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = {'config': self.root/'support/config.json', 'words': self.root/'support/words.tsv',
                      'base': self.root/'support/resources/pinyin_simp.dict.yaml',
                      'state': self.root/'support/state', 'rime': self.root/'Rime',
                      'shared': self.root/'Squirrel.app/Contents/SharedSupport'}
        self.base = self.root/'source/pinyin_simp.dict.yaml'
        core.atomic_write(self.base, b'# upstream header\n---\nname: pinyin_simp\nversion: "1"\nsort: by_weight\n...\n' + '你好\tni hao\t100\n'.encode())
        environment = patch.object(core, 'paths_for', return_value=self.paths)
        environment.start(); self.addCleanup(environment.stop)
        self.config = core.initialize(self.paths['config'], 'macos')
        core.atomic_write(self.paths['base'], self.base.read_bytes())

    def test_mac_paths_ignore_linux_xdg(self):
        with patch('pathlib.Path.home', return_value=Path('/Users/example')), patch.dict(os.environ, {'XDG_CONFIG_HOME':'/linux/config'}):
            paths = paths_for('macos')
        self.assertEqual(paths['rime'], Path('/Users/example/Library/Rime'))
        self.assertEqual(paths['words'], Path('/Users/example/Library/Application Support/GuitarInput/words.tsv'))

    def test_legacy_config_remains_linux(self):
        config = self.config | {'reload': 'fcitx4'}
        config.pop('platform'); config.pop('base_dictionary')
        core.write_json(self.paths['config'], config)
        self.assertEqual(core.load_config(self.paths['config'])['platform'], 'linux')

    def test_macos_rejects_fcitx_reload(self):
        core.write_json(self.paths['config'], self.config | {'reload': 'fcitx4'})
        with self.assertRaises(core.InputError): core.load_config(self.paths['config'])

    def test_source_only_no_desktop_commands_or_compiled_files(self):
        with patch.object(core.subprocess, 'run', side_effect=AssertionError('desktop command')), patch.object(core, 'compile_dictionary', side_effect=AssertionError('cross compile')):
            result = core.sync(self.config)
        self.assertEqual(result['status'], 'deployment_required')
        self.assertFalse((self.paths['rime']/'build').exists())
        self.assertIn('name: ginput_base', (self.paths['rime']/'ginput_base.dict.yaml').read_text())
        self.assertIn('  - ginput_base', (self.paths['rime']/'ginput.dict.yaml').read_text())
        self.assertIn('context_enabled: false', (self.paths['rime']/'ginput.schema.yaml').read_text())
        self.assertNotIn('pinyin_simp', (self.paths['rime']/'ginput.dict.yaml').read_text())

    def test_preserves_other_config_loader_userdb_and_dictionary(self):
        target = self.paths['rime']
        others = {'default.custom.yaml':b'patch: {}\n', 'squirrel.custom.yaml':b'patch: {}\n',
                  'ginput.userdb/test': b'learned', 'pinyin_simp.dict.yaml':b'other dictionary'}
        for name, content in others.items(): core.atomic_write(target/name, content)
        core.atomic_write(target/'rime.lua', b'other = require("other")\n')
        core.sync(self.config); core.sync(self.config, True)
        for name, content in others.items(): self.assertEqual((target/name).read_bytes(), content)
        loader = (target/'rime.lua').read_text()
        self.assertIn('other = require("other")', loader)
        self.assertEqual(loader.count(core.LOADER_BEGIN), 1)

    def test_return_based_lua_loader_remains_last(self):
        target = self.paths['rime']
        core.atomic_write(target/'rime.lua', b'other = {}\nreturn other\n')
        core.sync(self.config)
        loader = (target/'rime.lua').read_text()
        self.assertTrue(loader.endswith('return other\n'))
        self.assertTrue(loader.startswith(core.LOADER_BEGIN))
        core.sync(self.config, True)
        self.assertEqual((target/'rime.lua').read_text(), loader)

    def test_repeated_sync_no_duplicate_backup(self):
        first = core.sync(self.config)
        second = core.sync(self.config)
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.paths['state']/'backups').iterdir())), 1)

    def test_backup_contains_previous_sources(self):
        core.sync(self.config)
        old = (self.paths['rime']/'ginput_personal.dict.yaml').read_bytes()
        self.paths['words'].write_text('新词\txin ci\t10\n')
        result = core.sync(self.config)
        self.assertEqual((Path(result['backup'])/'ginput_personal.dict.yaml').read_bytes(), old)
        self.assertIn('新词', (self.paths['rime']/'ginput_personal.dict.yaml').read_text())

    def test_base_update_changes_revision(self):
        before = core.sync(self.config)
        with self.paths['base'].open('a') as stream: stream.write('中国\tzhong guo\t100\n')
        after = core.sync(self.config)
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertIn('中国', (self.paths['rime']/'ginput.lexicon.tsv').read_text())

    def test_bad_loader_does_not_change_sources(self):
        core.sync(self.config)
        old = (self.paths['rime']/'ginput.dict.yaml').read_bytes()
        (self.paths['rime']/'rime.lua').write_text(core.LOADER_BEGIN)
        with self.assertRaises(core.InputError): core.sync(self.config, True)
        self.assertEqual((self.paths['rime']/'ginput.dict.yaml').read_bytes(), old)

    def test_mid_publish_failure_rolls_back(self):
        core.sync(self.config)
        old = {name:(self.paths['rime']/name).read_bytes() for name in macos.MANAGED}
        self.paths['words'].write_text('新词\txin ci\t10\n')
        write = core.atomic_write
        failed = False
        def fail_once(path, data):
            nonlocal failed
            if path == self.paths['rime']/'ginput_base.dict.yaml' and not failed:
                failed = True
                raise OSError('disk full')
            write(path, data)
        with patch.object(core, 'atomic_write', fail_once), self.assertRaises(OSError): core.sync(self.config)
        self.assertEqual({name:(self.paths['rime']/name).read_bytes() for name in macos.MANAGED}, old)
        self.assertFalse((self.paths['state']/'pending-transaction.json').exists())

    def test_symlinked_lua_directory_is_rejected(self):
        self.paths['rime'].mkdir()
        outside = self.root/'unrelated'; outside.mkdir()
        (self.paths['rime']/'lua').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(core.InputError): core.sync(self.config)
        self.assertEqual(list(outside.iterdir()), [])

    def test_install_preserves_manual_words_and_existing_customization(self):
        self.paths['words'].write_text('个人词\tge ren ci\t88\n')
        custom = self.paths['rime']/'default.custom.yaml'
        core.atomic_write(custom, b'patch:\n  schema_list:\n    - schema: other\n')
        old = custom.read_bytes()
        with patch.object(macos, 'host_platform', return_value='macos'):
            result = macos.install(self.paths['config'], self.base)
            macos.install(self.paths['config'], self.base)
        self.assertEqual(custom.read_bytes(), old)
        self.assertIn('个人词', (self.paths['rime']/'ginput_personal.dict.yaml').read_text())
        self.assertEqual(result['schema_list'], 'existing_file_unchanged')

    def test_install_creates_only_append_patch_when_none_exists(self):
        with patch.object(macos, 'host_platform', return_value='macos'):
            result = macos.install(self.paths['config'], self.base)
        self.assertEqual((self.paths['rime']/'default.custom.yaml').read_bytes(), macos.SCHEMA_PATCH)
        self.assertEqual(result['schema_list'], 'created_append_patch')

    def test_install_rejects_linux_and_legacy_configs(self):
        with patch.object(macos, 'host_platform', return_value='linux'), self.assertRaises(core.InputError):
            macos.install(self.paths['config'], self.base)
        config = self.config | {'platform':'linux', 'reload':'none'}
        core.write_json(self.paths['config'], config)
        with patch.object(macos, 'host_platform', return_value='macos'), self.assertRaises(core.InputError):
            macos.install(self.paths['config'], self.base)

    def test_watch_and_linux_only_commands_are_blocked(self):
        with self.assertRaises(core.InputError): core.watch(self.paths['config'])
        for command in (['skin','build','--output',str(self.root/'skin')], ['learning','enable'], ['service','enable']):
            with patch('ginput.cli.host_platform', return_value='macos'), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--config',str(self.paths['config']), *command]), 1)

    def test_explicit_config_equals_syntax_is_respected(self):
        path = self.root/'explicit/config.json'
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(main(['--config='+str(path), 'init', '--platform','macos']), 0)
        self.assertTrue(path.is_file())

    def test_package_is_deterministic_and_contains_no_private_data(self):
        one, two = self.root/'one.zip', self.root/'two.zip'
        self.paths['words'].write_text('秘密词\tmi mi ci\t123\n')
        package(one, self.base); package(two, self.base)
        self.assertEqual(one.read_bytes(), two.read_bytes())
        with zipfile.ZipFile(one) as archive:
            names = archive.namelist()
            self.assertTrue(any(n.endswith('/install.command') for n in names))
            self.assertFalse(any(n.endswith(('.bin','.sqlite3','.pyc')) or 'userdb' in n for n in names))
            self.assertNotIn('秘密词'.encode(), b''.join(archive.read(name) for name in names))
            manifest = json.loads(archive.read(next(n for n in names if n.endswith('/manifest.json'))))
            self.assertFalse(manifest['personal_data'])
        with self.assertRaises(ValueError): package(one, self.base)

    def test_package_ignores_unlisted_data_and_rejects_public_symlinks(self):
        source = self.root/'public-source'
        names = list(package_macos.PUBLIC_FILES) + ['licenses/pinyin-simp-APACHE-2.0.txt',
                 'licenses/pinyin-simp-AUTHORS.txt', 'docs/macos.md',
                 'platforms/macos/install.command', 'platforms/macos/squirrel.custom.example.yaml']
        for name in names:
            target = source/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT/name).read_bytes())
        (source/'ginput/words.tsv').write_text('untracked-private-sentinel')
        with patch.object(package_macos, 'ROOT', source):
            archive = package(self.root/'allowlisted.zip', self.base)
            with zipfile.ZipFile(archive) as bundle:
                self.assertFalse(any(name.endswith('/ginput/words.tsv') for name in bundle.namelist()))
                self.assertNotIn(b'untracked-private-sentinel', b''.join(bundle.read(n) for n in bundle.namelist()))
            example = source/'ginput/examples/words.example.tsv'
            example.unlink()
            example.symlink_to(source/'ginput/words.tsv')
            with self.assertRaises(ValueError): package(self.root/'bad-link.zip', self.base)

    def test_source_package_installs_from_extracted_directory(self):
        archive = package(self.root/'bundle.zip', self.base)
        extracted = self.root/'extracted'
        with zipfile.ZipFile(archive) as bundle: bundle.extractall(extracted)
        folder = next(extracted.iterdir())
        result = subprocess.run([sys.executable, '-m', 'ginput', '--version'], cwd=folder, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '0.6.0')
        # The actual install is covered above with a mocked Mac host and temp paths.


if __name__ == '__main__': unittest.main()
