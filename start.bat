@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto :fail
)
rem Install on first use, or when the hash-locked dependency file changes.
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'requirements.lock').Hash"') do set "REQ_HASH=%%H"
if not defined REQ_HASH goto :fail
set "OLD_HASH="
if exist ".venv\.requirements-sha256" set /p OLD_HASH=<".venv\.requirements-sha256"
if not "%REQ_HASH%"=="%OLD_HASH%" (
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --require-hashes -r requirements.lock
    if errorlevel 1 goto :fail
    >".venv\.requirements-sha256" echo %REQ_HASH%
)
".venv\Scripts\python.exe" -m gmail_summary %*
if errorlevel 1 goto :fail
popd
exit /b 0
:fail
echo.
echo 啟動失敗。請確認已安裝 Python 3.12（含 Tcl/Tk 與 py launcher），並檢查上方錯誤。
pause
popd
exit /b 1
