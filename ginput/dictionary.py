"""Portable UTF-8 vocabulary contract. No desktop APIs or user filesystem access."""
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from .errors import InputError

PINYIN = re.compile(r"[a-z]+(?: [a-z]+)*\Z")


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


def lexicon(words: list[Word], base_dictionary: Path | None = None) -> bytes:
    entries = {}
    if base_dictionary:
        for line in base_dictionary.read_text().splitlines():
            parts = line.split('\t')
            if len(parts) >= 3 and PINYIN.fullmatch(parts[1]) and parts[2].isdigit():
                entries[parts[0], parts[1]] = int(parts[2])
    for word in words:
        entries[word.text, word.pinyin] = max(word.weight, entries.get((word.text, word.pinyin), 0))
    return ''.join(f'{text}\t{code}\t{weight}\n' for (text, code), weight in sorted(entries.items())).encode()
