@echo off
setlocal EnableDelayedExpansion
title Chhanni 3 setup
cd /d "%~dp0"
echo ==================================================
echo   Chhanni 3 - setup  (app + Resolve + Premiere)
echo ==================================================

call :findpy
if not defined PY (
  echo Python nahi mila - install kar raha hoon ^(2-3 minute^)...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  call :findpy
)
if not defined PY (
  echo Python install nahi ho paya. python.org se Python 3.12 install karke setup.bat dobara chalaiye.
  pause & exit /b 1
)
echo Python: !PY!

echo [1/3] Python packages...
"!PY!" -m pip install --upgrade pip --quiet
"!PY!" -m pip install numpy opencv-python --quiet
if errorlevel 1 (echo pip install failed & pause & exit /b 1)
echo    [ok] numpy + opencv

echo [2/3] FFmpeg...
call :findff
if not defined FF (
  echo FFmpeg install kar raha hoon ^(NVIDIA GPU decoding wala full build^)...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  call :findff
)
if not defined FF (
  echo FFmpeg install nahi ho paya. gyan.dev/ffmpeg/builds se "full" build lekar PATH mein daaliye.
  pause & exit /b 1
)
>"%~dp0app\ffmpeg_path.txt" echo !FF!
echo    [ok] FFmpeg: !FF!

echo [3/3] App, Resolve panel, Premiere panel, shortcuts...
"!PY!" "%~dp0app\install_helper.py"

echo.
echo ==================================================
echo   Ho gaya!  Desktop par "Chhanni" icon se app kholiye.
echo   Aage ke updates app ke andar se: Settings ^> Updates (ye setup dobara nahi chalana)
echo   Resolve : Workspace ^> Scripts ^> Chhanni
echo   Premiere: Window ^> Extensions ^> Chhanni  (Premiere restart karein)
echo   AI (Whisper + CLIP): app mein "AI setup" ^> Install AI pack, ya setup_ai.bat
echo ==================================================
choice /c YN /n /m "Abhi Chhanni kholna hai? (Y/N) "
if errorlevel 2 exit /b 0
for %%W in ("!PY!") do set "PYW=%%~dpWpythonw.exe"
if not exist "!PYW!" set "PYW=!PY!"
start "" "!PYW!" "%LOCALAPPDATA%\Chhanni\app\Chhanni.pyw"
exit /b 0

:findpy
set "PY="
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
) do if not defined PY if exist %%P set "PY=%%~P"
if defined PY goto :eof
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%P"
if defined PY goto :eof
for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%P"
goto :eof

:findff
set "FF="
for /f "delims=" %%F in ('where ffmpeg 2^>nul') do if not defined FF set "FF=%%F"
if defined FF goto :eof
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "FF=%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"
if defined FF goto :eof
for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg.exe" 2^>nul') do if not defined FF set "FF=%%F"
goto :eof
