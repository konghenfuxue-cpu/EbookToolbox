# EbookToolbox（电子书工具箱）

EbookToolbox 是一个面向个人 Windows 电脑、使用 PySide6 与 Qt Widgets 构建的 EPUB
工具箱。业务 core 与界面分离，耗时操作通过后台任务运行，避免阻塞主界面。

当前版本为 `0.1.0`，已提供四个正式功能页面：

- **EPUB 图片压缩**：压缩 JPEG、PNG 和静态 WebP，可选压缩策略和封面处理；
  显示逐图片进度与处理计数，可在图片之间或原子发布前安全取消；
- **EPUB 目录修复**：识别卷章结构并修复 EPUB2 NCX 与 EPUB3 nav 目录，显示五阶段进度，并可在原子发布前安全取消；
- **EPUB 综合修复**：单本或顺序批量修复内部路径、资源引用和 OPF 结构；检测
  spine 直接图片，并可显式生成 XHTML 包装页（默认仅报告）；批量任务显示逐文件
  进度与成功/失败计数，可在当前文件安全完成后取消剩余任务；卷章规范化提供读取、识别、写入、发布四阶段进度，并可在发布前安全取消。
- **TXT 排版并合并**：按 `[作者]` 文件名分组，自动识别 UTF-8、UTF-16 和 GB18030，
  清理不可见字符并生成 UTF-8 BOM 作者合集；显示逐文件进度与成功/失败计数，
  可在当前作者合集安全发布后取消剩余作者。

所有工具默认都不会覆盖源文件。TXT 合并默认输出到输入目录下独立的
`EbookToolbox-合集` 文件夹；各工具相互独立，不会被其他页面自动串联执行。

## 开发环境运行

