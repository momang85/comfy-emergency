@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
set "ROOT=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY_KIND="
set "PY_EXE="
set "CODE=1"

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PY_KIND=launcher"
        goto :launch
    )
)

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PY_KIND=exe"
        for /f "delims=" %%P in ('where python') do (
            if not defined PY_EXE set "PY_EXE=%%P"
        )
        goto :launch
    )
)

if exist "%ROOT%python_embeded\python.exe" (
    set "PY_KIND=exe"
    set "PY_EXE=%ROOT%python_embeded\python.exe"
    goto :launch
)
if exist "%ROOT%ComfyUI_windows_portable\python_embeded\python.exe" (
    set "PY_KIND=exe"
    set "PY_EXE=%ROOT%ComfyUI_windows_portable\python_embeded\python.exe"
    goto :launch
)
if exist "%ROOT%..\python_embeded\python.exe" (
    set "PY_KIND=exe"
    set "PY_EXE=%ROOT%..\python_embeded\python.exe"
    goto :launch
)
if exist "%ROOT%..\..\python_embeded\python.exe" (
    set "PY_KIND=exe"
    set "PY_EXE=%ROOT%..\..\python_embeded\python.exe"
    goto :launch
)

echo [错误] 没有找到可用的 Python。
echo.
echo 请安装 Python 3.8 或更高版本，或把本工具放进 ComfyUI 便携包目录。
echo 也可以手动运行：python comfy_er.py doctor --comfy "你的 ComfyUI 目录"
set "CODE=1"
goto :finish

:launch
if "%~1"=="" (
    if /I "%PY_KIND%"=="launcher" (
        py -3 "%ROOT%comfy_er.py" doctor
    ) else (
        "%PY_EXE%" "%ROOT%comfy_er.py" doctor
    )
) else (
    if /I "%PY_KIND%"=="launcher" (
        py -3 "%ROOT%comfy_er.py" %*
    ) else (
        "%PY_EXE%" "%ROOT%comfy_er.py" %*
    )
)
set "CODE=%ERRORLEVEL%"
goto :finish

:finish
echo.
echo [提示] 本窗口会停留，按任意键关闭。
pause >nul
exit /b %CODE%
