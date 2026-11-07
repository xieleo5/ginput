# Guitar Input (ginput)

Guitar Input（ginput）是一个支持本地自定义词库的 Linux 中文输入法项目。

第一版基于 **Rime 拼音引擎 + Fcitx 4 桌面接入**，自行管理输入方案与个人词库。支持简体全拼、拼音首字母简拼、Rime 的本地选词学习，以及「编辑本地文件 → 自动编译 → 更新词库」。通用汉字和基础词语来自系统的 `pinyin_simp`，个人词库在此基础上扩充。

## 拼写支持（0.3.0）

| 输入方式 | 输入示例 | 可选结果 |
| --- | --- | --- |
| 首字母简拼 | `nh`、`zg` | 你好、中国 |
| `v` 代替 `yu` | `v`、`van`、`ve`、`vn`、`vyan` | 雨、原、月、云、语言 |
| 双字母声母 | `zhg` | 中国 |
| 简拼与全拼混用 | `nih`、`nhao` | 你好 |
| 个人词条简拼 | `slsrf`、`slshrf` | 示例输入法 |
| 常见音节内换序 | `nihoa`、`zhognguo`、`beijign` | 你好、中国、北京 |
| 常见音节内换序 | `shagnhai`、`tainkong` | 上海、天空 |
| 同排邻键误按 | `nihso`、`nihap` | 你好 |

这些示例通过实际 Rime 选词上屏测试，确认会消化完整输入。简拼存在重码，例如 `zg` 也可表示“这个”，不保证目标词永远第一。当前容错是有限规则与 Rime 的邻键搜索；**不保证任意错别字、漏字母、多字母或跨音节乱序**。

`v` 可以代替 `yu` 开头的音节，`nv` / `lv` 仍表示 nü / lü。此规则用于键盘输入；词库文件中仍填写标准拼音 `yu` / `yuan` / `yue` / `yun`。

换序规则保留原始拼音，新增候选可能改变次要候选的排序。单独的 `n`/`m` 用于简拼，不作为“嗯/呣”的特殊全拼。按个人习惯选词后的排序仍由 Rime 学习。

候选框基于 Fcitx 4 的原生界面，提供可选的下拉选词插件和本机皮肤生成工具。尚无图形词库编辑器、完整 SSF 导入器或 Fcitx 5 接入；不提供搜狗的云词库和预测服务。

## 本机词库

Python 3.10+，词库管理工具只用标准库；可选皮肤渲染需要 `python3-cairo`。无需 pip 安装即可使用：

```bash
python3 -m ginput init
python3 -m ginput doctor
python3 -m ginput edit
```

默认个人词库为 `~/.local/share/ginput/words.tsv`，配置为 `~/.config/ginput/config.json`。尊重 `XDG_DATA_HOME` / `XDG_CONFIG_HOME` / `XDG_STATE_HOME`。

词库使用 UTF-8，三列用 **Tab** 分隔，拼音音节用空格分隔，ü 写作 v：

```text
# 词语<Tab>拼音<Tab>权重
示例输入法	shi li shu ru fa	10000
示例短语	shi li duan yu	10000
```

`<Tab>` 表示真实 Tab 键，不能输入字面文本 `<Tab>`。每行一个词，权重范围 1–1000000；同词不同读音可以共存。同词同读音重复、列数错误或非法权重会拒绝更新。格式验证不判断拼音读音是否正确。较高权重提高静态词条权重，实际排序还受上下文和学习记录影响。

词库保存在仓库外；仓库只包含示例。同步程序只访问本地文件与本机 Fcitx，不上传词语、不从文档抓取内容。

## 接入 Fcitx 4

Ubuntu 22.04 的依赖如下。先查看模拟结果，确认没有删除现有输入法：

```bash
apt-get -s install fcitx-rime librime-bin rime-data-pinyin-simp rime-prelude
# 需要安装时，使用系统密码弹窗：
pkexec --disable-internal-agent /usr/bin/apt-get install fcitx-rime librime-bin rime-data-pinyin-simp rime-prelude
```

编译并发布本项目的文件：

```bash
python3 -m ginput validate
python3 -m ginput sync --no-reload
```

首次启用需要在 `~/.config/fcitx/rime/default.custom.yaml` 的现有 `patch/schema_list` 中加入 `schema: ginput`。如果文件不存在，可创建：

```yaml
patch:
  schema_list:
    - schema: ginput
    - schema: pinyin_simp
```

