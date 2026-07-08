@echo off
rem Health check: is everything the overlay needs actually in place?
rem Read-only and safe to run any time. Double-click me when something
rem looks wrong and read the FAIL/WARN lines.
rem NB: no %errorlevel% inside ( ) blocks -- cmd expands it at parse time.
cd /d "%~dp0"

if not exist python\python.exe goto :venvpython
python\python.exe tools\preflight.py %*
goto :done

:venvpython
if not exist .venv\Scripts\python.exe goto :syspython
.venv\Scripts\python.exe tools\preflight.py %*
goto :done

:syspython
where py >nul 2>nul
if errorlevel 1 goto :plain
py -3 tools\preflight.py %*
goto :done

:plain
python tools\preflight.py %*

:done
pause
