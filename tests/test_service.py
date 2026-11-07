from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ginput.cli import service_unit


class ServiceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('systemd-analyze'), 'requires systemd-analyze')
    def test_systemd_accepts_generated_unit_with_space_in_path(self):
        with tempfile.TemporaryDirectory(prefix='ginput service ') as folder:
            root = Path(folder)
            unit = root / 'ginput-watch.service'
            unit.write_text(service_unit(root, root / 'config.json'))
            result = subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
