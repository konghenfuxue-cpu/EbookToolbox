[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-NormalizedPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    return $fullPath.TrimEnd(
        [char[]]@(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    )
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    Write-Host ("> {0} {1}" -f $FilePath, ($ArgumentList -join " ")) -ForegroundColor Cyan
    & $FilePath @ArgumentList
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0) {
        throw "命令失败（退出码 $nativeExitCode）：$FilePath $($ArgumentList -join ' ')"
    }
}

function Remove-SafeBuildTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [Parameter(Mandatory = $true)]
        [string[]]$AllowedTargets
    )

    $normalizedTarget = Get-NormalizedPath -Path $Path
    $projectPrefix = $ProjectRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $normalizedTarget.StartsWith(
        $projectPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "拒绝清理项目目录之外的路径：$normalizedTarget"
    }

    $isAllowed = $false
    foreach ($allowedTarget in $AllowedTargets) {
        if ([string]::Equals(
            $normalizedTarget,
            $allowedTarget,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            $isAllowed = $true
            break
        }
    }
    if (-not $isAllowed) {
        throw "拒绝清理未列入白名单的路径：$normalizedTarget"
    }

    $pathToInspect = $normalizedTarget
    while (-not [string]::Equals(
        $pathToInspect,
        $ProjectRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        if (Test-Path -LiteralPath $pathToInspect) {
            $inspectedItem = Get-Item -LiteralPath $pathToInspect -Force
            if (
                ($inspectedItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                throw "拒绝通过重解析点清理构建产物：$pathToInspect"
            }
        }
        $pathToInspect = Split-Path -Parent $pathToInspect
    }

    if (Test-Path -LiteralPath $normalizedTarget) {
        Write-Host "清理：$normalizedTarget"
        $item = Get-Item -LiteralPath $normalizedTarget -Force
        if ($item.PSIsContainer) {
            Remove-Item -LiteralPath $normalizedTarget -Recurse -Force
        }
        else {
            Remove-Item -LiteralPath $normalizedTarget -Force
        }
    }
}

function Assert-RequiredFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "构建产物缺少 $Description：$Path"
    }
}

function Assert-RequiredFilePattern {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,

        [Parameter(Mandatory = $true)]
        [string]$Filter,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        throw "构建产物缺少 $Description 所在目录：$Directory"
    }

    $match = Get-ChildItem -LiteralPath $Directory -File -Filter $Filter | Select-Object -First 1
    if ($null -eq $match) {
        throw "构建产物缺少 $Description（匹配 $Filter）：$Directory"
    }
}

