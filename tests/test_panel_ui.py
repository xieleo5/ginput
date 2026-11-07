"""Grid layout and alpha compositing tests; run under Xvfb in CI."""
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

try:
    from ginput.panel_ui import CandidateWindow, Gtk, cairo
    AVAILABLE = Gtk.init_check()[0]
except ImportError:
    AVAILABLE = False
if os.environ.get('GINPUT_REQUIRE_UI') == '1' and not AVAILABLE:
    raise RuntimeError('UI tests require GTK 3, python3-gi-cairo and a display (Xvfb is sufficient)')


@unittest.skipUnless(AVAILABLE, 'requires GTK 3 and a display')
class PanelUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        environment=patch.dict(os.environ,{name:str(Path(self.tmp.name)/name) for name in ('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_RUNTIME_DIR')})
        environment.start();self.addCleanup(environment.stop)
        self.a, self.b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.window = CandidateWindow(self.a)
        self.assertEqual(self.b.recv(32), b'ready')
        self.addCleanup(self.window.destroy)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.window.state = dict(serial=1,x=30,y=30,cursor_height=16,expanded=True,
                                 selected=6,previous=False,next=True,preedit='ni hao',
                                 words=['示例词']*20)
        self.window.arrange()

    def test_twenty_candidates_form_four_rows_of_five(self):
        cells = self.window.cells
        self.assertEqual(len(cells),20)
        self.assertEqual(len({c[0] for c in cells}),5)
        self.assertEqual(len({c[1] for c in cells}),4)
        for i,cell in enumerate(cells):
            self.assertEqual(cell[4],i)
            self.assertLessEqual(cell[0]+cell[2],self.window.total_w)
            if i % 5: self.assertGreaterEqual(cell[0],cells[i-1][0]+cells[i-1][2])

    def test_short_last_page_and_long_words_keep_columns(self):
        self.window.state['words']=['很长的示例候选文字'*12]*7
        self.window.arrange()
        self.assertEqual(len({c[1] for c in self.window.cells}),2)
        self.assertEqual(len({c[0] for c in self.window.cells}),5)
        monitor=self.window.get_display().get_monitor_at_point(30,30).get_workarea()
        self.assertLessEqual(self.window.total_w,monitor.width)

    def test_panel_background_has_real_alpha(self):
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,self.window.total_w,self.window.total_h)
        self.window.draw(None,cairo.Context(surface))
        surface.flush()
        # An empty point in the panel has alpha 0.22, not a solid pastel fill.
        x,y,w,h=self.window.body
        offset=int(y+h-2)*surface.get_stride()+int(x+w/2)*4+3
        alpha=surface.get_data()[offset]
        alpha=alpha if isinstance(alpha,int) else alpha[0]
        self.assertGreater(alpha,40)
        self.assertLess(alpha,80)

    def test_mouse_message_keeps_frame_identity(self):
        self.window.command('select',6)
        self.assertEqual(self.b.recv(128),b'select 1 6')

    def test_theme_opacity_loaded_from_local_config(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'XDG_CONFIG_HOME':folder}):
            path=Path(folder)/'fcitx/skin/ginput-custom'
            path.mkdir(parents=True)
            (path/'fcitx_skin.conf').write_text('[GuitarPanel]\nOpacity=0.35\n')
            self.window.load_theme()
            self.assertEqual(self.window.opacity,.35)
