@echo off
rem Copy the outputs, close the tunnel and delete the Colab runtime.
setlocal
chcp 65001 >nul
set "DISTRO_OPT="
if defined COMFY_COLAB_WSL_DISTRO set "DISTRO_OPT=-d %COMFY_COLAB_WSL_DISTRO%"
wsl.exe %DISTRO_OPT% --cd "%~dp0." -- bash -lc "bash ./down.sh"
echo.
pause
