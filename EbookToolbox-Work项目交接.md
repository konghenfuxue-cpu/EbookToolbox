# EbookToolbox 项目交接文档

> 用途：将当前 Windows 本地项目迁移到 GitHub，并由 ChatGPT Work 接手主要开发。
>
> 更新日期：2026-08-12

## 1. 项目概述

项目名称：`EbookToolbox`

本地目录：

```text
D:\EbookToolbox
```

项目目标：开发一个长期维护、以 Windows 为主要平台的 PySide6 电子书工具箱，将目前分散的 EPUB、TXT、漫画处理脚本逐步整合进一个统一图形界面。

项目定位：

- 主要供个人长期使用，后续可能公开发布 Windows 安装包。
- 优先保证稳定、数据安全、中文路径兼容和批量处理能力。
- 使用纯 Python 核心处理模块与 PySide6 Qt Widgets 桌面界面。
- 不再以 Tauri/Vue 项目 `cnwxi/epub_tool` 为基础。

## 2. 已确定的技术方案

| 项目 | 选择 |
|---|---|
| Python | 3.11.9 |
| 桌面界面 | PySide6 6.11.1 |
| Qt 技术 | Qt Widgets，不使用 QML |
| 主题 | qt-material 2.17，后续允许自定义 QSS |
| 依赖管理 | uv 0.9.7 |
| 单元测试 | pytest 9.1.1 |
| Qt 界面测试 | pytest-qt 4.5.0 |
| 检查与格式化 | Ruff 0.16.2 |
| 版本控制 | Git，主分支 `main` |
| 代码结构分析 | codebase-memory-mcp 0.10.2（Windows 本机） |
| 主要开发协作 | ChatGPT Work＋GitHub＋本机 VS Code |

## 3. 当前完成状态

已经完成：

- 创建项目目录 `D:\EbookToolbox`。
- 执行 `uv init --app`。
- 创建项目虚拟环境 `.venv`。
- 安装 PySide6、qt-material、pytest、pytest-qt 和 Ruff。
- VS Code 已选择项目解释器：

  ```text
  D:\EbookToolbox\.venv\Scripts\python.exe
  ```

- 验证 PySide6 可以正常导入，版本为 `6.11.1`。
- 初始化 Git 仓库。
- 分支名称改为 `main`。
- Git 用户信息已配置。
- Git 工作区当前为干净状态。
- Windows 本机已经安装并验证 `codebase-memory-mcp 0.10.2`。
- CBM 已接入部分 VS Code Profile、Junie、Copilot 和 Continue。

尚未完成：

- 尚未建立正式 PySide6 项目架构。
- 尚未创建主窗口和功能页面。
- 尚未迁移任何旧脚本。
- 尚未创建 GitHub 远程仓库。
- 尚未将本地仓库推送到 GitHub。
- 尚未配置 GitHub Actions Windows 测试或打包。
- 尚未建立发布安装包。

## 4. 当前本地仓库文件

项目目前仍是 `uv init` 生成的最小脚手架，主要包含：

```text
D:\EbookToolbox
├─ .git\
├─ .venv\
├─ .gitignore
├─ .python-version
├─ README.md
├─ main.py
├─ pyproject.toml
└─ uv.lock
```

注意：`.venv` 不应提交到 GitHub。

## 5. 最终计划整合的功能

### EPUB 修复

- EPUB 目录修复。
- EPUB 批量修复。
- ZIP/EPUB 容器结构检查与安全重打包。
- OPF、manifest、spine、NCX、EPUB 3 Navigation 修复。
- 无效跳转、重复锚点和缺失资源检测。
- Apple Books、多看等阅读器兼容性检查。

### EPUB 图片

- EPUB 图片压缩。
- JPEG 质量、最大尺寸和格式策略设置。
- GIF 静态图转 JPEG/PNG 等兼容修复。
- PNG、JPEG、WebP 转换。
- 封面识别、替换和引用修复。
- 图片缺失、损坏和未加载风险报告。

### TXT 工具

- 多个 TXT 排版与合并。
- 卷名、章节名和隐藏 VIP 章节识别。
- 章节编号纠错。
- 网站广告清理。
- TXT 转 EPUB。
- 生成卷章目录和跳转锚点。
- 输出逐项异常报告。

### 漫画工具

- ZIP、CBZ、图片目录扫描。
- 长条韩漫与分页日漫模式区分。
- 章节识别与实际话数命名。
- CBZ 自动分卷。
- 封面下载或生成卷号封面。
- JPEG 质量约 85、限制宽度约 1440～1800 像素。
- 简体＋繁体关键词合并搜索和结果去重（后期功能）。
- 更准确过滤公告、后记、重复页和未识别文件。

