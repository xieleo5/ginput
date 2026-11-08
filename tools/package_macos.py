#!/usr/bin/env python3
"""Build a portable source bundle, never reading the user's personal vocabulary."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ginput import __version__
from ginput.errors import InputError
from ginput.platforms.macos import base_source


# Explicit public resources, not a recursive scan of users' working directories.
PUBLIC_FILES = (
    'ginput/__init__.py', 'ginput/__main__.py', 'ginput/cli.py', 'ginput/core.py',
    'ginput/dictionary.py', 'ginput/errors.py', 'ginput/learning.py', 'ginput/panel_ui.py',
    'ginput/skin.py', 'ginput/platforms/__init__.py', 'ginput/platforms/macos.py',
    'ginput/platforms/linux/__init__.py', 'ginput/platforms/linux/desktop.py',
    'ginput/platforms/linux/learning.py', 'ginput/platforms/linux/panel_ui.py',
    'ginput/platforms/linux/feedback.py',
    'ginput/platforms/linux/skin.py', 'ginput/rime/ginput.schema.yaml',
    'ginput/rime/ginput.lua', 'ginput/examples/words.example.tsv',
)


def public_bytes(relative):
    path = ROOT / relative
    candidate = path
    while candidate != ROOT:
        if candidate.is_symlink(): raise ValueError(f'拒绝打包符号链接：{candidate}')
        candidate = candidate.parent
    return path.read_bytes()


def package(output: Path, dictionary: Path):
    if dictionary.is_symlink(): raise ValueError('基础词库必须是普通文件，不能为符号链接。')
    base_source(dictionary)
    if output.exists(): raise ValueError('输出文件已存在；请换一个名称，避免覆盖。')
    prefix = f'ginput-macos-{__version__}'
    payload = {name: public_bytes(name) for name in PUBLIC_FILES}
    payload['data/pinyin_simp.dict.yaml'] = dictionary.read_bytes()
    for name in ('pinyin-simp-APACHE-2.0.txt', 'pinyin-simp-AUTHORS.txt'):
        payload['licenses/' + name] = public_bytes('licenses/' + name)
    payload['README.md'] = public_bytes('docs/macos.md')
    payload['install.command'] = public_bytes('platforms/macos/install.command')
    payload['squirrel.custom.example.yaml'] = public_bytes('platforms/macos/squirrel.custom.example.yaml')
    manifest = {'version': __version__, 'kind': 'source-only', 'personal_data': False,
                'base_source': 'https://github.com/rime/rime-pinyin-simp',
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}}
    payload['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    # x mode prevents overwriting a concurrent or existing build.
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(prefix + '/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name == 'install.command' else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-dictionary', type=Path, default=Path('/usr/share/rime-data/pinyin_simp.dict.yaml'))
    args = parser.parse_args()
    try:
        output = package(args.output.expanduser().resolve(), args.base_dictionary.expanduser().absolute())
        print(f'{output}\nSHA256 {hashlib.sha256(output.read_bytes()).hexdigest()}')
    except (OSError, ValueError, InputError) as exc:
        print(f'package_macos: {exc}', file=sys.stderr)
        raise SystemExit(1)
