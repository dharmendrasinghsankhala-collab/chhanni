@echo off
setlocal EnableDelayedExpansion
title Chhanni AI pack setup
cd /d "%~dp0"
echo ==================================================
echo   Chhanni AI pack  (Whisper + CLIP, local GPU)
echo   ~3-4 GB download, ek baar. 15-30 minute lag sakte hain.
echo ==================================================
set "PY="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Content $env:APPDATA\Chhanni\install.json | ConvertFrom-Json).python"`) do set "PY=%%P"
if not defined PY (echo Pehle setup.bat chalaiye. & pause & exit /b 1)
echo Python: !PY!

echo [1/4] PyTorch (CUDA)...
"!PY!" -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 (echo PyTorch install failed & pause & exit /b 1)

echo [2/4] faster-whisper + CUDA libraries...
"!PY!" -m pip install --upgrade faster-whisper nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
if errorlevel 1 (echo faster-whisper install failed & pause & exit /b 1)

echo [3/4] CLIP...
"!PY!" -m pip install --upgrade open_clip_torch pillow
if errorlevel 1 (echo open_clip install failed & pause & exit /b 1)

echo [4/4] Models download + GPU test...
"!PY!" "%~dp0app\ai_selftest.py"

echo.
echo ==================================================
echo   AI pack ready. Chhanni app band karke dobara kholiye.
echo ==================================================
pause