已有文件时保留原有方案和其他设置。安装 Fcitx Rime 插件后，可能需要重新登录一次才能加载新插件。在 Fcitx 配置中添加 **Rime**，通过 Rime 菜单执行一次「部署」，然后从方案菜单（通常为 Ctrl+`）选择 **Guitar Input · 拼音**。GNOME 的「输入源」列表不一定显示 Fcitx 方案。

## 自动更新

```bash
python3 -m ginput watch
```

保存词库后，监视器会等待内容稳定约 1 秒，再校验和编译。Fcitx 处于中文启用状态时会等待；切到 Fcitx 的英文/关闭状态后再发布并请求重载。**这是保守的第一版行为，不是在正在输入的候选框里强行刷新。** Rime 内部的 Shift 英文模式不一定改变 Fcitx 状态，可用 Fcitx 的切换键（通常 Ctrl+Space）。

需要登录后自动监视时：

```bash
python3 -m ginput service enable
journalctl --user -u ginput-watch -f
python3 -m ginput status
# 停止自动监视：
python3 -m ginput service disable
```

服务直接引用当前项目目录，移动或删除项目后要重新启用。`published` 表示文件已发布；`reload_requested` 表示已发出重载请求，不能据此断言每个应用的候选框已经验证。`reload_pending` 表示文件已发布，但重载失败，监视器会重试。

编译先在临时目录完成，成功后只替换 ginput 的方案、字典和四个编译文件。失败保留上一版，写入中途故障通过恢复记录回退。程序不写 Rime 的 `ginput.userdb`，因此保留输入习惯学习；删除静态词条不会删除已经学过的词。单次失败详情位于 `~/.local/state/ginput/last-compile.log`，该文件可能包含词库内容，留在本机。

## 下拉选词与皮肤

可选下拉插件需要 C 编译器和 `fcitx-libs-dev` 头文件，安装仅写入当前用户目录：

```bash
python3 tools/install_panel.py
# 头文件也可通过 --include-dir /path/to/include 指定。
```

完成当前输入后重启 Fcitx，插件便会加载。有候选词时按 **↓** 展开竖排列表，再按 **↑ / ↓** 移动选择，**Enter** 上屏高亮候选，**Esc** 收起且保留拼音。数字键和翻页键继续可用，每页五个候选。不在输入时保留方向键原有行为。插件目前作用于 Fcitx Rime；其他输入法不受影响。

生成和启用皮肤：

```bash
python3 -m ginput skin build --output /tmp/ginput-theme
python3 -m ginput skin activate /tmp/ginput-theme
```

输出目录必须为空。添加本机 PNG 装饰图时，使用：

```bash
python3 -m ginput skin build --output /tmp/ginput-art-theme \
  --artwork /path/to/artwork.png --height 140
# 可选：--crop X,Y,WIDTH,HEIGHT，坐标以原图像素计算。
python3 -m ginput skin activate /tmp/ginput-art-theme
```

图片等比缩放，放置在文字面板右侧，不参与拼音与候选行的间距计算。启用后，完成当前输入再重启 Fcitx；皮肤使用 Fcitx 4 classic UI，会影响共享这个界面的其他输入法。原配置及已有同名皮肤自动备份在 `~/.local/state/ginput/skin-backups/`。

个人图片、生成皮肤、词库与学习记录应保存在仓库外；仓库仅提供通用代码和示例。原生插件源码按 GPL-2.0-or-later 提供，见 `native/LICENSE`；本机复制的 Fcitx 默认图标遵循 Fcitx 的许可。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v
```

普通单元测试覆盖错误词库、并发更新、回退、输入中等待和学习数据保护。安装 `librime-bin librime-dev rime-data-pinyin-simp rime-prelude` 和 C 编译器后，还会运行真正的 Rime 集成测试：全拼、简拼、常见换序、邻键误按、完整上屏及正常拼音排序，并修改文件验证监视器自动编译后的新候选。可设置 `GINPUT_REQUIRE_RIME=1`，让缺少依赖直接失败而不是跳过。

安装 `fcitx-libs-dev` 后还会运行原生插件的按键回归测试，覆盖松键后的布局保持、选中候选、收起、失焦和空输入；`GINPUT_REQUIRE_PANEL=1` 要求该测试不可跳过。自定义头文件目录使用 `FCITX_INCLUDE_DIR`。

集成测试只在临时目录运行，不连接桌面、不改真实用户词库。自定义测试运行库可通过 `RIME_DEPLOYER`、`RIME_SHARED_DATA`、`RIME_INCLUDE_DIR`、`RIME_LIBRARY_DIR` 指定。

本机配置用 `--config /绝对路径/config.json` 指定；`build --output /空目录` 可单独验证编译。配置中的 `reload: "none"` 用于隔离测试或手动部署。

## 后续方向

- 图形词库编辑器：增删、搜索、权重与错误定位。
- 皮肤设置界面与更多布局选项。
- Fcitx 5 接入、部署完成反馈、打包安装及更细致的空闲检测。

项目依赖的设计依据：[Rime 输入方案文档](https://github.com/rime/home/wiki/RimeWithSchemata)、[拼写运算与常见换序规则](https://github.com/rime/home/wiki/SpellingAlgebra)、[Rime 邻键纠错实现](https://github.com/rime/librime/blob/1.7.3/src/rime/dict/corrector.cc)、[Fcitx Rime](https://github.com/fcitx/fcitx-rime)、[基础简体拼音词库](https://github.com/rime/rime-pinyin-simp)。运行库和基础词库由系统包提供，各自遵循上游许可。
