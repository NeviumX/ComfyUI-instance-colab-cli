@echo off
rem Download a Hugging Face or Civitai model to the running Colab runtime, and optionally to Google Drive.
rem The window closes by itself when the download succeeds, and stays open when it fails.
setlocal
chcp 65001 >nul
set "DISTRO_OPT="
if defined COMFY_COLAB_WSL_DISTRO set "DISTRO_OPT=-d %COMFY_COLAB_WSL_DISTRO%"
title Model download
wsl.exe %DISTRO_OPT% --cd "%~dp0." -- bash -lc "bash ./model-download.sh"
if errorlevel 1 (
    echo.
    pause
)
