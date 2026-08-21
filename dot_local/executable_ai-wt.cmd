@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "_AI_WT_PYTHON="
set "_AI_WT_PYTHON_ARGS="
set "_AI_WT_SCRIPT=%~dp0ai-wt.py"
if not exist "%_AI_WT_SCRIPT%" (
  >&2 echo ai-wt: managed Python payload not found: %_AI_WT_SCRIPT%
  endlocal & exit /b 2
)

call :probe_py_launcher
if defined _AI_WT_PYTHON goto run
call :probe_python python3.exe
if defined _AI_WT_PYTHON goto run
call :probe_python python.exe
if defined _AI_WT_PYTHON goto run

>&2 echo ai-wt: Python 3.10 or newer was not found in PATH
>&2 echo ai-wt: install Python separately, then rerun ai-wt
endlocal & exit /b 2

:run
rem Percent expansion happens before this chain runs, so Python starts without launcher-private state.
set "_AI_WT_SCRIPT=" & set "_AI_WT_PYTHON=" & set "_AI_WT_PYTHON_ARGS=" & set "_AI_WT_EXIT=" & "%_AI_WT_PYTHON%" %_AI_WT_PYTHON_ARGS% "%_AI_WT_SCRIPT%" %*
set "_AI_WT_EXIT=%ERRORLEVEL%"
endlocal & exit /b %_AI_WT_EXIT%

:probe_py_launcher
for /f "delims=" %%I in ('where py.exe 2^>nul') do if not defined _AI_WT_PYTHON call :accept_python "%%I" "-3"
exit /b 0

:probe_python
for /f "delims=" %%I in ('where %~1 2^>nul') do if not defined _AI_WT_PYTHON call :accept_python "%%I" ""
exit /b 0

:accept_python
echo(%~1| findstr /i /c:"\Microsoft\WindowsApps\" >nul
if not errorlevel 1 exit /b 0
"%~1" %~2 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 exit /b 0
set "_AI_WT_PYTHON=%~1"
set "_AI_WT_PYTHON_ARGS=%~2"
exit /b 0
