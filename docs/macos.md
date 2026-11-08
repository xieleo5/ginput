# Guitar Input 0.6.0 · macOS / 鼠须管配置版

这是 **Squirrel（鼠须管）+ Guitar Input 方案、词库和 Lua** 的源码配置包，不是独立 `.app`，不需要你申请 Apple 开发者帐号，也不要求关闭 Gatekeeper 或系统安全功能。鼠须管本身请使用官方发行版。

**验证边界：** 本版在 Linux 上制作并用真实 librime 引擎验证公共输入逻辑与 Mac 源码部署流程。尚未在 macOS 上实际安装、切换输入源和输入中文，不能视为已完成 Mac 实机验收。包内没有 Linux 编译出的 `.bin` 文件，最终编译由 Mac 上的鼠须管完成。

## 需要什么

- 官方当前 Squirrel 发行版。上游当前 README 要求 macOS 13.0+；老 macOS 请另核对适配版本。本项目尚无实测兼容矩阵。
- Python 3.10+（只用标准库，没有 pip 依赖）。macOS 不保证自带合适 Python。
- 鼠须管官方发行版自带 Lua 支持与 prelude 数据；不需要另外安装系统 Lua。
- 配置包内 `data/pinyin_simp.dict.yaml` 已附带基础简体词典和 Apache-2.0 许可。不会假定鼠须管已经安装这份字典。

官方来源：[Squirrel 下载与要求](https://github.com/rime/squirrel)、[Python macOS 下载](https://www.python.org/downloads/macos/)、[Lua 支持变更记录](https://github.com/rime/squirrel/blob/master/CHANGELOG.md)。安装脚本不会下载或安装这些软件。

## 首次安装

1. 安装官方鼠须管，按提示注销/登录，在系统输入源设置里添加鼠须管。
2. 解压 `ginput-macos-0.6.0.zip`。保留解压目录，后续词库管理命令在这个目录执行。
3. 在终端进入解压目录，运行：

   ```bash
   bash install.command
   ```

   或双击 `install.command`。如果系统拦截，请从终端运行上面的命令，不必关闭系统安全设置。

4. 安装器会保留已有 `words.tsv` 和已有输入习惯，不读取任何文档或剪贴板。首次没有 `default.custom.yaml` 时，它新建追加方案的补丁；**已有文件时完全不改它**。此时打开 `~/Library/Rime/default.custom.yaml`，在已有 `patch` 下添加 Guitar Input，勿重复写第二个 `patch`：

   ```yaml
   patch:
     "schema_list/+":
       - schema: ginput
   ```

   如果已有 `schema_list/+`，就在它的列表末尾加一项；如果已有显式 `schema_list`，直接在其原列表里加入 `- schema: ginput`。保留其他方案和配置，勿覆盖整个文件。

5. 完成当前输入后，从鼠须管菜单选择 **重新部署**。再从 Rime 方案菜单选 **Guitar Input · 拼音**。
6. 在普通文本编辑器依次测试 `nihao`、`nh`、`vyan`、`nihoa`，应能选择“你好”“你好”“语言”“你好”。再检查选词、空格、退格、翻页、英文标点以及重启后的学习记录。候选可能因个人学习改变次序。

安装命令成功只表示配置文件写入，**不代表鼠须管已部署成功或已在应用中生效**。

## 修改词库

在解压目录执行：

```bash
python3 -m ginput doctor
python3 -m ginput edit
python3 -m ginput validate
python3 -m ginput sync
```

`edit` 用 macOS 的 `open` 打开本机 `words.tsv`；保存为 UTF-8 纯文本，三列用 Tab 分隔，例如 `示例输入法<Tab>shi li shu ru fa<Tab>10000`，其中 `<Tab>` 代表真实 Tab。`sync` 只是把本机词库生成到本机 Rime 目录，**不是跨设备同步，也不会联网**。完成后仍要手动「重新部署」。`status` 的 `deployment_required` 表示此步骤尚待用户完成；没有确认接口时不会冒报成功。

本版不提供 Mac 自动重部署或 launchd 服务，避免猜测当前是否正在输入。`watch`、`service`、Linux 皮肤和原始输入历史功能在 Mac 上会明确提示不支持，而不会调用 `fcitx-remote`、`systemctl` 或 GTK。Rime 自身的选词学习仍由鼠须管提供。

## 文件位置与保护

- 管理配置、手工词库：`~/Library/Application Support/GuitarInput/config.json`、`words.tsv`。
- 基础词库：同目录下 `resources/pinyin_simp.dict.yaml`。
- 输入方案源码：`~/Library/Rime/` 下的 `ginput*` 文件、`lua/ginput.lua`。
- 公共 Lua 入口：在 `rime.lua` 开头维护带 `GINPUT MANAGED LOADER` 标记的一段，保留其他代码和末尾的 `return`。标记不完整会拒绝写入。
- 备份：管理目录的 `state/backups/<时间>/`，包含被覆盖的 Guitar Input 文件与 `rime.lua`；`manifest.json` 标明原来存在和不存在的文件。需要恢复时，先停止输入，按其中相对路径把备份复制回 Rime 目录，并重新部署；如之后手动修改了 `rime.lua`，应只恢复其中的项目段，不要覆盖新改动。
- 不改 `ginput.userdb`，不改其他输入方案，不改 `squirrel.custom.yaml`，不改 Squirrel 应用目录。中途文件发布失败会用事务记录回退；下一次 `sync` 也会先处理未完成的事务。

想停用时，从方案列表移除 `ginput` 并重新部署即可。不要顺手删除整个 `~/Library/Rime`。词库、备份、Rime 学习数据库均留在本机，没有 iCloud、Git、WebDAV 等跨设备同步服务。

## 能力范围

| 功能 | 此版本 |
| --- | --- |
| 全拼、简拼、v 代 yu、有限拼写纠错、ASCII 标点 | 共用方案，Linux librime 回归覆盖，Mac 实机待验收 |
| 本地手工词库 + Rime 选词学习 | 已接入文件流程，Mac 实机待验收 |
| 候选框 | 使用鼠须管原生候选框 |
| Linux GTK 五列展开面板、透明图片皮肤、X11 上下文、原始输入历史 | 不移植，不启用 |
| 跨设备词库同步 | 未实现，未选择传输服务 |
| iOS 键盘 | 只有架构边界和计划，没有可安装 App/Extension |

`squirrel.custom.example.yaml` 只是可选示例，不自动安装；把需要的字段合并到现有设置后，会影响所有 Squirrel 方案。

## 开发者打包

在仓库根目录，使用已安装的 `pinyin_simp` 源码字典：

```bash
python3 tools/package_macos.py --output dist/ginput-macos-0.6.0.zip
# 非默认来源可显式指定：--base-dictionary /path/to/pinyin_simp.dict.yaml
```

构建工具只打包仓库代码、示例词条与指定基础字典，从不读取当前用户的词库、输入历史或 `userdb`。manifest 记录文件 SHA-256，包括基础词典来源与实际内容哈希。输出同名文件已存在会拒绝覆盖。测试和生产配置包均不携带编译产物。

设计依据：[文件位置](https://github.com/rime/home/wiki/RimeWithSchemata)、[追加方案与重新部署](https://github.com/rime/home/wiki/CustomizationGuide)、[基础词库许可](https://github.com/rime/rime-pinyin-simp/blob/master/AUTHORS)。
