import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ginput.cli import main
from ginput.platforms.linux.feedback import FeedbackStore


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'XDG_STATE_HOME':self.temp.name});env.start();self.addCleanup(env.stop)
        self.now=10000
        self.store=FeedbackStore(clock=lambda:self.now);self.addCleanup(self.store.close)
        self.commit=dict(event='commit',window='one',text='太少了',preedit='taihaole',
            candidates=[{'text':'太少了','comment':'纠错 · tai shao le'},{'text':'太好了','comment':''}],selected_index=0,expanded=False)

    def test_only_explicit_mark_persists_snapshot_with_candidates_and_choice(self):
        self.store.observe(self.commit)
        self.assertFalse(self.store.path.exists())
        result=self.store.mark('one')
        self.assertTrue(result['saved'])
        report=self.store.reports()[0]
        for key in ('preedit','text','candidates','selected_index'):
            self.assertEqual(report['input'][key],self.commit[key])
        self.assertEqual(report['status'],'open')
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)

    def test_delete_and_new_composition_keep_previous_commit(self):
        self.store.observe(self.commit)
        for event in [dict(event='key',window='one',key=65288),dict(event='key',window='one',key=ord('n'))]:self.store.observe(event)
        self.store.mark('one')
        self.assertEqual(self.store.reports()[0]['input']['preedit'],'taihaole')

    def test_duplicate_mark_is_idempotent_and_survives_restart(self):
        self.store.observe(self.commit)
        first=self.store.mark('one');second=self.store.mark('one')
        self.assertEqual(first['id'],second['id'])
        self.store.close()
        reader=FeedbackStore();self.addCleanup(reader.close)
        self.assertEqual(len(reader.reports()),1)

    def test_snapshot_is_copied_and_windows_do_not_mix(self):
        self.store.observe(self.commit)
        self.commit['candidates'][0]['text']='changed'
        self.assertFalse(self.store.mark('two')['saved'])
        self.store.mark('one')
        self.assertEqual(self.store.reports()[0]['input']['candidates'][0]['text'],'太少了')

    def test_sensitive_and_expired_inputs_are_not_marked(self):
        self.store.observe(self.commit|{'sensitive':True})
        self.assertFalse(self.store.mark('one')['saved'])
        self.store.observe(self.commit);self.now+=1801
        self.assertFalse(self.store.mark('one')['saved'])
        self.assertFalse(self.store.path.exists())

    def test_review_can_annotate_resolve_and_clear(self):
        self.store.observe(self.commit);identifier=self.store.mark('one')['id']
        self.store.update(identifier,expected='太好了',note='正确全拼不应纠错')
        self.assertEqual(self.store.reports()[0]['expected'],'太好了')
        self.store.update(identifier,status='resolved')
        self.assertEqual(self.store.reports(),[])
        self.assertEqual(len(self.store.reports('all')),1)
        self.store.clear();self.assertEqual(self.store.reports('all'),[])

    def test_cli_lists_and_annotates_saved_reports(self):
        self.store.observe(self.commit);identifier=self.store.mark('one')['id']
        with patch('ginput.cli.host_platform',return_value='linux'),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['feedback','list']),0)
        self.assertEqual(json.loads(output.getvalue())[0]['id'],identifier)
        with patch('ginput.cli.host_platform',return_value='linux'),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['feedback','note',identifier,'--expected','太好了']),0)
            self.assertEqual(main(['feedback','resolve',identifier]),0)
        self.assertEqual(self.store.reports('all')[0]['expected'],'太好了')

    def test_non_pinyin_commit_does_not_replace_last_pinyin_snapshot(self):
        self.store.observe(self.commit)
        self.store.observe(dict(event='commit',window='one',text='!',preedit=''))
        self.store.mark('one')
        self.assertEqual(self.store.reports()[0]['input']['text'],'太少了')
