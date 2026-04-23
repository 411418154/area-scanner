@echo off
setlocal enabledelayedexpansion

REM One-click upload helper for Windows CMD.
REM Usage:
REM   upload.bat
REM   upload.bat "fix: update gui flow"

set "MSG=%~1"
if "%MSG%"=="" set "MSG=update: auto upload"

echo [1/3] Staging changes...
git add .
if errorlevel 1 (
  echo [ERROR] git add failed.
  exit /b 1
)

echo [2/3] Creating commit...
git diff --cached --quiet
if not errorlevel 1 (
  git commit -m "%MSG%"
  if errorlevel 1 (
    echo [ERROR] git commit failed.
    exit /b 1
  )
) else (
  echo [INFO] No staged changes. Skipping commit.
)

echo [3/3] Pushing to remote...
git push
if errorlevel 1 (
  echo [ERROR] git push failed. Check remote/auth settings.
  exit /b 1
)

echo [DONE] Upload finished.
exit /b 0
