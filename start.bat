@echo off
rem Create a Colab runtime, start ComfyUI, forward the port and open it in the browser.
rem Keep this window open while you use ComfyUI. It holds the SSH tunnel and watches the Stop button in ComfyUI.
rem To use a non-default WSL distro, set COMFY_COLAB_WSL_DISTRO, for example to Ubuntu-24.04.
setlocal
chcp 65001 >nul
set "DISTRO_OPT="
if defined COMFY_COLAB_WSL_DISTRO set "DISTRO_OPT=-d %COMFY_COLAB_WSL_DISTRO%"
title ComfyUI on Colab
wsl.exe %DISTRO_OPT% --cd "%~dp0." -- bash -lc "bash ./up.sh"
echo.
pause
