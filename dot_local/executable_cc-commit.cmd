@echo off
setlocal EnableExtensions DisableDelayedExpansion

if /i "%~1"=="-h" goto help
if /i "%~1"=="--help" goto help
if /i "%~1"=="help" goto help

set "GIT_AUTHOR_NAME=Claude"
set "GIT_AUTHOR_EMAIL=noreply@anthropic.com"
set "GIT_COMMITTER_NAME=Claude"
set "GIT_COMMITTER_EMAIL=noreply@anthropic.com"
git commit %*
set "_COMMIT_EXIT=%ERRORLEVEL%"
endlocal & exit /b %_COMMIT_EXIT%

:help
echo usage: cc-commit ^<git commit args^>
echo.
echo Direct git commit replacement that records Claude as author and committer.
echo Pass normal git commit arguments directly; no wrapper-specific flags are needed.
echo.
echo Examples:
echo   cc-commit -m "subject"
echo   cc-commit -m "subject" -m "body"
echo   cc-commit -F C:\path\to\message
endlocal & exit /b 0
