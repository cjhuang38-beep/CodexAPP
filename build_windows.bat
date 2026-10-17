@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --require-hashes -r requirements-dev.lock
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto :fail
set "GMAIL_SUMMARY_ONEFILE=0"
if /i "%~1"=="onefile" set "GMAIL_SUMMARY_ONEFILE=1"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean GmailSummary.spec
if errorlevel 1 goto :fail
echo.
echo 打包完成。OAuth 用戶端設定未放入 exe；請依 docs\OAUTH.md 由開發者安裝到程式旁。
echo 發布前請完成 docs\VALIDATION.md 的 Windows 驗收。
popd
exit /b 0
:fail
echo 測試或打包失敗，請檢查上方錯誤。
pause
popd
exit /b 1
