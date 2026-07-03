@echo off
title Nuke + ComfyUI Launcher
echo ====================================================
echo             AI STUDIO NUKE LAUNCHER
echo ====================================================
echo.
echo [1] Local GPU (Tailscale Workstation)
echo [2] Cloud GPU (RunPod)
echo.

set /p target="Choose your render engine (1 or 2, or type local/runpod): "

if /I "%target%"=="2" goto runpod
if /I "%target%"=="runpod" goto runpod
goto local

:runpod
echo.
set /p COMFY_URL="Paste your RunPod proxy URL (e.g., https://xyz-8188.proxy.runpod.net): "
set COMFY_LABEL=RunPod
echo.
echo Targeting Cloud GPU...
goto launch

:local
echo.
echo Targeting Local Workstation...
set COMFY_URL=http://100.87.37.6:8188
set COMFY_LABEL=Local GPU
goto launch

:launch
echo Launching Nuke...
start "" "L:\Apps\Nuke17.0v1\Nuke17.0.exe" --nukex -V
exit