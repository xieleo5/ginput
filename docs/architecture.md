# 模块边界与后续平台

0.6.0 继续使用轻量 Python 包，不引入复杂多语言工作区。原有 `python3 -m ginput`、`bin/ginput` 和 `native/panel.c` 编译入口保持兼容；0.5 配置缺少 `platform` 字段时仍解释为 Linux，避免迁移时切错真实词库。

```text
ginput/
  dictionary.py           # UTF-8 TSV / Word / 词典索引，不依赖桌面
  errors.py               # 共用错误类型
  rime/                   # 共用 schema、纠错与候选排序 Lua
  core.py                 # 配置、渲染、Linux 编译、锁/事务发布、兼容 API
  cli.py                  # 公共命令路由
  platforms/
    __init__.py           # 主机与目录选择
    linux/                # Fcitx、systemd、GTK、皮肤与 X11 学习
    macos.py              # Squirrel 源码发布、安装保护、Mac 路径检查
platforms/
  linux/native/           # Fcitx 4 C 插件与许可
  macos/                  # 安装入口、可选鼠须管 UI 设置样例
  ios/                    # 设计与验收边界，无虚假的占位 App
native/                   # 旧编译路径兼容入口
licenses/                 # 分发词库所需许可与归属
tests/                    # 公共、平台回归与 fixtures/ 格式契约
```

`ginput.learning`、`ginput.panel_ui`、`ginput.skin` 保留兼容导入，实际实现已归入 Linux。Linux 安装器复制独立可运行的实际模块，不复制兼容壳。没有强制用户搬动已有数据目录。

## 复用什么

- **输入语义**：Rime schema 和 Lua 保持一份。新增平台不能悄悄改变合法拼写优先、最多一次整串纠错和词频只打破同层平局的规则。
- **词条交换格式 v1**：UTF-8，无声调小写拼音，音节用空格；`词语<Tab>拼音<Tab>整数权重`，权重 1–1000000。同词异音可并存，同词同音重复拒绝，`ü/u:` 归一化为 `v`，允许空词库与 `#` 注释。参见 `ginput/examples/words.example.tsv` 和 `tests/fixtures/dictionary-v1.json`。
- **契约测试**：用跨语言可读的 JSON 固定有效与无效词条，未来 Swift 导入器对同一数据运行测试，不复制一套含糊的拼音规则。
- **隐私边界**：交换词条不等于同步原始输入记录。`ginput.userdb`、Linux 学习 SQLite 和临时窗口上下文不是跨平台交换格式。

## 不复用什么

Python 命令行不是 iOS 运行核心；它是桌面构建/管理工具。GTK、X11、Fcitx、systemd 不能带入 Squirrel 或 iOS。Mac 使用鼠须管原生候选框；共用 Lua 在 Mac 禁用 Linux 窗口上下文文件读取。Linux 特有能力仍有回归测试。

跨设备同步暂不实现，未来若需要应单独确认传输渠道、数据范围、冲突策略与备份。`sync` 目前始终指本机词库发布。

## 构建与测试

- Linux 全套：`/usr/bin/python3 -m unittest discover -s tests -v`；依赖齐全时设 `GINPUT_REQUIRE_RIME=1 GINPUT_REQUIRE_PANEL=1 GINPUT_REQUIRE_UI=1`。
- 无桌面依赖的 Mac 适配测试：`python3 -m unittest discover -s tests -p 'test_macos.py' -v`。
- Mac 源码产物用 Linux librime 临时部署并验证词条、纠错和 ASCII 标点。这是共用引擎检查，不能代替 Squirrel 实机测试。
- CI 增加 macOS Python 单元与打包脚本检查，不会在 CI 修改实际输入源。原 Linux 完整回归仍保留。

## 后续顺序

1. 在真实 Mac 检查首次安装、已有 Rime 配置、重复安装、编辑词库、重新部署与备份恢复。
2. 再决定 iOS 使用可导入 Rime 方案的现有宿主，还是开发自有 Keyboard Extension。
3. 若开发自有 iOS 键盘，先完成设备上的最小输入闭环、资源与内存预算，再考虑词库交换/同步；详见 `platforms/ios/README.md`。
