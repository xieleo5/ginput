"""Fcitx process and systemd integration."""
from pathlib import Path
import subprocess
import sys

from ...errors import InputError, Pending


def ensure_inactive():
    try:
        result = subprocess.run(["fcitx-remote"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Pending("未连接到 Fcitx 4；稍后自动重试。") from exc
    if result.returncode or result.stdout.strip() not in ("0", "1", "2"):
        raise Pending("未连接到 Fcitx 4；稍后自动重试。")
    if result.stdout.strip() == "2":
        raise Pending("词库已保存；切到英文/关闭输入法后会自动更新，以免中断正在输入的内容。")


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
    from ...core import atomic_write, xdg
    unit = xdg('XDG_CONFIG_HOME', '.config') / 'systemd/user/ginput-watch.service'
    if action == 'disable':
        subprocess.run(['systemctl', '--user', 'disable', '--now', unit.name], check=True)
        return
    text = service_unit(Path(__file__).resolve().parents[3], config)
    atomic_write(unit, text.encode())
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', unit.name], check=True)
    print('已启用词库监视：journalctl --user -u ginput-watch -f')
