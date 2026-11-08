import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from . import __version__
from .core import (InputError, Pending, atomic_write, compile_dictionary, config_path,
                   fingerprint, initialize, load_config, read_status, read_words, sync, watch, xdg)


# Backward-compatible export for existing service consumers.
from .platforms.linux.desktop import service_unit, service
from .platforms import host_platform


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='ginput', description='Guitar Input · 个人拼音与本地词库')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--config', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='创建本机配置与个人词库，不修改输入法设置')
    init.add_argument('--platform', choices=['linux', 'macos'])
    sub.add_parser('validate', help='检查词库格式')
    sub.add_parser('edit', help='使用默认文本编辑器打开个人词库')
    sub.add_parser('status', help='查看路径和上次更新状态')
    sub.add_parser('doctor', help='检查依赖，不安装软件')
    build = sub.add_parser('build', help='在独立目录中验证编译')
    build.add_argument('--output', type=Path, required=True)
    synchronize = sub.add_parser('sync', help='编译并更新 Guitar Input 文件')
    synchronize.add_argument('--force', action='store_true')
    synchronize.add_argument('--no-reload', action='store_true', help='只发布文件，不向桌面请求重载')
    sub.add_parser('watch', help='监视词库，保存后自动更新')
    svc = sub.add_parser('service', help='管理用户级自动更新服务')
    svc.add_argument('action', choices=['enable', 'disable'])
    learning = sub.add_parser('learning', help='管理本机输入历史与个人学习')
    learning.add_argument('action', choices=['enable', 'disable', 'status', 'clear-history'])
    learning.add_argument('--retention-days', type=int, default=7, help='原始历史保留天数；0 表示直到手动清除')
    learning.add_argument('--include-learned', action='store_true', help='清除历史时同时清除本工具的自动词表和相邻词统计；保留手工词库与 Rime userdb')
    skin = sub.add_parser('skin', help='生成或启用本机 Fcitx 皮肤')
    skin_sub = skin.add_subparsers(dest='skin_action', required=True)
    skin_build = skin_sub.add_parser('build')
    skin_build.add_argument('--output',type=Path,required=True)
    skin_build.add_argument('--artwork',type=Path)
    skin_build.add_argument('--crop',help='图片区域 x,y,width,height')
    skin_build.add_argument('--height',type=int,default=180)
    skin_build.add_argument('--accent-crop',help='左侧装饰区域 x,y,width,height')
    skin_build.add_argument('--opacity',type=float,default=.22)
    skin_activate = skin_sub.add_parser('activate')
    skin_activate.add_argument('directory',type=Path)
    mac = sub.add_parser('macos', help='鼠须管配置包安装（不安装鼠须管、不自动部署）')
    mac_sub = mac.add_subparsers(dest='mac_action', required=True)
    mac_install = mac_sub.add_parser('install')
    mac_install.add_argument('--base-dictionary', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.config is None:
            args.config = config_path(args.platform if args.command == 'init' else None)
        args.config = args.config.expanduser().resolve()
        if args.command == 'macos':
            from .platforms.macos import install
            print(json.dumps(install(args.config, args.base_dictionary.expanduser().resolve()), ensure_ascii=False, indent=2))
            return 0
        if args.command in ('learning', 'skin', 'service') and host_platform() != 'linux':
            raise InputError('此功能属于 Linux Fcitx 适配器，macOS 请使用鼠须管原生候选框和 Rime 选词学习。')
        if args.command == 'learning':
            from .learning import LearningStore, configure
            store = LearningStore()
            try:
                if args.action == 'enable': configure(True,args.retention_days)
                elif args.action == 'disable': configure(False,store.retention_days)
                elif args.action == 'clear-history': store.clear(args.include_learned)
                print(json.dumps(store.status(),ensure_ascii=False,indent=2))
            finally: store.close()
            return 0
        if args.command == 'skin':
            from .skin import build, activate
            if args.skin_action == 'build':
                crop = tuple(map(int,args.crop.split(','))) if args.crop else None
                if crop and len(crop)!=4: raise InputError('crop 需要 x,y,width,height 四个数字。')
                accent = tuple(map(int,args.accent_crop.split(','))) if args.accent_crop else None
                if accent and len(accent)!=4: raise InputError('accent-crop 需要四个数字。')
                build(args.output.expanduser().resolve(), args.artwork.expanduser().resolve() if args.artwork else None, crop, args.height, accent, args.opacity)
                print(f'皮肤已生成：{args.output}')
            else:
                backup = activate(args.directory.expanduser().resolve())
                print(f'皮肤配置已启用；备份：{backup}。输入完成后重载 Fcitx 生效。')
            return 0
        if args.command == 'init':
            config = initialize(args.config, args.platform)
            print(f"配置：{args.config}\n个人词库：{config['words']}\n尚未修改系统输入法；下一步运行 doctor。")
            return 0
        config = load_config(args.config)
        if args.command == 'validate':
            _, words = read_words(config)
            print(f'格式检查通过：{len(words)} 条个人词条。')
        elif args.command == 'edit':
            subprocess.run(['open' if config.get('platform') == 'macos' else 'xdg-open', config['words']], check=True)
        elif args.command == 'status':
            print(json.dumps({'config': str(args.config), 'words': config['words'],
                              'reload': config['reload'], 'last_sync': read_status(config)}, ensure_ascii=False, indent=2))
        elif args.command == 'doctor':
            if config.get('platform') == 'macos':
                from .platforms.macos import doctor
                checks = doctor(config)
                for name, ok in checks.items(): print(f"{'OK' if ok else 'MISSING'}  {name}")
                print('Lua 与候选输入需在鼠须管重新部署后实测；这里只检查路径，不保证输入法已生效。')
                return 0 if all(checks.values()) else 1
            checks = {config['deployer']: bool(shutil.which(config['deployer'])),
                      'pinyin_simp.dict.yaml': (Path(config['shared_data']) / 'pinyin_simp.dict.yaml').is_file(),
                      'default.yaml': (Path(config['shared_data']) / 'default.yaml').is_file()}
            checks['librime-plugin-lua'] = any(Path('/usr/lib').glob('*/rime-plugins/librime-lua.so')) or Path('/usr/lib/rime-plugins/librime-lua.so').is_file()
            if config['reload'] == 'fcitx4':
                checks['fcitx-remote'] = bool(shutil.which('fcitx-remote'))
                checks['fcitx-rime addon'] = Path('/usr/share/fcitx/addon/fcitx-rime.conf').is_file()
            for name, ok in checks.items():
                print(f"{'OK' if ok else 'MISSING'}  {name}")
            return 0 if all(checks.values()) else 1
        elif args.command == 'build':
            if config.get('platform') == 'macos':
                raise InputError('macOS 使用 sync 生成源码，再由鼠须管重新部署；不复用 Linux 编译文件。')
            output = args.output.expanduser().resolve()
            if output.exists() and any(output.iterdir()):
                raise InputError('编译输出目录必须为空，避免覆盖其他文件。')
            data, words = read_words(config)
            compile_dictionary(config, words, fingerprint(config, data), output)
            print(f'编译验证通过：{output}')
        elif args.command == 'sync':
            if args.no_reload and config.get('platform') != 'macos':
                config['reload'] = 'none'
            result = sync(config, args.force)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'watch':
            watch(args.config)
        elif args.command == 'service':
            if config.get('platform') != 'linux': raise InputError('macOS 暂无后台自动部署服务。')
            service(args.config, args.action)
    except Pending as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (InputError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f'ginput: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
