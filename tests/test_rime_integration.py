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
from unittest.mock import patch

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
        environment=patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.folder/'runtime')})
        environment.start();self.addCleanup(environment.stop)
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

    def test_unlisted_full_spelling_sentence_precedes_even_high_weight_correction(self):
        Path(self.config['words']).write_text('太少了\ttai shao le\t1000000\n我们少了\two men shao le\t1000000\n')
        core.sync(self.config)
        for keys,expected,wrong in [('taihaole','太好了','太少了'),('womenhaole','我们好了','我们少了')]:
            with self.subTest(keys=keys):
                self.assertNotIn(expected+'\t',(Path(self.config['rime_dir'])/'ginput.lexicon.tsv').read_text())
                choices=self.candidates(keys)
                self.assertEqual(choices[0],expected)
                self.assertGreater(choices.index(wrong),choices.index(expected))
                self.assert_commits(keys,expected)

    def test_ambiguous_vowel_split_keeps_a_cost_without_penalizing_all_sentences(self):
        core.sync(self.config)
        self.assertEqual(self.candidates('biaodia')[0],'标点')
        self.assertEqual(self.candidates('taihaolea')[0],'太好了啊')

    def test_v_aliases_yu_without_changing_nv_lv(self):
        core.sync(self.config)
        for keys, word in [('v', '雨'), ('van', '原'), ('ve', '月'), ('vn', '云'),
                           ('vyan', '语言'), ('nv', '女'), ('lv', '绿')]:
            with self.subTest(keys=keys):
                self.assert_commits(keys, word)

    def test_partial_candidate_does_not_count_as_typo_correction(self):
        core.sync(self.config)
        # A visible 你好 matching only a prefix must not silently eat the final q.
        result = subprocess.run([str(self.probe), str(SHARED), self.config['rime_dir'],
                                 'nihsoq', '--commit', '你好'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)

    def test_bounded_cost_and_legal_abbreviations_precede_edits(self):
        core.sync(self.config)
        for keys,word in [('gem','哥们'),('zhonwgen','中文'),('biaodia','标点')]:
            with self.subTest(keys=keys):
                self.assertEqual(self.candidates(keys)[0],word)
                self.assert_commits(keys,word)
        choices=self.candidates('shiyou')
        self.assertLess(choices.index('是有'),choices.index('石头'))
        for keys,word in [('nihsoq','你好'),('nihapq','你好'),('zhonwgne','中文')]:
            result=subprocess.run([str(self.probe),str(SHARED),self.config['rime_dir'],keys,'--commit',word],capture_output=True,text=True)
            self.assertIn(result.returncode,(5,6),keys+result.stdout+result.stderr)

    def test_personal_preferences_and_compounds_are_ordinary_dictionary_entries(self):
        Path(self.config['words']).write_text('是有\tshi you\t100000\n无限量\twu xian liang\t100000\n')
        core.sync(self.config)
        for keys,word in [('shiyou','是有'),('wuxianliang','无限量')]:
            self.assertEqual(self.candidates(keys)[0],word)
            self.assert_commits(keys,word)

    def test_ascii_punctuation_alone_and_after_composition(self):
        core.sync(self.config)
        punctuation='!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'
        for typed,expected in [(punctuation,punctuation),('nihao ,.', '你好,.')]+[('nihao'+mark,'你好'+mark) for mark in punctuation if mark not in ',.']:
            with self.subTest(typed=typed):
                result=subprocess.run([str(self.probe),str(SHARED),self.config['rime_dir'],typed,'--type',expected],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_comma_period_page_candidates_without_committing_punctuation(self):
        core.sync(self.config)
        for typed,index in [('ni, ',0),('ni. ',5),('ni.., ',5),('ni., ',0)]:
            with self.subTest(typed=typed):
                expected=self.candidates('ni')[index]
                result=subprocess.run([str(self.probe),str(SHARED),self.config['rime_dir'],typed,'--type',expected],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_context_is_window_scoped_and_cannot_override_spelling_cost(self):
        core.sync(self.config)
        runtime=self.folder/'runtime/ginput';runtime.mkdir(parents=True)
        (runtime/'context.tsv').write_text(f'one\t{int(time.time())}\n是有\t1\n石头\t1\n')
        (runtime/'active-window').write_text('one\n')
        self.assertEqual(self.candidates('shiyou')[0],'是有')
        choices=self.candidates('shiyou');self.assertGreater(choices.index('石头'),choices.index('石油'))
        (runtime/'active-window').write_text('two\n')
        self.assertEqual(self.candidates('shiyou')[0],'石油')
        (runtime/'active-window').write_text('one\n')
        (runtime/'context.tsv').write_text('one\t1\n是有\t1\n')
        self.assertEqual(self.candidates('shiyou')[0],'石油')

    def test_deletion_hint_cannot_bury_common_full_spelling_below_rare_words(self):
        core.sync(self.config)
        baseline=self.candidates('shi')
        self.assertEqual(baseline[0],'是')
        runtime=self.folder/'runtime/ginput';runtime.mkdir(parents=True)
        (runtime/'active-window').write_text('one\n')
        for weight in (-.6,-1,-100):
            with self.subTest(weight=weight):
                (runtime/'context.tsv').write_text(f'one\t{int(time.time())}\n是\t{weight}\n')
                choices=self.candidates('shi')
                self.assertLessEqual(choices.index('是'),3)
                self.assertEqual([w for w in choices if w!='是'],[w for w in baseline if w!='是'])
        (runtime/'context.tsv').write_text(f'one\t{int(time.time())}\n是\t-0.6\n石\t0.6\n')
        choices=self.candidates('shi')
        self.assertEqual(choices[0],'石')
        self.assertLess(choices.index('是'),5)

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


@unittest.skipUnless(AVAILABLE, 'requires real librime test dependencies')
class SquirrelSourceIntegration(unittest.TestCase):
    setUpClass = classmethod(RimeIntegration.setUpClass.__func__)
    tearDownClass = classmethod(RimeIntegration.tearDownClass.__func__)
    candidates = RimeIntegration.candidates
    assert_commits = RimeIntegration.assert_commits
    # Reuse only probe setup, not all Linux-specific watcher/context tests.
    def setUp(self):
        RimeIntegration.setUp(self)
        self.config.update(platform='macos', reload='manual', base_dictionary=str(SHARED/'pinyin_simp.dict.yaml'))

    def test_squirrel_source_deploy_and_input(self):
        core.atomic_write(Path(self.config['rime_dir'])/'rime.lua', b'return {}\n')
        result = core.sync(self.config)
        self.assertEqual(result['status'], 'deployment_required')
        target = Path(self.config['rime_dir'])
        self.assertFalse((target/'build').exists())
        core.atomic_write(target/'default.custom.yaml', b'patch:\n  schema_list:\n    - schema: ginput\n')
        built = subprocess.run([DEPLOYER, '--build', str(target), str(SHARED), str(target/'build')], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        for keys, word in [('nihao','你好'),('nh','你好'),('vyan','语言'),('nihoa','你好'),('slsrf','示例输入法')]:
            with self.subTest(keys=keys): self.assert_commits(keys, word)
        result=subprocess.run([str(self.probe),str(SHARED),str(target),'nihao!','--type','你好!'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        # A stale Linux context bridge must not influence Mac ranking.
        runtime=self.folder/'runtime/ginput';runtime.mkdir(parents=True)
        (runtime/'active-window').write_text('one\n')
        (runtime/'context.tsv').write_text(f'one\t{int(time.time())}\n是有\t1\n')
        self.assertEqual(self.candidates('shiyou')[0], '石油')
