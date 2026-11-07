"""Native panel event regression; does not connect to the desktop."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

INCLUDE = Path(os.environ.get('FCITX_INCLUDE_DIR', '/usr/include'))
AVAILABLE = (INCLUDE / 'fcitx/module.h').is_file()
if os.environ.get('GINPUT_REQUIRE_PANEL') == '1' and not AVAILABLE:
    raise RuntimeError('Native panel tests require fcitx-libs-dev headers')


@unittest.skipUnless(AVAILABLE, 'requires fcitx-libs-dev and a C compiler')
class PanelTests(unittest.TestCase):
    def test_grid_paging_selection_and_renderer_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'panel-probe'
            source = Path(__file__).with_name('panel_probe.c')
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            '-isystem', str(INCLUDE), str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