try {
    if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
        throw "此脚本只支持 Windows。"
    }

    $scriptDirectory = Get-NormalizedPath -Path $PSScriptRoot
    $projectRoot = Get-NormalizedPath -Path (Split-Path -Parent $scriptDirectory)
    $currentDirectory = Get-NormalizedPath -Path (Get-Location).Path
    if (-not [string]::Equals(
        $currentDirectory,
        $projectRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "请在项目根目录运行脚本。当前目录：$currentDirectory；项目根目录：$projectRoot"
    }

    foreach ($rootMarker in @("pyproject.toml", "uv.lock", "EbookToolbox.spec", "main.py")) {
        $markerPath = Join-Path $projectRoot $rootMarker
        if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
            throw "项目根目录缺少必要文件：$markerPath"
        }
    }

    $uvCommand = Get-Command uv -CommandType Application -ErrorAction Stop

    $buildWorkDirectory = Get-NormalizedPath -Path (Join-Path $projectRoot "build/EbookToolbox")
    $portableDirectory = Get-NormalizedPath -Path (Join-Path $projectRoot "dist/EbookToolbox")
    $zipPath = Get-NormalizedPath -Path (Join-Path $projectRoot "dist/EbookToolbox-portable.zip")
    $allowedCleanupTargets = @($buildWorkDirectory, $portableDirectory, $zipPath)

    Invoke-NativeCommand -FilePath $uvCommand.Source -ArgumentList @("sync", "--dev")
    Invoke-NativeCommand -FilePath $uvCommand.Source -ArgumentList @("run", "ruff", "check", ".")
    Invoke-NativeCommand -FilePath $uvCommand.Source -ArgumentList @("run", "pytest")

    foreach ($target in $allowedCleanupTargets) {
        Remove-SafeBuildTarget `
            -Path $target `
            -ProjectRoot $projectRoot `
            -AllowedTargets $allowedCleanupTargets
    }

    Invoke-NativeCommand -FilePath $uvCommand.Source -ArgumentList @(
        "run",
        "pyinstaller",
        "--noconfirm",
        "--workpath",
        "build/EbookToolbox",
        "--distpath",
        "dist",
        "EbookToolbox.spec"
    )

    $executablePath = Join-Path $portableDirectory "EbookToolbox.exe"
    $internalDirectory = Join-Path $portableDirectory "_internal"
    Assert-RequiredFile -Path $executablePath -Description "EbookToolbox.exe"

    $requiredFiles = @(
        @("qt_material/themes/light_blue.xml", "qt-material light_blue 主题"),
        @("qt_material/material.qss.template", "qt-material 样式模板"),
        @("qt_material/fonts/roboto/Roboto-Regular.ttf", "qt-material Roboto 字体"),
        @("PySide6/plugins/platforms/qwindows.dll", "Qt Windows 平台插件"),
        @("PySide6/plugins/imageformats/qsvg.dll", "Qt SVG 图像插件"),
        @("PySide6/plugins/imageformats/qico.dll", "Qt ICO 图像插件"),
        @("ebook_toolbox/resources/app_icon.ico", "应用 ICO 图标"),
        @("ebook_toolbox/resources/app_icon.svg", "应用 SVG 图标")
    )
    foreach ($requiredFile in $requiredFiles) {
        $relativePath = $requiredFile[0].Replace(
            "/",
            [System.IO.Path]::DirectorySeparatorChar
        )
        Assert-RequiredFile `
            -Path (Join-Path $internalDirectory $relativePath) `
            -Description $requiredFile[1]
    }

    $pillowDirectory = Join-Path $internalDirectory "PIL"
    Assert-RequiredFilePattern `
        -Directory $pillowDirectory `
        -Filter "_imaging.*.pyd" `
        -Description "Pillow _imaging 扩展"
    Assert-RequiredFilePattern `
        -Directory $pillowDirectory `
        -Filter "_imagingcms.*.pyd" `
        -Description "Pillow _imagingcms 扩展"
    Assert-RequiredFilePattern `
        -Directory $pillowDirectory `
        -Filter "_webp.*.pyd" `
        -Description "Pillow _webp 扩展"

    $forbiddenNames = @(
        ".codebase-memory",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "migration_sources",
        "tests"
    )
    $forbiddenItems = @(
        Get-ChildItem -LiteralPath $portableDirectory -Recurse -Force |
            Where-Object {
                ($forbiddenNames -contains $_.Name) -or
                ((-not $_.PSIsContainer) -and $_.Extension -in @(".pyc", ".pyo"))
            }
    )
    if ($forbiddenItems.Count -gt 0) {
        $examples = ($forbiddenItems | Select-Object -First 5 -ExpandProperty FullName) -join "; "
        throw "便携目录包含禁止的测试、迁移或缓存内容：$examples"
    }

    Compress-Archive -LiteralPath $portableDirectory -DestinationPath $zipPath -CompressionLevel Optimal
    Assert-RequiredFile -Path $zipPath -Description "便携版 ZIP"

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $archiveExecutable = $archive.Entries | Where-Object {
            $_.FullName.Replace("\", "/") -eq "EbookToolbox/EbookToolbox.exe"
        } | Select-Object -First 1
        if ($null -eq $archiveExecutable) {
            throw "便携版 ZIP 未包含顶层 EbookToolbox/EbookToolbox.exe。"
        }
    }
    finally {
        $archive.Dispose()
    }

    $portableBytes = (
        Get-ChildItem -LiteralPath $portableDirectory -Recurse -File |
            Measure-Object -Property Length -Sum
    ).Sum
    if ($null -eq $portableBytes) {
        $portableBytes = 0
    }
    $executableBytes = (Get-Item -LiteralPath $executablePath).Length
    $zipBytes = (Get-Item -LiteralPath $zipPath).Length

    Write-Host ""
    Write-Host "Windows 便携版构建成功。" -ForegroundColor Green
    Write-Host (
        "EXE：{0}（{1} 字节，{2:N2} MB）" -f
        $executablePath,
        $executableBytes,
        ($executableBytes / 1MB)
    )
    Write-Host (
        "便携目录：{0}（{1} 字节，{2:N2} MB）" -f
        $portableDirectory,
        $portableBytes,
        ($portableBytes / 1MB)
    )
    Write-Host (
        "ZIP：{0}（{1} 字节，{2:N2} MB）" -f
        $zipPath,
        $zipBytes,
        ($zipBytes / 1MB)
    )
    exit 0
}
catch {
    Write-Error -Message ("Windows 便携版构建失败：{0}" -f $_.Exception.Message) `
        -ErrorAction Continue
    exit 1
}
