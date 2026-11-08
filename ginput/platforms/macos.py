"""Squirrel source deployment. Never imports Fcitx, calls sudo, or deploys live UI.

The installed Squirrel, not a Linux build, compiles these sources on macOS.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import tempfile

from ..errors import InputError, Pending
from . import host_platform

MANAGED = ('ginput.schema.yaml', 'ginput.dict.yaml', 'ginput_personal.dict.yaml',
           'lua/ginput.lua', 'ginput.lexicon.tsv', 'rime.lua', 'ginput_base.dict.yaml')
SCHEMA_PATCH = b'patch:\n  "schema_list/+":\n    - schema: ginput\n'


def base_source(path: Path) -> bytes:
    """Namespace the Apache-2.0 base table to avoid other installed schemas."""
    if not path.is_file():
        raise InputError(f'缺少基础词库：{path}。请使用 macOS 配置包中的 data/pinyin_simp.dict.yaml。')
    text = path.read_text(encoding='utf-8-sig')
    if not re.search(r'^\.\.\.\s*$', text, re.M):
        raise InputError('基础词库不是完整的 Rime 字典。')
    text, count = re.subn(r'^name: (?:pinyin_simp|ginput_base)\s*$', 'name: ginput_base', text, flags=re.M)
    if count != 1:
        raise InputError('基础词库必须是 pinyin_simp 或 ginput_base。')
    return ('# Guitar Input modification: table renamed to ginput_base; entries unchanged.\n' + text).encode('utf-8')


def safe_paths(target: Path, names=MANAGED):
    """Reject file and intermediate directory symlinks inside the target."""
    if target.is_symlink():
        raise InputError(f'拒绝写入符号链接目录：{target}')
    for name in names:
        path = target / name
        while path != target:
            if path.is_symlink(): raise InputError(f'拒绝覆盖符号链接：{path}')
            path = path.parent


def backup_sources(target: Path, state: Path) -> Path:
    from ..core import atomic_write, write_json
    folder = state / 'backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    # Includes no userdb, other input method files, or raw input history.
    present = []
    for name in MANAGED:
        path = target / name
        if path.exists():
            atomic_write(folder / name, path.read_bytes())
            present.append(name)
    write_json(folder / 'manifest.json', {'rime_dir': str(target), 'files': present,
               'absent': sorted(set(MANAGED) - set(present))})
    return folder


def sync_sources(config: dict, force=False) -> dict:
    from .. import core
    target, state = Path(config['rime_dir']), Path(config['state_dir'])
    base = Path(config['base_dictionary'])
    with core.locked(state):
        data, words = core.read_words(config)
        raw_base = base.read_bytes() if base.is_file() else b''
        normalized = base_source(base)
        revision = core.fingerprint(config, data + b'\0BASE\0' + raw_base)
        previous = core.read_status(config)
        safe_paths(target)
        if not force and not (state / 'pending-transaction.json').exists() and previous.get('revision') == revision and all((target / name).is_file() for name in MANAGED):
            return previous
        core.recover(target, state)
        with tempfile.TemporaryDirectory(prefix='squirrel-', dir=state) as folder:
            stage = Path(folder)
            sources = core.render_sources(words, revision, base, base_table='ginput_base', context=False)
            sources['ginput_base.dict.yaml'] = normalized
            for name, content in sources.items(): core.atomic_write(stage / name, content)
            if core.read_words(config)[0] != data or base.read_bytes() != raw_base:
                raise Pending('生成期间词库改变，请重新运行 sync。')
            backup = backup_sources(target, state)
            core.publish(stage, target, state, source_only=True)
        result = {'status': 'deployment_required', 'revision': revision, 'entries': len(words),
                  'updated_at': datetime.now(timezone.utc).isoformat(), 'rime_dir': str(target),
                  'backup': str(backup), 'next_step': '完成当前输入后，从鼠须管菜单选择「重新部署」，再选择 Guitar Input · 拼音。'}
        core.write_json(state / 'status.json', result)
        return result


def install(path: Path, base_dictionary: Path) -> dict:
    """Explicit user-invoked installation; retains existing manual word source."""
    from .. import core
    if host_platform() != 'macos':
        raise InputError('安装只能在 macOS 运行；可在 Linux 打包和隔离测试。')
    base_source(base_dictionary)  # Validate before touching user directories.
    if path.exists() and core.load_config(path).get('platform') != 'macos':
        raise InputError('此配置是 Linux 配置，不能用于安装鼠须管版本。')
    config = core.initialize(path, 'macos')
    destination = Path(config['base_dictionary'])
    if destination.is_symlink(): raise InputError(f'拒绝覆盖符号链接：{destination}')
    core.atomic_write(destination, base_dictionary.read_bytes())
    result = sync_sources(config, force=True)
    custom = Path(config['rime_dir']) / 'default.custom.yaml'
    # No YAML rewriting: existing settings are always preserved byte-for-byte.
    try:
        with custom.open('xb') as stream: stream.write(SCHEMA_PATCH)
        result['schema_list'] = 'created_append_patch'
    except FileExistsError:
        result['schema_list'] = 'existing_file_unchanged'
        result['schema_note'] = '请在现有 default.custom.yaml 的 patch/schema_list 或 schema_list/+ 列表中加入 schema: ginput；保留其他设置。'
    result['config'] = str(path)
    result['words'] = config['words']
    return result


def doctor(config: dict) -> dict:
    base = Path(config['base_dictionary'])
    shared = Path(config['shared_data'])
    checks = {'macOS': host_platform() == 'macos',
              'Squirrel shared data': shared.is_dir(),
              'pinyin_simp base dictionary': base.is_file()}
    for name in ('default.yaml', 'punctuation.yaml', 'key_bindings.yaml'):
        checks[name] = (shared / name).is_file()
    # No unreliable Linux .so check on macOS. Actual Lua behavior needs deploy + typing.
    return checks
