@echo off
setlocal EnableExtensions DisableDelayedExpansion
>&2 echo ERROR: oc-commit.cmd cannot safely forward commit messages through cmd.exe.
>&2 echo Run oc-commit from PowerShell so the adjacent oc-commit.ps1 wrapper is used.
endlocal & exit /b 64
