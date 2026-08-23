# EPUB 批量修复工具使用说明

> 适用环境：Windows 10/11、Python 3  
> 适用文件：`.epub`  
> 处理原则：生成新的“修复版”，不修改原始 EPUB  
> 配套脚本：增强版（2026-08-15）

## 1. 工具文件

请把以下两个文件放在同一个文件夹中：

```text
EPUB批量修复.cmd
epub_repair.py
```

建议将上传文件重命名为上面的标准名称，删除文件名中的 `(1)`。

| 文件 | 用途 |
|---|---|
| `EPUB批量修复.cmd` | Windows拖拽启动器，适合日常使用 |
| `epub_repair.py` | 核心修复程序，也可在PowerShell中直接运行 |

## 2. 主要功能

工具会自动执行以下处理：

- 修复 EPUB 内部含 `: * ? " < > |` 等Windows非法字符的文件名和目录名；
- 按类型重建安全路径，例如 `Text`、`Images`、`Styles`、`Fonts`；
- 同步更新 OPF、NCX、XHTML和CSS中的资源引用；
- 删除 manifest 中指向不存在文件的无效项目；
- 将未登记的图片、CSS、字体和正文资源补回 manifest；
- 清理 spine 中引用无效资源的项目；
- 缺少语言元数据时补充 `zh-CN`；
- 将 `mimetype` 放到压缩包第一项，并使用不压缩方式保存；
- 检查ZIP完整性、manifest、spine和正文；
- 检测到内容加密时停止处理并给出提示；
- 如果系统能找到 Calibre 的 `ebook-meta`，额外测试Calibre能否读取。

它主要用于修复以下错误：

```text
Book is empty, no content in spine
Spineless: Book is empty
manifest 资源不存在
Id does not exist in manifest
EPUB appears to be invalid ZIP file
WinError 123：文件名、目录名或卷标语法不正确
```

## 3. 使用前准备

### 3.1 安装 Python 3

在PowerShell中运行：

```powershell
py -3 --version
```

也可以运行：

```powershell
python --version
```

如果显示类似下面的内容，说明已经安装：

```text
Python 3.13.5
```

如果提示找不到命令，请安装Python 3，并在安装界面勾选：

```text
Add Python to PATH
```

### 3.2 整理工具文件

推荐目录结构：

```text
EPUB批量修复工具
├── EPUB批量修复.cmd
└── epub_repair.py
```

不要只复制CMD文件，否则无法运行核心修复程序。

## 4. 最简单的使用方法

### 4.1 修复一本 EPUB

1. 选中需要修复的 EPUB；
2. 将它拖到 `EPUB批量修复.cmd` 上；
3. 等待窗口显示处理结果；
4. 按任意键关闭窗口。

程序会在原书旁边生成：

```text
原书名_修复版.epub
```

例如：

```text
我家老婆来自一千年前.epub
我家老婆来自一千年前_修复版.epub
```

原始 EPUB 不会被修改或删除。

### 4.2 同时修复多本 EPUB

选中多本 EPUB，一起拖到：

```text
EPUB批量修复.cmd
```

处理过程会显示：

```text
[1/10]
[2/10]
[3/10]
```

完成后显示：

```text
处理完成：成功 9，失败 1
```

### 4.3 修复一个文件夹

把包含 EPUB 的文件夹拖到CMD上。

默认只处理该文件夹当前一层中的 EPUB，不扫描子文件夹。

## 5. PowerShell高级用法

先在工具文件夹空白处按住 `Shift` 并右键，选择“在终端中打开”。

### 5.1 修复单本书

```powershell
py -3 .\epub_repair.py "D:\待处理\小说.epub"
```

### 5.2 处理文件夹当前一层

```powershell
py -3 .\epub_repair.py "D:\待处理"
```

### 5.3 递归扫描所有子文件夹

```powershell
py -3 .\epub_repair.py -r "D:\小说整理"
```

### 5.4 统一保存到另一个文件夹

```powershell
py -3 .\epub_repair.py -r "D:\小说整理" -o "D:\修复完成"
```

### 5.5 覆盖已有修复版

```powershell
py -3 .\epub_repair.py --overwrite "D:\待处理\小说.epub"
```

拖拽CMD运行时已经默认带有 `--overwrite`，只会覆盖同名的：

```text
原书名_修复版.epub
```

不会覆盖原始 EPUB。

## 6. 参数说明

| 参数 | 作用 |
|---|---|
| `-r`、`--recursive` | 递归扫描所有子文件夹 |
| `-o`、`--output-dir` | 指定统一输出目录 |
| `--overwrite` | 覆盖已经存在的同名修复版 |
| `--reference` | 指定内容完整的原始 EPUB，为缺失正文或资源的转换版补回文件后再修复 |

### 6.1 用参考原书补回缺失资源

如果待修复 EPUB 的 OPF 仍记录章节或图片，但压缩包内的实际文件已经丢失，可指定一份内容完整的原始 EPUB：

```powershell
py -3 .\epub_repair.py --overwrite --reference "D:\原书\完整原书.epub" "D:\待修复\转换版.epub"
```

程序只会在能够安全对应时补回资源，并继续执行结构修复；不会修改参考原书或待修复原文件。

