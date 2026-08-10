@echo off
setlocal EnableExtensions DisableDelayedExpansion

if /i "%~1"=="-h" goto help
if /i "%~1"=="--help" goto help
if /i "%~1"=="help" goto help

set "GIT_AUTHOR_NAME=OpenCode"
set "GIT_AUTHOR_EMAIL=noreply@opencode.ai"
set "GIT_COMMITTER_NAME=OpenCode"
set "GIT_COMMITTER_EMAIL=noreply@opencode.ai"
git commit %*
set "_COMMIT_EXIT=%ERRORLEVEL%"
endlocal & exit /b %_COMMIT_EXIT%

:help
echo usage: oc-commit ^<git commit args^>
echo.
echo Direct git commit replacement that records OpenCode as author and committer.
echo Pass normal git commit arguments directly; no wrapper-specific flags are needed.
echo.
echo Examples:
echo   oc-commit -m "subject"
echo   oc-commit -m "subject" -m "body"
echo   oc-commit -F C:\path\to\message
endlocal & exit /b 0
