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
subprocess.run(['/usr/bin/python3', '-c', 'import gi, cairo; gi.require_foreign("cairo"); gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'],check=True)
target=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'fcitx'
with tempfile.TemporaryDirectory() as folder:
    library=Path(folder)/'fcitx-ginput-panel.so'
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fPIC','-shared',
                    '-isystem',str(args.include_dir),str(root/'platforms/linux/native/panel.c'),'-l:libX11.so.6','-o',str(library)],check=True)
    for source,relative in ((library,'lib/fcitx-ginput-panel.so'),
                           (root/'platforms/linux/native/fcitx-ginput-panel.conf','addon/fcitx-ginput-panel.conf')):
        dest=target/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists(): shutil.copy2(dest,dest.with_suffix(dest.suffix+'.previous'))
        temp=dest.with_suffix(dest.suffix+'.new')
        shutil.copyfile(source,temp)
        temp.replace(dest)
data=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'ginput'
data.mkdir(parents=True,exist_ok=True)
renderer=data/'panel_ui.py'
if renderer.exists(): shutil.copy2(renderer,renderer.with_suffix('.py.previous'))
temp=renderer.with_suffix('.py.new')
shutil.copyfile(root/'ginput/platforms/linux/panel_ui.py',temp)
temp.replace(renderer)
learning=data/'learning.py'
learning_temp=learning.with_suffix('.py.new')
shutil.copyfile(root/'ginput/platforms/linux/learning.py',learning_temp)
learning_temp.replace(learning)
print('下拉候选插件已安装到用户目录。完成当前输入后重启 Fcitx 加载。')
