@echo off
rem MGS1 Undub Studio launcher (Windows).
rem Creates a virtualenv on first run, installs/updates dependencies only when
rem they change, then starts the GUI. Arguments are passed through, e.g.:
rem   launch.bat --host 0.0.0.0 --port 9000
setlocal
cd /d "%~dp0"

set PY=python
where %PY% >nul 2>nul
if errorlevel 1 (
    set PY=py -3
    where py >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python not found. Install Python 3.10+ from python.org first.
        pause
        exit /b 1
    )
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo ERROR: Python 3.10+ is required.
    pause
    exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo First run - creating virtualenv in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 ( echo ERROR: could not create virtualenv. & pause & exit /b 1 )
)

set REQS=gui\requirements.txt
set STAMP=.venv\.requirements-stamp
fc /b "%REQS%" "%STAMP%" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies ...
    .venv\Scripts\python -m pip install --quiet --upgrade pip
    .venv\Scripts\python -m pip install -r "%REQS%"
    if errorlevel 1 ( echo ERROR: dependency install failed. & pause & exit /b 1 )
    copy /y "%REQS%" "%STAMP%" >nul
)

.venv\Scripts\python gui\app.py %*
if errorlevel 1 pause