## 7. 如何判断修复成功

成功时会显示类似：

```text
[成功] D:\待处理\小说_修复版.epub
正文 397 项，资源 410 项；Calibre ebook-meta 读取通过
```

如果电脑没有检测到 `ebook-meta`，可能显示：

```text
未检测到 ebook-meta，已完成内部结构验证
```

这不代表失败，只表示没有执行Calibre附加测试。

## 8. 修复后的推荐检查顺序

1. 保留原始 EPUB，不要立即删除；
2. 用 Sigil 打开 `_修复版.epub`；
3. 检查封面、目录和章节是否正常；
4. 随机查看开头、中间和结尾章节；
5. 在 Sigil 中运行 EPUBCheck；
6. 如果需要，再运行 ImgShrinker 批量压缩图片；
7. 按 `Ctrl+S` 保存；
8. 导入 Calibre 或 Calibre-Web；
9. 在 Apple Books 或实际阅读器中测试目录跳转。

推荐流程：

```text
原始EPUB
→ EPUB批量修复
→ Sigil/EPUBCheck验证
→ ImgShrinker压缩图片（可选）
→ Calibre导入
→ Apple Books或阅读器测试
```

## 9. 常见错误及处理方法

### 9.1 找不到 Python

错误：

```text
[ERROR] Python 3 was not found.
```

处理：

1. 安装Python 3；
2. 安装时勾选 `Add Python to PATH`；
3. 关闭并重新打开PowerShell；
4. 再次运行工具。

### 9.2 不是有效的 ZIP/EPUB 文件

错误：

```text
不是有效的 ZIP/EPUB 文件
```

可能原因：

- 文件下载不完整；
- 文件扩展名虽然是 `.epub`，实际并不是EPUB；
- ZIP中央目录已经严重损坏。

建议重新下载原文件。

### 9.3 ZIP数据损坏或CRC错误

错误：

```text
ZIP 数据损坏
CRC 错误
```

说明压缩数据本身已经损坏，通常无法通过结构重建恢复，需要更换文件来源。

### 9.4 检测到 DRM/内容加密

错误：

```text
检测到 DRM/内容加密
```

工具不会移除DRM，也不会尝试绕过内容保护。请更换合法、无DRM的EPUB版本。

> 注意：少数EPUB只对字体进行标准混淆，也可能带有 `encryption.xml`。如果确认书籍正文可以正常阅读，却被报告为加密，可用Sigil检查字体资源，或把文件和日志单独分析。

### 9.5 manifest资源不存在

错误示例：

```text
manifest 资源不存在：Images/logo.png
```

当前增强版会自动删除指向不存在文件的坏记录。缺失图片本身无法恢复，但可以避免Calibre或Sigil在处理时中止。

### 9.6 资源未登记到 manifest

错误：

```text
资源未登记到 manifest
```

增强版会把实际存在的图片、CSS、字体和正文补回manifest。如果仍然报错，请保留原书和完整日志进一步检查。

### 9.7 spine中没有可读取正文

错误：

```text
spine 中没有可读取正文
```

说明可能存在以下情况：

- 正文文件已经丢失；
- OPF严重损坏；
- 正文没有登记到spine；
- 文件并不是正常制作的EPUB。

这种情况通常需要单独重建OPF和阅读顺序。

### 9.8 输出文件已经存在

在命令末尾加入：

```powershell
--overwrite
```

拖拽CMD运行时已经默认覆盖旧的修复版。

### 9.9 双击 CMD 后没有处理书籍

CMD需要接收EPUB或文件夹路径。正确做法是把文件拖到CMD上，而不是直接双击CMD。

## 10. 工具不能修复的内容

本工具不是万能恢复程序，无法处理：

- DRM加密正文；
- 已经丢失的章节、图片或字体；
- 严重损坏且无法解压的ZIP数据；
- 错别字、乱码正文和翻译错误；
- 段落、字体、行距等普通排版问题；
- 自动生成章节标题和目录；
- 繁简转换；
- 被删减的小说内容。

这些问题需要使用Sigil、Calibre编辑器、文本处理工具或其他原始来源处理。

## 11. 使用建议

- 第一次处理重要书籍时，先复制一份测试；
- 不要直接删除原始EPUB；
- 批量处理整个书库前，先用5～10本进行测试；
- 不要同时在Sigil、Calibre编辑器中打开正在处理的文件；
- 文件被占用时，先关闭阅读器和编辑器；
- 修复成功只代表结构通过检查，仍应抽查正文和目录；
- DRM文件直接分类记录，不要反复运行修复工具；
- 普通排版问题不要使用结构修复工具反复重打包。

## 12. 快速参考

```powershell
# 修复一本
py -3 .\epub_repair.py "D:\待处理\小说.epub"

# 处理文件夹当前一层
py -3 .\epub_repair.py "D:\待处理"

# 递归处理所有子文件夹
py -3 .\epub_repair.py -r "D:\小说整理"

# 递归处理并输出到指定目录
py -3 .\epub_repair.py -r "D:\小说整理" -o "D:\修复完成"

# 覆盖已有修复版
py -3 .\epub_repair.py --overwrite "D:\待处理\小说.epub"
```

---

最后更新：2026-08-15
