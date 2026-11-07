#!/usr/bin/env python3
"""Build and install the optional native panel module for this user only."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser=argparse.ArgumentParser()
parser.add_argument('--include-dir',type=Path,default=Path('/usr/include'))
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
if not (args.include_dir/'fcitx/module.h').exists():
    parser.error('缺少 fcitx-libs-dev 头文件；可用 --include-dir 指定已有头文件目录。')
target=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'fcitx'
with tempfile.TemporaryDirectory() as folder:
    library=Path(folder)/'fcitx-ginput-panel.so'
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fPIC','-shared',
                    '-isystem',str(args.include_dir),str(root/'native/panel.c'),'-o',str(library)],check=True)
    for source,relative in ((library,'lib/fcitx-ginput-panel.so'),
                           (root/'native/fcitx-ginput-panel.conf','addon/fcitx-ginput-panel.conf')):
        dest=target/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists(): shutil.copy2(dest,dest.with_suffix(dest.suffix+'.previous'))
        temp=dest.with_suffix(dest.suffix+'.new')
        shutil.copyfile(source,temp)
        temp.replace(dest)
print('下拉候选插件已安装到用户目录。完成当前输入后重启 Fcitx 加载。')
