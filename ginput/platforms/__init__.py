"""Small platform boundary. Paths and desktop behavior stay out of dictionary.py."""
import os
from pathlib import Path
import sys
from ..errors import InputError


def host_platform():
    if sys.platform == 'darwin': return 'macos'
    if sys.platform.startswith('linux'): return 'linux'
    raise InputError('目前支持 Linux 和 macOS；iOS 不是 Python CLI 运行目标。')


def paths_for(platform=None):
    platform = platform or host_platform()
    home = Path.home()
    if platform == 'macos':
        support = home / 'Library/Application Support/GuitarInput'
        return {'config': support / 'config.json', 'words': support / 'words.tsv',
                'state': support / 'state', 'base': support / 'resources/pinyin_simp.dict.yaml',
                'rime': home / 'Library/Rime',
                'shared': Path('/Library/Input Methods/Squirrel.app/Contents/SharedSupport')}
    if platform != 'linux': raise InputError('未知平台。')
    def xdg(key, fallback): return Path(os.environ.get(key, str(home / fallback))).expanduser().resolve()
    return {'config': xdg('XDG_CONFIG_HOME', '.config') / 'ginput/config.json',
            'words': xdg('XDG_DATA_HOME', '.local/share') / 'ginput/words.tsv',
            'state': xdg('XDG_STATE_HOME', '.local/state') / 'ginput',
            'rime': xdg('XDG_CONFIG_HOME', '.config') / 'fcitx/rime',
            'shared': Path('/usr/share/rime-data')}
