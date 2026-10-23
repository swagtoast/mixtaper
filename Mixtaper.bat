@echo off
rem Avvia mixtaper dai sorgenti (Windows) / Runs mixtaper from source (Windows)
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo Installo uv / installing uv...
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
uv run --no-editable --python 3.12 mixtaper %*
pause
