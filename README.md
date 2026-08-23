# EbookToolbox（电子书工具箱）

EbookToolbox 是一个面向 Windows、使用 PySide6 与 Qt Widgets 构建的桌面电子书工具箱。
项目目前处于**阶段 1：应用外壳**：已经提供主窗口、工具导航、欢迎页和页面占位符，
尚未接入 EPUB、TXT 或漫画的实际处理逻辑。

## 当前界面

- 左侧工具导航：首页、EPUB 目录修复、EPUB 图片压缩、TXT 工具、漫画工具、检测报告和设置；
- 中央区域随导航切换，首页包含项目简介与最近任务占位区；
- 底部状态栏展示应用状态；
- Qt Material 提供基础界面主题。

处理核心与界面将保持分离；后续耗时任务会通过后台任务框架执行，避免阻塞 Qt 主线程。

## 环境要求

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)（推荐）

## Windows 安装与运行

在 PowerShell 中进入项目目录，然后安装依赖并启动应用：

```powershell
uv sync --dev
uv run python main.py
```

也可以在 PowerShell 中设置 `PYTHONPATH` 后使用模块入口：

```powershell
$env:PYTHONPATH = "src"
uv run python -m ebook_toolbox
```

项目默认使用 uv 的通用默认 PyPI，不在仓库中绑定特定镜像。如果国内网络访问较慢，
可以仅在当前 PowerShell 会话中临时配置清华 PyPI 镜像、超时时间和文件复制模式：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
$env:UV_HTTP_TIMEOUT = "600"
$env:UV_LINK_MODE = "copy"
uv sync --dev
```

关闭当前 PowerShell 窗口后，这些临时环境变量不会继续生效。

## 开发检查

在无桌面显示服务的 Linux 环境中，可令 Qt 使用 offscreen 平台：

```bash
QT_QPA_PLATFORM=offscreen uv run pytest
uv run ruff check .
```

## 目录结构

```text
src/ebook_toolbox/
├── app.py                 # QApplication 创建和程序入口
├── ui/
│   ├── main_window.py     # 主窗口与页面切换
│   ├── navigation.py      # 左侧工具导航
│   └── pages/             # 欢迎页和功能占位页
└── __main__.py            # python -m ebook_toolbox 入口
tests/gui/                 # pytest-qt 界面测试
```

## 开发范围

当前版本只建立可扩展的 GUI 基础架构。旧脚本迁移、EPUB 读写、批处理和后台任务框架
将在后续阶段逐步实现；应用不会在现阶段修改任何电子书文件。

## EPUB 图片压缩 core（第一版）

仓库现已提供不依赖 Qt 的单本 EPUB 图片压缩 core，位于
`src/ebook_toolbox/services/epub_image_compressor/`。它处理 JPEG、PNG 和静态 WebP，
原样保留 GIF，并在遇到多帧 PNG/WebP 时跳过并返回 warning。默认配置跳过封面，
且不会覆盖源 EPUB 或已有输出。

```python
from pathlib import Path

from ebook_toolbox.services.epub_image_compressor import (
    BALANCED,
    CompressionRequest,
    compress_epub,
)

result = compress_epub(
    CompressionRequest(source=Path("book.epub"), profile=BALANCED)
)
if result.success:
    print(result.output)
else:
    print(result.errors)
```

当前版本只有同步的纯 Python core 和自动化测试，尚未接入 PySide6 页面、后台任务、
批处理或 CLI。调用方应检查 `result.success`、`result.errors` 和 `result.warnings`。

## EPUB 目录修复 core（第一版）

仓库现已提供不依赖 PySide6 的单本 EPUB 目录修复 core，位于
`src/ebook_toolbox/services/epub_toc_repair/`。它可根据书脊正文标题识别中文及常见英文
卷章结构，重建 EPUB2 NCX、EPUB3 `nav.xhtml`，并在目录文档缺失时补齐必要的 OPF
manifest/spine 引用。默认不覆盖输入或已有输出，默认输出名为
`<原文件名>_toc_fixed.epub`。

```python
from pathlib import Path

from ebook_toolbox.services.epub_toc_repair import (
    TocRepairRequest,
    repair_epub_toc,
)

result = repair_epub_toc(TocRepairRequest(source=Path("collection.epub")))
if result.success:
    print(result.output)
else:
    print(result.errors)
```

`replace_existing=False` 可保留已有且目标有效的格式主目录。目录修复 core 已接入
“EPUB 目录修复”页面，并通过后台线程池执行；当前尚未提供 CLI 或批处理。
