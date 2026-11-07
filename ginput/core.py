"""Validate, compile and publish only Guitar Input's own Rime files."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata

PACKAGE = Path(__file__).resolve().parent
ARTIFACTS = ("ginput.schema.yaml", "ginput.prism.bin", "ginput.table.bin", "ginput.reverse.bin")
SOURCES = ("ginput.schema.yaml", "ginput.dict.yaml", "ginput_personal.dict.yaml")
PINYIN = re.compile(r"[a-z]+(?: [a-z]+)*\Z")


class InputError(Exception):
    """A user-actionable validation or deployment failure."""


class Pending(InputError):
    """Wait for the input method to become inactive before replacing files."""


@dataclass(frozen=True)
class Word:
    text: str
    pinyin: str
    weight: int


def parse_words(data: str) -> list[Word]:
    words = []
    seen = set()
    for number, line in enumerate(data.lstrip('\ufeff').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        cols = line.split('\t')
        if len(cols) != 3:
            raise InputError(f"第 {number} 行：需要三列，用 Tab 分隔。")
        text, pinyin, weight = (c.strip() for c in cols)
        if not text or len(text) > 128 or any(unicodedata.category(c).startswith('C') for c in text):
            raise InputError(f"第 {number} 行：词语须为 1–128 个字符，不能含控制字符。")
        pinyin = ' '.join(pinyin.lower().replace('ü', 'v').replace('u:', 'v').split())
        if not PINYIN.fullmatch(pinyin) or len(pinyin) > 512:
            raise InputError(f"第 {number} 行：使用无声调拼音，音节用空格分隔，ü 可写 v。")
        if not weight.isascii() or not weight.isdigit() or not 1 <= int(weight) <= 1_000_000:
            raise InputError(f"第 {number} 行：权重须为 1–1000000 的整数。")
        key = (text, pinyin)
        if key in seen:
            raise InputError(f"第 {number} 行：词语和拼音与前面的条目重复。")
        seen.add(key)
        words.append(Word(text, pinyin, int(weight)))
    return words


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


def config_path() -> Path:
    return xdg('XDG_CONFIG_HOME', '.config') / 'ginput/config.json'


def default_config() -> dict:
    return {
        'version': 1,
        'words': str(xdg('XDG_DATA_HOME', '.local/share') / 'ginput/words.tsv'),
        'rime_dir': str(xdg('XDG_CONFIG_HOME', '.config') / 'fcitx/rime'),
        'shared_data': '/usr/share/rime-data',
        'state_dir': str(xdg('XDG_STATE_HOME', '.local/state') / 'ginput'),
        'deployer': 'rime_deployer',
        'reload': 'fcitx4',
    }


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise InputError(f"不能读取配置 {path}；先运行 ginput init。") from exc
    required = set(default_config())
    if not isinstance(config, dict) or not required <= config.keys() or config['version'] != 1:
        raise InputError('配置格式不正确或版本不支持。')
    for key in ('words', 'rime_dir', 'shared_data', 'state_dir'):
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
            raise InputError(f'配置 {key} 必须为绝对路径。')
    if config['reload'] not in ('none', 'fcitx4'):
        raise InputError('reload 仅支持 none 或 fcitx4。')
    if not isinstance(config['deployer'], str) or not config['deployer']:
        raise InputError('deployer 必须为程序名或可执行文件路径。')
    return config


def initialize(path: Path) -> dict:
    if path.exists():
        return load_config(path)
    config = default_config()
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
    data = Path(config['words']).read_bytes()
    if len(data) > 10 * 1024 * 1024:
        raise InputError('词库超过 10 MiB；请拆分或精简后重试。')
    try:
        return data, parse_words(data.decode('utf-8-sig'))
    except UnicodeDecodeError as exc:
        raise InputError('词库必须使用 UTF-8 编码。') from exc


def fingerprint(config: dict, data: bytes) -> str:
    digest = hashlib.sha256()
    for item in (data, (PACKAGE / 'rime/ginput.schema.yaml').read_bytes(), json.dumps(config, sort_keys=True).encode()):
        digest.update(item)
        digest.update(b'\0')
    return digest.hexdigest()


def render_sources(words: list[Word], revision: str) -> dict[str, bytes]:
    personal = '\n'.join(f'{w.text}\t{w.pinyin}\t{w.weight}' for w in words)
    return {
        'ginput.schema.yaml': (PACKAGE / 'rime/ginput.schema.yaml').read_bytes(),
        'ginput.dict.yaml': (
            f'---\nname: ginput\nversion: "{revision}"\nsort: by_weight\n'
            'use_preset_vocabulary: false\nimport_tables:\n  - pinyin_simp\n  - ginput_personal\n...\n'
        ).encode(),
        'ginput_personal.dict.yaml': (
            f'---\nname: ginput_personal\nversion: "{revision}"\nsort: by_weight\n...\n{personal}\n'
        ).encode(),
    }


def compile_dictionary(config: dict, words: list[Word], revision: str, stage: Path) -> None:
    shared = Path(config['shared_data'])
    for name in ('default.yaml', 'pinyin_simp.dict.yaml', 'punctuation.yaml', 'key_bindings.yaml'):
        if not (shared / name).is_file():
            raise InputError(f'缺少 Rime 数据：{shared / name}。请安装 rime-data-pinyin-simp 和 rime-prelude。')
    if not shutil.which(config['deployer']):
        raise InputError('找不到 rime_deployer；请安装 librime-bin。')
    stage.mkdir(parents=True, exist_ok=True)
    for name, content in render_sources(words, revision).items():
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
    if config['reload'] == 'none':
        return
    try:
        result = subprocess.run(['fcitx-remote'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Pending('未连接到 Fcitx 4；稍后自动重试。') from exc
    if result.returncode or result.stdout.strip() not in ('0', '1', '2'):
        raise Pending('未连接到 Fcitx 4；稍后自动重试。')
    if result.stdout.strip() == '2':
        raise Pending('词库已保存；切到英文/关闭输入法后会自动更新，以免中断正在输入的内容。')


def publish(stage: Path, target: Path, state: Path) -> None:
    """Keep a recovery journal before replacing each of our seven managed files."""
    files = list(SOURCES) + [f'build/{name}' for name in ARTIFACTS]
    # Store bytes as hex: no unsafe paths, pickle, or arbitrary backup-file reads.
    previous = {}
    for name in files:
        path = target / name
        if path.is_symlink():
            raise InputError(f'拒绝覆盖符号链接：{path}')
        previous[name] = path.read_bytes().hex() if path.exists() else None
    journal = {'target': str(target), 'files': previous}
    write_json(state / 'pending-transaction.json', journal)
    try:
        for name in files:
            atomic_write(target / name, (stage / name).read_bytes())
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
    if journal['target'] != str(target) or set(journal['files']) != expected:
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
            if Path(config['words']).read_bytes() != data:
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
    debounce = Debounce()
    last_done, last_message = None, None
    retry_at = 0.0
    print('Guitar Input 正在监视本地词库。Ctrl+C 停止。', flush=True)
    while True:
        now = time.monotonic()
        try:
            config = load_config(path)
            data = Path(config['words']).read_bytes()
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
