import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ginput import core


class WordTests(unittest.TestCase):
    def test_utf8_comments_and_umlaut(self):
        words = core.parse_words('\ufeff# 示例\n女儿\tNÜ er\t50\n')
        self.assertEqual(words, [core.Word('女儿', 'nv er', 50)])

    def test_reject_bad_rows(self):
        for text in ('你好 ni hao 1', '你好\tni3 hao\t10', '你好\tni hao\t-1',
                     '你好\tni hao\t0', '你好\tni hao\t1000001', '你好\tni hao\t1\textra',
                     '\x00\tni\t1', '你好\tni hao\t1\n你好\tni hao\t2'):
            with self.subTest(text=text), self.assertRaises(core.InputError):
                core.parse_words(text)

    def test_multiple_readings_allowed(self):
        self.assertEqual(len(core.parse_words('行\txing\t1\n行\thang\t1')), 2)

    def test_empty_dictionary_allowed(self):
        self.assertEqual(core.parse_words('# no personal words\n'), [])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = core.default_config() | {
            'words': str(self.root / 'words.tsv'),
            'state_dir': str(self.root / 'state'),
            'rime_dir': str(self.root / 'rime'), 'reload': 'none',
        }
        Path(self.config['words']).write_text('示例输入法\tshi li shu ru fa\t10000\n')

    @staticmethod
    def fake_compile(config, words, revision, stage):
        for name, value in core.render_sources(words, revision).items():
            core.atomic_write(stage / name, value)
        for name in core.ARTIFACTS:
            core.atomic_write(stage / 'build' / name, revision.encode())

    def test_invalid_dictionary_preserves_previous_files(self):
        with patch.object(core, 'compile_dictionary', self.fake_compile):
            core.sync(self.config)
            before = (self.root / 'rime/ginput_personal.dict.yaml').read_bytes()
            Path(self.config['words']).write_text('broken row')
            with self.assertRaises(core.InputError):
                core.sync(self.config)
        self.assertEqual((self.root / 'rime/ginput_personal.dict.yaml').read_bytes(), before)

    def test_source_changed_while_building_not_published(self):
        def changing(config, words, revision, stage):
            self.fake_compile(config, words, revision, stage)
            Path(config['words']).write_text('新词\txin ci\t10')
        with patch.object(core, 'compile_dictionary', changing), self.assertRaises(core.Pending):
            core.sync(self.config)
        self.assertFalse((self.root / 'rime/ginput.dict.yaml').exists())

    def test_user_learning_and_other_schemas_are_untouched(self):
        userdb = self.root / 'rime/ginput.userdb/000001.log'
        other = self.root / 'rime/default.custom.yaml'
        core.atomic_write(userdb, b'learned input')
        core.atomic_write(other, b'other custom schemas')
        with patch.object(core, 'compile_dictionary', self.fake_compile):
            core.sync(self.config)
            core.sync(self.config, force=True)
        self.assertEqual(userdb.read_bytes(), b'learned input')
        self.assertEqual(other.read_bytes(), b'other custom schemas')

    def test_shared_lua_loader_is_preserved_without_duplicate_managed_blocks(self):
        path=self.root/'rime/rime.lua'
        core.atomic_write(path,b'other_filter = require("other").filter\n')
        with patch.object(core,'compile_dictionary',self.fake_compile):
            core.sync(self.config);core.sync(self.config,force=True)
        text=path.read_text()
        self.assertIn('other_filter = require("other").filter\n', text)
        self.assertEqual(text.count(core.LOADER_BEGIN),1)
        self.assertIn('ginput_correction = require("ginput").translator',text)

    def test_invalid_shared_lua_loader_prevents_publish(self):
        path=self.root/'rime/rime.lua'
        core.atomic_write(path,core.LOADER_BEGIN.encode())
        with patch.object(core,'compile_dictionary',self.fake_compile),self.assertRaises(core.InputError):
            core.sync(self.config)
        self.assertEqual(path.read_text(),core.LOADER_BEGIN)
        self.assertFalse((self.root/'rime/ginput.schema.yaml').exists())

    def test_learning_changed_during_compile_is_retried(self):
        def changing(config,words,revision,stage):
            self.fake_compile(config,words,revision,stage)
            (self.root/'learned.tsv').write_text('新词\txin ci\t3000\n')
        with patch.object(core,'compile_dictionary',changing),self.assertRaises(core.Pending):
            core.sync(self.config)
        self.assertFalse((self.root/'rime/ginput.dict.yaml').exists())

    def test_unchanged_dictionary_does_not_rebuild(self):
        with patch.object(core, 'compile_dictionary', side_effect=self.fake_compile) as compile_mock:
            core.sync(self.config)
            core.sync(self.config)
        self.assertEqual(compile_mock.call_count, 1)

    def test_compile_failure_preserves_previous(self):
        with patch.object(core, 'compile_dictionary', self.fake_compile):
            old = core.sync(self.config)
        Path(self.config['words']).write_text('新词\txin ci\t10')
        with patch.object(core, 'compile_dictionary', side_effect=core.InputError('compiler failed')):
            with self.assertRaises(core.InputError):
                core.sync(self.config)
        self.assertEqual(core.read_status(self.config), old)

    def test_mid_publish_failure_restores_all_files(self):
        with patch.object(core, 'compile_dictionary', self.fake_compile):
            core.sync(self.config)
        old = (self.root / 'rime/ginput.dict.yaml').read_bytes()
        Path(self.config['words']).write_text('新词\txin ci\t10')
        real_write = core.atomic_write
        failed = False

        def fail_once(path, data):
            nonlocal failed
            if path == self.root / 'rime/build/ginput.prism.bin' and not failed:
                failed = True
                raise OSError('simulated disk failure')
            real_write(path, data)

        with patch.object(core, 'compile_dictionary', self.fake_compile), patch.object(core, 'atomic_write', fail_once):
            with self.assertRaises(OSError):
                core.sync(self.config)
        self.assertEqual((self.root / 'rime/ginput.dict.yaml').read_bytes(), old)
        self.assertFalse((self.root / 'state/pending-transaction.json').exists())

    def test_typing_defers_update(self):
        result = type('Result', (), {'returncode': 0, 'stdout': '2\n'})()
        config = self.config | {'reload': 'fcitx4'}
        with patch.object(core.subprocess, 'run', return_value=result), self.assertRaises(core.Pending):
            core.sync(config)
        self.assertFalse((self.root / 'rime/ginput.dict.yaml').exists())

    def test_request_failure_is_not_reported_as_reload_success(self):
        with patch.object(core, 'ensure_inactive'), patch.object(core, 'compile_dictionary', self.fake_compile), patch.object(core.subprocess, 'run', side_effect=OSError('no bus')):
            result = core.sync(self.config | {'reload': 'fcitx4'})
        self.assertEqual(result['status'], 'reload_pending')

    def test_lock_prevents_two_publishers(self):
        state = Path(self.config['state_dir'])
        with core.locked(state), self.assertRaises(core.Pending):
            core.sync(self.config)

    def test_does_not_follow_managed_file_symlink(self):
        target = self.root / 'rime'
        target.mkdir()
        unrelated = self.root / 'unrelated'
        unrelated.write_text('keep')
        (target / 'ginput.dict.yaml').symlink_to(unrelated)
        with patch.object(core, 'compile_dictionary', self.fake_compile), self.assertRaises(core.InputError):
            core.sync(self.config)
        self.assertEqual(unrelated.read_text(), 'keep')

    def test_debounce_waits_for_stable_content(self):
        debounce = core.Debounce(1)
        self.assertFalse(debounce.ready('a', 0))
        self.assertFalse(debounce.ready('b', 0.5))
        self.assertFalse(debounce.ready('b', 1))
        self.assertTrue(debounce.ready('b', 1.6))


if __name__ == '__main__':
    unittest.main()
