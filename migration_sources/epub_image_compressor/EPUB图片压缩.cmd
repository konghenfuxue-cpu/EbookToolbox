@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title EPUB Image Shrink

where py >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3，并勾选 Add Python to PATH。
    goto :end
)

py -3 -c "import PIL" >nul 2>nul
if errorlevel 1 (
    echo 正在安装所需组件 Pillow...
    py -3 -m pip install --user Pillow
    if errorlevel 1 (
        echo [错误] Pillow 安装失败。请联网后重试。
        goto :end
    )
)

if not exist "%~dp0epub_image_shrink.py" (
    echo [错误] 找不到 epub_image_shrink.py，请将 CMD 和 Python 脚本放在同一文件夹。
    goto :end
)

if "%~1"=="" (
    py -3 "%~dp0epub_image_shrink.py"
) else (
    py -3 "%~dp0epub_image_shrink.py" "%~1"
)

:end
echo.
pause
endlocal
