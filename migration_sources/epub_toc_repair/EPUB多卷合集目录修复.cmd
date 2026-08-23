@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>nul
title EPUB Collection TOC Repair

set "SCRIPT=%~dp0epub_collection_toc.py"
set "EXITCODE=1"

echo ========================================
echo EPUB 多卷合集目录修复工具
echo ========================================
echo.

if not exist "%SCRIPT%" (
    echo [错误] 找不到 epub_collection_toc.py
    echo 请先完整解压 ZIP，并保持 CMD、PY、TXT 三个文件在同一文件夹。
    goto :END
)

where py.exe >nul 2>nul
if not errorlevel 1 goto :USE_PY

where python.exe >nul 2>nul
if not errorlevel 1 goto :USE_PYTHON

echo [错误] 未检测到 Python 3。
echo 请安装 Python 3，并勾选 Add Python to PATH。
echo 下载地址：https://www.python.org/downloads/
goto :END

:USE_PY
if "%~1"=="" (
    py.exe -3 "%SCRIPT%"
) else (
    py.exe -3 "%SCRIPT%" %*
)
set "EXITCODE=%ERRORLEVEL%"
goto :END

:USE_PYTHON
if "%~1"=="" (
    python.exe "%SCRIPT%"
) else (
    python.exe "%SCRIPT%" %*
)
set "EXITCODE=%ERRORLEVEL%"

:END
echo.
if "%EXITCODE%"=="0" (
    echo 工具已正常结束。
) else (
    echo 工具未能完成，请把上方错误文字或截图发给我。
)
echo.
pause
endlocal