## 6. 必须遵守的架构与安全约束

1. 核心处理逻辑与 PySide6 界面分离。
2. 核心功能应可以不启动 GUI 独立运行和测试。
3. 所有耗时任务必须在后台执行，不得阻塞 Qt 主线程。
4. 必须支持任务进度、取消、失败重试和错误详情。
5. 默认不覆盖、不删除源文件。
6. 覆盖模式必须由用户明确开启，并尽可能先建立可恢复备份。
7. 不得写死 `D:\` 或任何用户目录。
8. 必须正确处理 Windows 中文路径、空格及长路径。
9. 临时目录必须使用独立安全目录，并保证异常后清理。
10. EPUB 写回必须使用临时文件完成验证后再原子替换目标。
11. 日志既要显示在界面，也要保存为 UTF-8 文件。
12. 每项批处理都要返回成功、失败、跳过和警告统计。
13. 不能因单个文件失败而终止整个批处理。
14. 不应在日志中泄露 Cookie、Token、密码或其他凭据。
15. 真实 EPUB、漫画、小说书库和图片不提交到 GitHub。

## 7. 推荐项目结构

以下是目标结构，不要求第一步全部创建：

```text
EbookToolbox/
├─ src/
│  └─ ebook_toolbox/
│     ├─ __init__.py
│     ├─ __main__.py
│     ├─ app.py
│     ├─ ui/
│     │  ├─ main_window.py
│     │  ├─ navigation.py
│     │  ├─ pages/
│     │  ├─ dialogs/
│     │  └─ widgets/
│     ├─ core/
│     │  ├─ models.py
│     │  ├─ results.py
│     │  ├─ exceptions.py
│     │  └─ tool_contract.py
│     ├─ services/
│     │  ├─ epub/
│     │  ├─ text/
│     │  └─ comic/
│     ├─ workers/
│     │  ├─ task_worker.py
│     │  └─ task_manager.py
│     ├─ settings/
│     │  └─ app_settings.py
│     ├─ logging/
│     │  └─ log_manager.py
│     └─ utils/
│        ├─ paths.py
│        ├─ atomic_write.py
│        └─ temp_dirs.py
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ gui/
├─ tools_legacy/
├─ samples/
├─ reports/
├─ docs/
├─ .github/workflows/
├─ .cbmignore
├─ .gitignore
├─ AGENTS.md
├─ README.md
├─ pyproject.toml
└─ uv.lock
```

## 8. 第一版界面目标

主窗口标题：`电子书工具箱`

左侧导航：

- 首页
- EPUB 修复
- EPUB 图片
- TXT 工具
- 漫画工具
- 检测报告
- 设置

中间区域：

- 根据左侧导航切换页面。
- 首页显示项目说明和最近任务占位区域。
- 文件任务页支持选择文件、选择目录和拖放。
- 提供任务列表、状态、单项进度和整体进度。
- 提供输出目录、处理参数和开始/取消按钮。
- 底部或单独页面显示实时日志。

设置：

- 浅色/深色主题。
- 默认输出目录策略。
- 是否自动打开输出目录。
- 最大并发数。
- 日志保留策略。
- 使用 `QSettings` 永久保存。
- 保存窗口尺寸、位置和最后打开页面。

## 9. 后台任务建议

第一阶段建议采用：

- `QThreadPool`＋`QRunnable` 执行 Python 内部任务。
- Qt Signals 报告进度、日志、结果、错误和完成状态。
- 使用线程安全的取消令牌，由处理循环定期检查。
- 旧 PowerShell、CMD 或独立 EXE 使用 `QProcess` 调用。
- 禁止从工作线程直接修改任何界面控件。

统一任务结果至少包含：

```text
任务ID
工具ID
输入路径
输出路径
状态
处理耗时
成功/失败/跳过/警告统计
用户可读摘要
错误类型和详细信息
报告文件路径
```

## 10. 旧脚本迁移原则

旧脚本不能整目录直接复制后连接按钮。每个工具需要先拆分：

- 核心处理函数。
- 输入验证。
- 参数模型。
- 输出路径策略。
- 进度回调。
- 取消检查。
- 日志接口。
- 结果和报告模型。
- 命令行或 UI 交互层。

核心处理函数不能依赖：

- `Read-Host`
- `Pause`
- 固定工作目录
- 固定 `D:\` 路径
- 控制台颜色
- 自己弹出文件选择框
- 默认覆盖源文件

可以先使用 `QProcess` 兼容旧脚本，再逐步迁移为 Python 服务。

## 11. 分阶段开发计划

### 阶段 0：项目基线

- 建立 GitHub 私有仓库。
- 推送本地 `main` 分支。
- 将仓库连接到 ChatGPT Work。
- 保持原始最小脚手架提交不变。

### 阶段 1：应用外壳

- 创建标准 `src` 布局。
- 创建 PySide6 主窗口和左侧导航。
- 创建页面占位符。
- 接入 qt-material。
- 使用 QSettings 保存主题和窗口状态。
- 增加基本 pytest-qt 启动测试。
- 配置 Ruff。

### 阶段 2：任务框架

- 建立统一工具协议。
- 建立任务队列和后台执行器。
- 建立取消和进度机制。
- 建立日志与报告模型。
- 建立文件输入、输出和安全写回策略。

### 阶段 3：首个真实功能

第一个迁移功能：`EPUB目录修复工具 v2.4`。

原因：输入和输出都是 EPUB，适合先验证统一任务框架。

迁移前需要提供：

- 完整脚本目录。
- 使用说明。
- 一份正常 EPUB。
- 一份需要修复的 EPUB。
- 期望修复结果。
- 现有运行日志或报告。

### 阶段 4：扩充 EPUB 功能

- EPUB 批量修复。
- 图片压缩和兼容转换。
- 封面处理。
- 完整检测报告。

### 阶段 5：TXT 与漫画工具

- TXT 排版合并。
- TXT 转 EPUB。
- 漫画识别和 CBZ 分卷。

### 阶段 6：发布

- 使用 `pyside6-deploy`/Nuitka 构建 Windows 版本。
- 配置 GitHub Actions Windows Runner。
- 生成安装包或便携版本。
- 增加版本号、更新日志和发布说明。

## 12. ChatGPT Work 与 Windows 本机的职责

### ChatGPT Work

- 主要架构设计。
- 多文件代码修改。
- Python 单元测试。
- Ruff 检查。
- 文档维护。
- GitHub 分支和 PR 工作流。

### Windows 本机 VS Code

- 运行原生 PySide6 窗口。
- 检查拖放、文件选择、主题和中文路径。
- 使用本机 codebase-memory-mcp。
- 调试 PowerShell、CMD、Calibre 和 Windows 专用功能。
- 构建和验证 Windows EXE/安装包。

两边通过 GitHub 分支同步，不直接复制 `.venv`。

## 13. GitHub 迁移步骤

### 在 GitHub 创建仓库

建议仓库名：

```text
EbookToolbox
```

建议先设置为 Private。创建时不要勾选自动添加 README、`.gitignore` 或 License，因为本地仓库已经存在这些文件。

### 在本地连接远程仓库

将下面的地址替换为自己的 GitHub 仓库地址：

```powershell
Set-Location "D:\EbookToolbox"

