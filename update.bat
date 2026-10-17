@echo off
rem Update ComfyUI on the running Colab runtime to the latest release. Custom nodes, settings and workflows are kept.
rem To choose a version, run ./update.sh v0.3.60 in WSL instead.
setlocal
chcp 65001 >nul
set "DISTRO_OPT="
if defined COMFY_COLAB_WSL_DISTRO set "DISTRO_OPT=-d %COMFY_COLAB_WSL_DISTRO%"
wsl.exe %DISTRO_OPT% --cd "%~dp0." -- bash -lc "bash ./update.sh"
echo.
pause