需要 Windows、Python 3.11 或更高版本，以及
[uv](https://docs.astral.sh/uv/)。在 PowerShell 中进入项目根目录：

```powershell
uv sync --dev
uv run python main.py
```

也可以使用模块入口：

```powershell
$env:PYTHONPATH = "src"
uv run python -m ebook_toolbox
```

项目不绑定特定 Python 包镜像。如果当前网络访问默认 PyPI 较慢，可以只为当前
PowerShell 会话临时设置镜像：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
$env:UV_HTTP_TIMEOUT = "600"
$env:UV_LINK_MODE = "copy"
uv sync --dev
```

开发检查命令：

```powershell
uv run ruff check .
uv run pytest
```

## 构建 Windows 便携版

项目使用 PyInstaller 的 **one-folder** 模式生成无控制台窗口程序。PyInstaller 只属于
开发依赖，不会作为应用运行依赖安装到最终用户的电脑。

在 Windows PowerShell 中进入项目根目录，然后运行：

```powershell
.\scripts\build_windows.ps1
```

如果本机执行策略禁止运行项目脚本，可以仅为当前 PowerShell 进程临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\build_windows.ps1
```

脚本默认会先运行依赖同步、Ruff 和完整测试。CI 已单独完成这些检查时，可使用
`-SkipChecks` 直接执行构建、资源校验和 EXE 自检；该参数不应替代本地提交前验证。

脚本会依次执行以下工作：

1. 确认系统为 Windows，且当前目录是项目根目录；
2. 执行 `uv sync --dev`、Ruff 和完整 pytest；
3. 只清理本项目明确的 PyInstaller 工作目录及便携版输出；
4. 使用 `EbookToolbox.spec` 构建 `EbookToolbox.exe`；
5. 验证 one-folder 的 `_internal` 运行资源（含 QtGui），并运行 EXE 的打包自检；
6. 检查 Qt Windows/图像插件、qt-material 主题与字体、Pillow 原生扩展和应用图标；
7. 将整个 `EbookToolbox` 目录压缩为便携版 ZIP，并打印实际大小。

构建输出为：

```text
dist/EbookToolbox/EbookToolbox.exe
dist/EbookToolbox-portable.zip
dist/EbookToolbox-portable.zip.sha256
```

构建中间文件位于 `build/EbookToolbox/`。`build/`、`dist/` 和开发缓存均被 Git 忽略，
而 `EbookToolbox.spec`、Windows 版本信息和图标源资源需要纳入版本控制。

GitHub Actions 会在 Windows 测试通过后执行同一构建脚本，启动打包后的 EXE 完成
`--self-test`，并将 `EbookToolbox-portable.zip` 及其 SHA-256 校验文件作为保留 14 天的
工作流产物上传。

正式发布使用 `v主版本.次版本.修订版本` 标签（例如 `v0.1.0`）。推送标签后，CI
先校验标签与 `pyproject.toml`、应用 `__version__`、Windows 数字和文本版本一致，
再执行测试、真实构建及 EXE 自检。发布任务下载同次运行的产物，复核 SHA-256，
然后创建正式 GitHub Release，附带便携 ZIP 和校验文件，并自动生成发布说明。
版本不一致、校验失败或已有同名 Release 时停止；只有标签触发发布任务。

## 使用 Windows 便携版

目标电脑不需要安装 Python、uv、PySide6 或 Pillow。解压
`EbookToolbox-portable.zip`，保留顶层 `EbookToolbox` 目录中的全部文件，然后双击
`EbookToolbox.exe`。不要只复制或单独运行 EXE；one-folder 版本需要同目录下的
`_internal` 运行资源。

“EPUB 综合修复”中的 Calibre 外部检查仍是可选功能。便携版不会捆绑 Calibre；启用该
选项时，目标电脑需要自行安装 Calibre，并确保对应命令可由应用找到。

当前版本不是安装程序，不需要管理员权限，不包含 Inno Setup、MSIX 或自动更新。

### Windows SmartScreen 提示

当前便携版没有代码签名。首次启动时 Windows SmartScreen 可能显示“Windows 已保护
你的电脑”。请只运行自己构建或从可信渠道取得的 ZIP；确认来源后，可在提示中选择
“更多信息”再选择“仍要运行”。此提示不代表构建失败，正式分发时仍应考虑购买并使用
可信代码签名证书。

## 服务层概览

四个功能页面仅收集参数和展示结构化结果，实际处理位于以下纯 Python 服务中：

```text
src/ebook_toolbox/services/
├── epub_image_compressor/    # 单本 EPUB 图片压缩
├── epub_toc_repair/          # 单本 EPUB 目录修复
├── epub_structural_repair/   # 单本及顺序批量综合结构修复
└── text_merge/               # TXT 排版与按作者合并
```

服务层不依赖 PySide6，可单独调用和测试。例如：

```python
from pathlib import Path

from ebook_toolbox.services.epub_image_compressor import (
    BALANCED,
    CompressionRequest,
    compress_epub,
)
from ebook_toolbox.services.epub_structural_repair import (
    BatchRepairRequest,
    repair_epub_batch,
)
from ebook_toolbox.services.epub_toc_repair import (
    TocRepairRequest,
    repair_epub_toc,
)
from ebook_toolbox.services.text_merge import TextMergeRequest, merge_author_texts

compressed = compress_epub(
    CompressionRequest(source=Path("book.epub"), profile=BALANCED)
)
toc_fixed = repair_epub_toc(TocRepairRequest(source=Path("collection.epub")))
repaired = repair_epub_batch(
    BatchRepairRequest(inputs=(Path("library"),), recursive=True)
)
merged = merge_author_texts(TextMergeRequest(Path("txt-library")))
```

调用方应始终检查结果的 `success`、`errors` 和 `warnings`，不要仅根据输出路径判断成功。

## 项目结构

```text
main.py                              # 源码启动入口
EbookToolbox.spec                    # PyInstaller one-folder 配置
packaging/windows_version_info.txt   # Windows EXE 版本资源
scripts/build_windows.ps1            # 完整便携版构建脚本
src/ebook_toolbox/
├── app.py                           # QApplication、主题和程序入口
├── resources/                       # 项目自有应用图标
├── services/                        # EPUB 与 TXT 纯 Python 服务
└── ui/                              # 主窗口、导航和正式功能页面
tests/                               # core 与 pytest-qt 自动化测试
```

`migration_sources/` 仅作为旧脚本迁移参考，不会包含在便携版发布产物中。
