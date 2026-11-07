"""Real Rime tests; no desktop connection or real user configuration is touched."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from ginput import core

ROOT = Path(__file__).resolve().parents[1]
SHARED = Path(os.environ.get('RIME_SHARED_DATA', '/usr/share/rime-data'))
DEPLOYER = os.environ.get('RIME_DEPLOYER', 'rime_deployer')
AVAILABLE = bool(shutil.which(DEPLOYER)) and (SHARED / 'pinyin_simp.dict.yaml').is_file()
if os.environ.get('GINPUT_REQUIRE_RIME') == '1' and not AVAILABLE:
    raise RuntimeError('Rime integration dependencies are required but missing')


@unittest.skipUnless(AVAILABLE, 'requires librime-bin, librime-dev, rime-data-pinyin-simp and a C compiler')
class RimeIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.probe = cls.root / 'rime-probe'
        flags = []
        if os.environ.get('RIME_INCLUDE_DIR'):
            flags += ['-I' + os.environ['RIME_INCLUDE_DIR']]
        if os.environ.get('RIME_LIBRARY_DIR'):
            flags += ['-L' + os.environ['RIME_LIBRARY_DIR']]
        subprocess.run(['cc', str(ROOT / 'tests/rime_probe.c'), *flags, '-lrime', '-o', str(cls.probe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.config = core.default_config() | {
            'words': str(self.folder / 'words.tsv'),
            'rime_dir': str(self.folder / 'rime'),
            'state_dir': str(self.folder / 'state'),
            'shared_data': str(SHARED), 'deployer': DEPLOYER, 'reload': 'none',
        }
        Path(self.config['words']).write_text('示例输入法\tshi li shu ru fa\t10000\n')

    def candidates(self, keys):
        output = subprocess.check_output([str(self.probe), str(SHARED), self.config['rime_dir'], keys], text=True)
        return output.splitlines()

    def assert_commits(self, keys, expected):
        result = subprocess.run([str(self.probe), str(SHARED), self.config['rime_dir'], keys,
                                 '--commit', expected], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f'{keys}: {result.stdout} {result.stderr}')
        self.assertEqual(result.stdout.strip(), expected)

    def test_base_pinyin_and_personal_word(self):
        result = core.sync(self.config)
        self.assertEqual(result['status'], 'published')
        self.assertIn('你好', self.candidates('nihao'))
        self.assertIn('示例输入法', self.candidates('shilishurufa'))

    def test_initials_and_mixed_full_spelling_commit_all_input(self):
        core.sync(self.config)
        for keys, expected in [('nh', '你好'), ('nih', '你好'), ('nhao', '你好'),
                               ('zg', '中国'), ('zhg', '中国'), ('slsrf', '示例输入法'),
                               ('slshrf', '示例输入法'), ('shilisrf', '示例输入法')]:
            with self.subTest(keys=keys):
                self.assert_commits(keys, expected)

    def test_common_transpositions_commit_all_input(self):
        core.sync(self.config)
        for keys, expected in [('nihoa', '你好'), ('zhognguo', '中国'), ('beijign', '北京'),
                               ('shagnhai', '上海'), ('tainkong', '天空')]:
            with self.subTest(keys=keys):
                self.assertIn(expected, self.candidates(keys)[:5])
                self.assert_commits(keys, expected)

    def test_keyboard_neighbor_mistakes_commit_all_input(self):
        core.sync(self.config)
        for keys in ('nihso', 'nihap'):
            with self.subTest(keys=keys):
                self.assertIn('你好', self.candidates(keys)[:5])
                self.assert_commits(keys, '你好')

    def test_correct_full_spelling_stays_first(self):
        core.sync(self.config)
        # No selection/learning before querying these: test fresh dictionary ranks.
        for keys, expected in [('nihao', '你好'), ('zhongguo', '中国'), ('beijing', '北京'),
                               ('shanghai', '上海'), ('shurufa', '输入法'), ('gongzuo', '工作'),
                               ('gaoxing', '高兴'), ('shilishurufa', '示例输入法')]:
            with self.subTest(keys=keys):
                self.assertEqual(self.candidates(keys)[0], expected)

    def test_partial_candidate_does_not_count_as_typo_correction(self):
        core.sync(self.config)
        # A visible 你好 matching only a prefix must not silently eat the final q.
        result = subprocess.run([str(self.probe), str(SHARED), self.config['rime_dir'],
                                 'nihaoq', '--commit', '你好'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)

    def test_recompile_adds_new_word_and_removes_old_static_word(self):
        core.sync(self.config)
        Path(self.config['words']).write_text('示例测试项目\tshi li ce shi xiang mu\t100000\n')
        core.sync(self.config)
        self.assertIn('示例测试项目', self.candidates('shiliceshixiangmu'))
        self.assertNotIn('示例输入法', (Path(self.config['rime_dir']) / 'ginput_personal.dict.yaml').read_text())

    def test_watcher_applies_editor_save_automatically(self):
        config_path = self.folder / 'config.json'
        core.write_json(config_path, self.config)
        core.sync(self.config)
        before = core.read_status(self.config)['revision']
        log = self.folder / 'watch.log'
        with log.open('w') as output:
            process = subprocess.Popen([sys.executable, '-m', 'ginput', '--config', str(config_path), 'watch'], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            try:
                # Saving via atomic rename matches common desktop editors.
                core.atomic_write(Path(self.config['words']), '示例更新词条\tshi li geng xin ci tiao\t100000\n'.encode())
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and core.read_status(self.config).get('revision') == before:
                    time.sleep(0.2)
                self.assertNotEqual(core.read_status(self.config)['revision'], before, log.read_text())
                self.assertIn('示例更新词条', self.candidates('shiligengxincitiao'))
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
