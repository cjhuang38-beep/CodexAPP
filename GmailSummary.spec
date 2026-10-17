# Run on Windows: python -m PyInstaller --noconfirm --clean GmailSummary.spec
# Default: onedir. Optional: GMAIL_SUMMARY_ONEFILE=1.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

if os.name != "nt":
    raise SystemExit("Windows exe 必須在 Windows 上打包；不可宣稱 Linux 產物為 Windows exe。")

onefile = os.environ.get("GMAIL_SUMMARY_ONEFILE") == "1"
root = Path(SPECPATH)
# Only explicit library resources; never collect the repository, config, keyring,
# environment, oauth_client.json, credentials.json, exported CSV, or user data.
datas = collect_data_files("customtkinter")
datas += collect_data_files("tzdata")
datas += collect_data_files("googleapiclient", includes=["discovery_cache/documents/gmail.v1.json"])
for name in ("keyring", "google-api-python-client", "google-auth", "google-auth-oauthlib"):
    datas += copy_metadata(name)
hiddenimports = collect_submodules("keyring.backends") + ["google.auth.transport.requests", "PIL._tkinter_finder"]
a = Analysis(
    [str(root / "desktop_entry.py")], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "ruff", "keyrings.alt", "sqlite3"], noarchive=False,
)
pyz = PYZ(a.pure)
if onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="GmailSummary", debug=False,
              strip=False, upx=False, console=False, disable_windowed_traceback=True)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GmailSummary", debug=False,
              strip=False, upx=False, console=False, disable_windowed_traceback=True)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GmailSummary")
