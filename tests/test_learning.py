"""Private history tests use isolated XDG directories, never desktop input."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from ginput import core
from ginput.learning import LearningStore, configure, paths, private_write


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        environment={name:str(self.root/name) for name in
                     ('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_RUNTIME_DIR')}
        self.env=patch.dict(os.environ,environment);self.env.start();self.addCleanup(self.env.stop)
        self.now=2000000000
        self.store=LearningStore(clock=lambda:self.now);self.addCleanup(self.store.close)
        private_write(paths()[4]/'ginput.lexicon.tsv',
                      '你好\tni hao\t100\n你\tni\t100\n好\thao\t100\n是\tshi\t100\n有\tyou\t100\n中国\tzhong guo\t100\n')

    def commit(self,text='你好',raw='ni hao',window='one'):
        self.store.event(dict(event='commit',window=window,text=text,preedit=raw))

    def key(self,key,state=0,composing=False,window='one'):
        self.store.event(dict(event='key',window=window,key=key,state=state,composing=composing))

    def enable(self,days=0):
        configure(True,days)

    def test_disabled_default_and_sensitive_events_never_create_database(self):
        self.commit()
        self.assertFalse((paths()[1]/'learning.sqlite3').exists())
        self.enable()
        self.store.event(dict(event='commit',window='one',text='secret',sensitive=True))
        self.assertFalse((paths()[1]/'learning.sqlite3').exists())

    def test_deleted_input_is_retained_without_learning_rejected_word(self):
        self.enable();self.key(ord('n'),composing=True);self.commit();self.key(0xff08)
        self.assertEqual(self.store.status()['events'],3)
        self.assertEqual(self.store.status()['terms'],0)
        self.assertNotIn('你好', (paths()[2]/'learned.tsv').read_text())
        rows=self.store.db.execute('SELECT payload FROM events').fetchall()
        self.assertEqual(json.loads(rows[1][0])['text'],'你好')
        self.assertIn('你好\t-0.6000',(paths()[3]/'context.tsv').read_text())

    def test_composition_backspace_is_not_a_committed_word_rejection(self):
        self.enable();self.commit();self.key(0xff08,composing=True)
        self.assertEqual(self.store.status()['terms'],1)

    def test_select_all_delete_and_replacement_do_not_leave_context(self):
        self.enable();self.commit();self.commit('中国','zhong guo')
        self.key(ord('a'),state=4);self.key(0xffff)
        self.assertEqual(self.store.windows['one']['text'],'')
        self.assertEqual(self.store.status()['terms'],0)
        self.commit();self.key(ord('a'),state=4);self.commit('中国','zhong guo')
        self.assertEqual(self.store.windows['one']['text'],'中国')
        self.assertEqual(self.store.status()['terms'],1)

    def test_uncertain_caret_operations_do_not_invent_negative_feedback(self):
        self.enable();self.commit();self.key(0xff08,state=4)
        self.assertEqual(self.store.status()['terms'],1)
        self.assertEqual(self.store.windows['one']['text'],'')

    def test_deleting_ascii_punctuation_does_not_reject_previous_word(self):
        self.enable();self.key(ord(','),composing=True);self.commit();self.key(0xff08)
        self.assertEqual(self.store.status()['terms'],1)
        self.assertEqual(self.store.windows['one']['text'],'你好')

    def test_context_is_separate_per_window_and_expires(self):
        self.enable();self.commit(window='one');self.commit('中国','zhong guo',window='two')
        context=(paths()[3]/'context.tsv').read_text()
        self.assertTrue(context.startswith('two\t'));self.assertNotIn('你好',context)
        self.key(ord('n'),window='one')
        self.assertIn('你好',(paths()[3]/'context.tsv').read_text())
        self.now+=1801;self.key(ord('n'),window='one')
        self.assertNotIn('你好',(paths()[3]/'context.tsv').read_text())

    def test_window_can_learn_a_new_compound_from_character_readings(self):
        self.enable();self.commit('是有','shiyou')
        self.assertIn('是有\tshi you\t3000',(paths()[2]/'learned.tsv').read_text())
        self.assertEqual(self.store.pronunciation('是有','shy'),'shi you')
        self.assertIsNone(self.store.pronunciation('是有','bad'))

    def test_long_retention_survives_restart_and_manual_clear_preserves_terms(self):
        self.enable();self.commit();self.now+=86400*400;self.key(ord('n'))
        self.assertEqual(self.store.status()['events'],2)
        self.store.close();self.store.connect()
        self.assertEqual(self.store.status()['events'],2)
        self.store.clear()
        self.assertEqual(self.store.status()['events'],0)
        self.assertEqual(self.store.status()['terms'],1)
        self.assertEqual(self.store.windows,{})
        self.store.clear(include_learned=True)
        self.assertEqual(self.store.status()['terms'],0)
        self.assertNotIn('你好',(paths()[2]/'learned.tsv').read_text())

    def test_retention_prunes_only_raw_history_and_disable_takes_effect(self):
        self.enable(7);self.commit();self.now+=86400*8;self.key(ord('n'))
        self.assertEqual(self.store.status()['events'],1)
        self.assertEqual(self.store.status()['terms'],1)
        configure(False,7);self.commit()
        self.assertFalse(self.store.enabled);self.assertIsNone(self.store.db)
        self.assertFalse((paths()[3]/'context.tsv').exists())

    def test_private_permissions_and_manual_dictionary_wins(self):
        self.enable();self.commit()
        for path in (paths()[0],paths()[1]/'learning.sqlite3',paths()[2]/'learned.tsv',paths()[3]/'context.tsv'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600,str(path))
        manual=paths()[2]/'words.tsv';private_write(manual,'你好\tni hao\t20\n')
        config=core.default_config()|{'words':str(manual)}
        before,words=core.read_words(config)
        self.assertEqual(words,[core.Word('你好','ni hao',20)])
        self.commit('是有','shi you')
        after,words=core.read_words(config)
        self.assertNotEqual(before,after)
        self.assertIn(core.Word('是有','shi you',3000),words)

    def test_previous_phrase_statistics_persist_but_window_text_does_not(self):
        self.enable();self.commit();self.commit('中国','zhong guo')
        self.store.close();self.store=LearningStore(clock=lambda:self.now)
        self.addCleanup(self.store.close)
        self.assertFalse(self.store.windows)
        self.commit()
        self.assertIn('中国\t0.0800',(paths()[3]/'context.tsv').read_text())