git remote add origin https://github.com/你的用户名/EbookToolbox.git
git remote -v
git push -u origin main
```

如果已经存在 `origin`：

```powershell
git remote set-url origin https://github.com/你的用户名/EbookToolbox.git
git push -u origin main
```

### 推送前检查

```powershell
git status
git ls-files
```

确认不存在：

```text
.venv/
真实 EPUB/TXT/CBZ/ZIP 文件
小说或漫画资源
日志中的私人信息
Cookie、Token、密码或 API Key
```

## 14. 交给 ChatGPT Work 的首条指令

连接 GitHub 仓库后，可以粘贴：

```text
请读取仓库根目录的《EbookToolbox 项目交接文档》以及现有 README、pyproject.toml、uv.lock 和入口文件。

本轮只做只读审查，不修改任何文件。请确认：
1. 当前仓库实际状态与交接文档是否一致；
2. Git 中是否误提交了 .venv、测试书籍、日志或敏感文件；
3. pyproject.toml 中依赖是否正确；
4. 当前最小入口能否运行；
5. 推荐的阶段 1 目录结构是否需要简化；
6. 第一阶段将创建和修改哪些文件；
7. 如何验证 Windows 中文路径、QSettings 和非阻塞界面。

请先输出审查结论和精简实施计划，等我确认后再创建 PySide6 应用外壳。
```

## 15. 当前第一优先级

不要立即迁移所有旧脚本。

当前唯一优先任务是：

> 完成一个能够启动、切换页面、保存主题与窗口状态，并通过 Ruff 和 pytest-qt 的 PySide6 应用外壳。

应用外壳稳定并提交后，再建立统一后台任务框架，最后迁移第一个 EPUB 目录修复工具。

