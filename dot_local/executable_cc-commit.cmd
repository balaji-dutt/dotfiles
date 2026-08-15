@echo off
setlocal EnableExtensions DisableDelayedExpansion
>&2 echo ERROR: cc-commit.cmd cannot safely forward commit messages through cmd.exe.
>&2 echo Run cc-commit from PowerShell so the adjacent cc-commit.ps1 wrapper is used.
endlocal & exit /b 64
