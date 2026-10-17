@echo off
rem Show the state of the Colab runtime, ComfyUI and the local tunnel.
setlocal
chcp 65001 >nul
set "DISTRO_OPT="
if defined COMFY_COLAB_WSL_DISTRO set "DISTRO_OPT=-d %COMFY_COLAB_WSL_DISTRO%"
wsl.exe %DISTRO_OPT% --cd "%~dp0." -- bash -lc "bash ./status.sh"
echo.
pause
