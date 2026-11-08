"""Validate, compile and publish only Guitar Input's own Rime files."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

PACKAGE = Path(__file__).resolve().parent
ARTIFACTS = ("ginput.schema.yaml", "ginput.prism.bin", "ginput.table.bin", "ginput.reverse.bin")
SOURCES = ("ginput.schema.yaml", "ginput.dict.yaml", "ginput_personal.dict.yaml",
           "lua/ginput.lua", "ginput.lexicon.tsv", "rime.lua")
LOADER_BEGIN = '-- BEGIN GINPUT MANAGED LOADER'
LOADER_END = '-- END GINPUT MANAGED LOADER'
from .dictionary import Word, parse_words, lexicon
from .errors import InputError, Pending
from .platforms import host_platform, paths_for


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, obj: dict) -> None:
    atomic_write(path, (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode())


def xdg(name: str, fallback: str) -> Path:
    return Path(os.environ.get(name, str(Path.home() / fallback))).expanduser().resolve()


def config_path(platform: str | None = None) -> Path:
    return paths_for(platform)['config']


def default_config(platform: str | None = None) -> dict:
    platform = platform or host_platform()
    paths = paths_for(platform)
    config = {
        'version': 1,
        'platform': platform,
        'words': str(paths['words']),
        'rime_dir': str(paths['rime']),
        'shared_data': str(paths['shared']),
        'state_dir': str(paths['state']),
        'deployer': 'rime_deployer',
        'reload': 'fcitx4' if platform == 'linux' else 'manual',
    }
    if platform == 'macos':
        config['base_dictionary'] = str(paths['base'])
    return config


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise InputError(f"不能读取配置 {path}；先运行 ginput init。") from exc
    required = {'version', 'words', 'rime_dir', 'shared_data', 'state_dir', 'deployer', 'reload'}
    if not isinstance(config, dict) or not required <= config.keys() or config['version'] != 1:
        raise InputError('配置格式不正确或版本不支持。')
    for key in ('words', 'rime_dir', 'shared_data', 'state_dir'):
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
            raise InputError(f'配置 {key} 必须为绝对路径。')
    config.setdefault('platform', 'linux')  # v0.5 configs remain Linux configs.
    if config['platform'] not in ('linux', 'macos'):
        raise InputError('platform 仅支持 linux 或 macos。')
    allowed = ('none', 'fcitx4') if config['platform'] == 'linux' else ('manual',)
    if config['reload'] not in allowed:
        raise InputError('当前平台不支持此 reload 设置；macOS 使用 manual。')
    if config['platform'] == 'macos':
        base = config.get('base_dictionary')
        if not isinstance(base, str) or not Path(base).is_absolute():
            raise InputError('macOS 配置 base_dictionary 必须为绝对路径。')
    if not isinstance(config['deployer'], str) or not config['deployer']:
        raise InputError('deployer 必须为程序名或可执行文件路径。')
    return config


def initialize(path: Path, platform: str | None = None) -> dict:
    if path.exists():
        return load_config(path)
    config = default_config(platform)
    words = Path(config['words'])
    words.parent.mkdir(parents=True, exist_ok=True)
    if not words.exists():
        # O_EXCL also protects an existing dictionary from concurrent initialization.
        try:
            with words.open('xb') as stream:
                os.chmod(words, 0o600)
                stream.write((PACKAGE / 'examples/words.example.tsv').read_bytes())
        except FileExistsError:
            pass
    write_json(path, config)
    return config


def read_words(config: dict) -> tuple[bytes, list[Word]]:
    path = Path(config['words'])
    data = path.read_bytes()
    learned_path = path.with_name('learned.tsv')
    learned = learned_path.read_bytes() if learned_path.exists() else b''
    if len(data) + len(learned) > 10 * 1024 * 1024:
        raise InputError('词库超过 10 MiB；请拆分或精简后重试。')
    try:
        manual_words = parse_words(data.decode('utf-8-sig'))
        merged = {(word.text, word.pinyin): word for word in parse_words(learned.decode('utf-8-sig'))}
        merged.update({(word.text, word.pinyin): word for word in manual_words})
        return data + b'\0LEARNED\0' + learned, list(merged.values())
    except UnicodeDecodeError as exc:
        raise InputError('词库必须使用 UTF-8 编码。') from exc


def fingerprint(config: dict, data: bytes) -> str:
    digest = hashlib.sha256()
    for item in (data, (PACKAGE / 'rime/ginput.schema.yaml').read_bytes(),
                 (PACKAGE / 'rime/ginput.lua').read_bytes(), json.dumps(config, sort_keys=True).encode()):
        digest.update(item)
        digest.update(b'\0')
    return digest.hexdigest()


def merge_loader(existing: bytes, managed: bytes) -> bytes:
    text = existing.decode('utf-8')
    if LOADER_BEGIN in text or LOADER_END in text:
        if text.count(LOADER_BEGIN) != 1 or text.count(LOADER_END) != 1 or text.index(LOADER_BEGIN) > text.index(LOADER_END):
            raise InputError('rime.lua 中的 Guitar Input 配置段不完整，未修改文件。')
        start, end = text.index(LOADER_BEGIN), text.index(LOADER_END) + len(LOADER_END)
        if text[end:end+1] == '\n': end += 1
        if start == 0 and text[end:end+1] == '\n': end += 1  # our separator
        text = text[:start] + text[end:]
    # Lua requires a top-level return to remain the last statement. Prepend
    # our independent assignments so an existing return-based loader stays valid.
    return managed + (b'\n' if text else b'') + text.encode()


def render_sources(words: list[Word], revision: str, base_dictionary: Path | None = None, *, base_table: str = 'pinyin_simp', context: bool = True) -> dict[str, bytes]:
    personal = '\n'.join(f'{w.text}\t{w.pinyin}\t{w.weight}' for w in words)
    return {
        'ginput.schema.yaml': (PACKAGE / 'rime/ginput.schema.yaml').read_bytes() + (b'\nginput:\n  context_enabled: false\n' if not context else b''),
        'ginput.dict.yaml': (
            f'---\nname: ginput\nversion: "{revision}"\nsort: by_weight\n'
            f'use_preset_vocabulary: false\nimport_tables:\n  - {base_table}\n  - ginput_personal\n...\n'
        ).encode(),
        'ginput_personal.dict.yaml': (
            f'---\nname: ginput_personal\nversion: "{revision}"\nsort: by_weight\n...\n{personal}\n'
        ).encode(),
        'lua/ginput.lua': (PACKAGE / 'rime/ginput.lua').read_bytes(),
        'ginput.lexicon.tsv': lexicon(words, base_dictionary),
        'rime.lua': (LOADER_BEGIN + '\n'
                     'ginput_correction = require("ginput").translator\n'
                     'ginput_ranking = require("ginput").filter\n' + LOADER_END + '\n').encode(),
    }


def compile_dictionary(config: dict, words: list[Word], revision: str, stage: Path) -> None:
    shared = Path(config['shared_data'])
    for name in ('default.yaml', 'pinyin_simp.dict.yaml', 'punctuation.yaml', 'key_bindings.yaml'):
        if not (shared / name).is_file():
            raise InputError(f'缺少 Rime 数据：{shared / name}。请安装 rime-data-pinyin-simp 和 rime-prelude。')
    if not shutil.which(config['deployer']):
        raise InputError('找不到 rime_deployer；请安装 librime-bin。')
    stage.mkdir(parents=True, exist_ok=True)
    for name, content in render_sources(words, revision, shared / 'pinyin_simp.dict.yaml').items():
        atomic_write(stage / name, content)
    atomic_write(stage / 'default.custom.yaml', b'patch:\n  schema_list:\n    - schema: ginput\n')
    result = subprocess.run(
        [config['deployer'], '--build', str(stage), str(shared), str(stage / 'build')],
        capture_output=True, timeout=120,
    )
    atomic_write(stage / 'compile.log', result.stdout + result.stderr)
    if result.returncode or any(not (stage / 'build' / name).is_file() or not (stage / 'build' / name).stat().st_size for name in ARTIFACTS):
        raise InputError('Rime 编译失败；现有词库未改动。详情见本机状态目录 last-compile.log。')


@contextmanager
def locked(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'sync.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Pending('另一个词库更新正在进行，稍后重试。') from exc
        yield


def ensure_inactive(config: dict) -> None:
    if config['reload'] in ('none', 'manual'):
        return
    from .platforms.linux.desktop import ensure_inactive as check
    check()


def publish(stage: Path, target: Path, state: Path, *, source_only: bool = False) -> None:
    """Journal managed files; preserve unrelated code in the shared Lua loader."""
    files = list(SOURCES) + (['ginput_base.dict.yaml'] if source_only else [f'build/{name}' for name in ARTIFACTS])
    # Store bytes as hex: no unsafe paths, pickle, or arbitrary backup-file reads.
    previous = {}
    for name in files:
        path = target / name
        if path.is_symlink():
            raise InputError(f'拒绝覆盖符号链接：{path}')
        previous[name] = path.read_bytes().hex() if path.exists() else None
    loader = target / 'rime.lua'
    merged_loader = merge_loader(loader.read_bytes() if loader.exists() else b'', (stage / 'rime.lua').read_bytes())
    journal = {'target': str(target), 'files': previous}
    write_json(state / 'pending-transaction.json', journal)
    try:
        for name in files:
            atomic_write(target / name, merged_loader if name == 'rime.lua' else (stage / name).read_bytes())
    except BaseException:
        recover(target, state)
        raise
    write_json(state / 'previous.json', journal)
    (state / 'pending-transaction.json').unlink()


def recover(target: Path, state: Path) -> None:
    path = state / 'pending-transaction.json'
    if not path.exists():
        return
    journal = json.loads(path.read_text())
    expected = set(SOURCES) | {f'build/{name}' for name in ARTIFACTS}
    legacy = expected - {'lua/ginput.lua', 'ginput.lexicon.tsv', 'rime.lua'}
    macos = set(SOURCES) | {'ginput_base.dict.yaml'}
    if journal['target'] != str(target) or set(journal['files']) not in (expected, legacy, macos):
        raise InputError('未完成的更新与当前目标不一致；保留恢复记录，停止覆盖。')
    for name, data in journal['files'].items():
        dest = target / name
        if data is None:
            dest.unlink(missing_ok=True)
        else:
            atomic_write(dest, bytes.fromhex(data))
    path.unlink()


def read_status(config: dict) -> dict:
    try:
        return json.loads((Path(config['state_dir']) / 'status.json').read_text())
    except FileNotFoundError:
        return {'status': 'not_synced'}


def sync(config: dict, force: bool = False) -> dict:
    if config.get('platform') == 'macos':
        from .platforms.macos import sync_sources
        return sync_sources(config, force)
    state, target = Path(config['state_dir']), Path(config['rime_dir'])
    with locked(state):
        data, words = read_words(config)
        revision = fingerprint(config, data)
        previous = read_status(config)
        if not force and not (state / 'pending-transaction.json').exists() and previous.get('revision') == revision and previous.get('status') in ('published', 'reload_requested') and all((target / name).is_file() for name in SOURCES) and all((target / 'build' / name).is_file() for name in ARTIFACTS):
            return previous
        ensure_inactive(config)
        recover(target, state)
        with tempfile.TemporaryDirectory(prefix='compile-', dir=state) as folder:
            stage = Path(folder)
            try:
                compile_dictionary(config, words, revision, stage)
            finally:
                if (stage / 'compile.log').exists():
                    atomic_write(state / 'last-compile.log', (stage / 'compile.log').read_bytes())
            # Editor may save again or user may start typing while compilation runs.
            if read_words(config)[0] != data:
                raise Pending('编译期间词库再次改变，将重新编译最新版本。')
            ensure_inactive(config)
            publish(stage, target, state)
        result = {'status': 'published', 'revision': revision, 'entries': len(words),
                  'updated_at': datetime.now(timezone.utc).isoformat(), 'rime_dir': str(target)}
        if config['reload'] == 'fcitx4':
            # The engine releases its old mmap files and opens the compiled replacements.
            # An accepted request does not prove a GUI candidate was displayed.
            try:
                subprocess.run(['fcitx-remote', '-r'], check=True, capture_output=True, timeout=10)
                result['status'] = 'reload_requested'
            except (OSError, subprocess.SubprocessError):
                result['status'] = 'reload_pending'
        write_json(state / 'status.json', result)
        return result


class Debounce:
    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self.value = None
        self.since = 0.0

    def ready(self, value: str, now: float) -> bool:
        if value != self.value:
            self.value, self.since = value, now
            return False
        return now - self.since >= self.delay


def watch(path: Path, interval: float = 0.5) -> None:
    if load_config(path).get('platform') == 'macos':
        raise InputError('macOS 暂不自动重部署；编辑后运行 sync，再从鼠须管菜单选择重新部署。')
    debounce = Debounce()
    last_done, last_message = None, None
    retry_at = 0.0
    print('Guitar Input 正在监视本地词库。Ctrl+C 停止。', flush=True)
    while True:
        now = time.monotonic()
        try:
            config = load_config(path)
            data, _ = read_words(config)
            revision = fingerprint(config, data)
            if debounce.ready(revision, now) and revision != last_done and now >= retry_at:
                result = sync(config)
                if result['status'] == 'reload_pending':
                    raise Pending('词库已编译，重载请求失败；稍后重试。')
                last_done = revision
                message = f"已更新 {result['entries']} 条个人词条；状态：{result['status']}。"
            else:
                message = last_message
        except Pending as exc:
            retry_at = now + 3
            message = str(exc)
        except (InputError, OSError, ValueError, subprocess.SubprocessError) as exc:
            # Retry recoverable failures even if the editor hasn't changed the source.
            retry_at = now + 5
            message = f'暂未更新：{exc}'
        if message is not None and message != last_message:
            print(message, flush=True)
            last_message = message
        time.sleep(interval)
