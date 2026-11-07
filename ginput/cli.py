import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import __version__
from .core import (InputError, Pending, atomic_write, compile_dictionary, config_path,
                   fingerprint, initialize, load_config, read_status, read_words, sync, watch, xdg)


def service_unit(root: Path, config: Path) -> str:
    # Invoke the package via its module directory. Quote systemd's own syntax,
    # including percent specifiers and dollar environment expansions.
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    if any(c in str(root) + str(config) + sys.executable for c in '\n\r'):
        raise InputError('服务路径不能包含换行符。')
    command = ' '.join([quote(sys.executable), '-m', 'ginput', '--config', quote(config), 'watch'])
    # WorkingDirectory takes a literal path, not ExecStart's quoted argv syntax.
    return f'''[Unit]
Description=Guitar Input local dictionary watcher
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory={str(root).replace('%', '%%')}
ExecStart={command}
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
'''


def service(config: Path, action: str) -> None:
    unit = xdg('XDG_CONFIG_HOME', '.config') / 'systemd/user/ginput-watch.service'
    if action == 'disable':
        subprocess.run(['systemctl', '--user', 'disable', '--now', unit.name], check=True)
        return
    text = service_unit(Path(__file__).resolve().parents[1], config)
    atomic_write(unit, text.encode())
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', unit.name], check=True)
    print('已启用词库监视：journalctl --user -u ginput-watch -f')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='ginput', description='Guitar Input · 个人拼音与本地词库')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--config', type=Path, default=config_path())
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='创建本机配置与个人词库，不修改输入法设置')
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
    skin = sub.add_parser('skin', help='生成或启用本机 Fcitx 皮肤')
    skin_sub = skin.add_subparsers(dest='skin_action', required=True)
    skin_build = skin_sub.add_parser('build')
    skin_build.add_argument('--output',type=Path,required=True)
    skin_build.add_argument('--artwork',type=Path)
    skin_build.add_argument('--crop',help='图片区域 x,y,width,height')
    skin_build.add_argument('--height',type=int,default=140)
    skin_activate = skin_sub.add_parser('activate')
    skin_activate.add_argument('directory',type=Path)
    args = parser.parse_args(argv)
    try:
        args.config = args.config.expanduser().resolve()
        if args.command == 'skin':
            from .skin import build, activate
            if args.skin_action == 'build':
                crop = tuple(map(int,args.crop.split(','))) if args.crop else None
                if crop and len(crop)!=4: raise InputError('crop 需要 x,y,width,height 四个数字。')
                build(args.output.expanduser().resolve(), args.artwork.expanduser().resolve() if args.artwork else None, crop, args.height)
                print(f'皮肤已生成：{args.output}')
            else:
                backup = activate(args.directory.expanduser().resolve())
                print(f'皮肤配置已启用；备份：{backup}。输入完成后重载 Fcitx 生效。')
            return 0
        if args.command == 'init':
            config = initialize(args.config)
            print(f"配置：{args.config}\n个人词库：{config['words']}\n尚未修改系统输入法；下一步运行 doctor。")
            return 0
        config = load_config(args.config)
        if args.command == 'validate':
            _, words = read_words(config)
            print(f'格式检查通过：{len(words)} 条个人词条。')
        elif args.command == 'edit':
            subprocess.run(['xdg-open', config['words']], check=True)
        elif args.command == 'status':
            print(json.dumps({'config': str(args.config), 'words': config['words'],
                              'reload': config['reload'], 'last_sync': read_status(config)}, ensure_ascii=False, indent=2))
        elif args.command == 'doctor':
            checks = {config['deployer']: bool(shutil.which(config['deployer'])),
                      'pinyin_simp.dict.yaml': (Path(config['shared_data']) / 'pinyin_simp.dict.yaml').is_file(),
                      'default.yaml': (Path(config['shared_data']) / 'default.yaml').is_file()}
            if config['reload'] == 'fcitx4':
                checks['fcitx-remote'] = bool(shutil.which('fcitx-remote'))
                checks['fcitx-rime addon'] = Path('/usr/share/fcitx/addon/fcitx-rime.conf').is_file()
            for name, ok in checks.items():
                print(f"{'OK' if ok else 'MISSING'}  {name}")
            return 0 if all(checks.values()) else 1
        elif args.command == 'build':
            output = args.output.expanduser().resolve()
            if output.exists() and any(output.iterdir()):
                raise InputError('编译输出目录必须为空，避免覆盖其他文件。')
            data, words = read_words(config)
            compile_dictionary(config, words, fingerprint(config, data), output)
            print(f'编译验证通过：{output}')
        elif args.command == 'sync':
            if args.no_reload:
                config['reload'] = 'none'
            result = sync(config, args.force)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'watch':
            watch(args.config)
        elif args.command == 'service':
            service(args.config, args.action)
    except Pending as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (InputError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'ginput: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
