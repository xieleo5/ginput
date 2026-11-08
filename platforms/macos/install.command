#!/bin/bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo '此安装入口只能在 macOS 使用。' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  echo '需要 Python 3.10+。请先从 https://www.python.org/downloads/macos/ 安装，再运行此文件。' >&2
  exit 1
fi
cd "$HERE"
python3 -m ginput macos install --base-dictionary "$HERE/data/pinyin_simp.dict.yaml"
echo '完成当前输入后，请从鼠须管菜单选择「重新部署」。已有 default.custom.yaml 的用户须按 README 加入方案。'
