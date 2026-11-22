@echo off
rem Authorize the Drive API with your own OAuth client again. Pass revoke to revoke it.
setlocal
chcp 65001 >nul
set "DISTRO_OPT="
if defined COMFY_COLAB_WSL_DISTRO set "DISTRO_OPT=-d %COMFY_COLAB_WSL_DISTRO%"
title Drive API authorization
wsl.exe %DISTRO_OPT% --cd "%~dp0." -- bash -lc "bash ./drive-auth.sh %1"
echo.
pause
