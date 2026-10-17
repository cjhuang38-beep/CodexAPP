# 驗證紀錄與 Windows 發布驗收

## 0.2.0 更新驗證（2026-10-18）

- 新增免 Cloud Console 的 Gmail 應用程式密碼登入、keyring 保存／恢復／登出；OAuth 保留為進階選項。
- 最新完整結果：**62 項通過、0 項失敗、0 項跳過**，使用 Linux Python 3.13.5＋Tk 8.6 與 Xorg dummy 真正建立桌面視窗；Ruff 全部通過。
- 新增 17 項 IMAP 核心測試：16 碼格式／空格正規化、TLS 憑證驗證、EXAMINE 唯讀、BODY.PEEK 僅正文、巢狀 MIME、中文進階查詢、Gmail ID／thread ID、日期與已讀解析、55 封跨批次及上限、去重、取消、部分失敗重試、keyring 保存／還原、拒絕不安全 keyring、撤銷密碼、另一帳號或同帳號既有有效憑證保留、網路錯誤保留憑證、拒絕 CSV ID 的 IMAP 命令注入／超出範圍識別。
- 新增 3 項實際 UI 測試：IMAP 登入／重新連線／登出不呼叫 OAuth；登入視窗秘密欄位輸入後遮罩、連線按鈕未超出視窗、取消不保存密碼；UID 後備識別合併時保留已存在的人工修改。
- `docs/imap-login.png` 為實際執行的新登入視窗截圖，沒有輸入任何真實帳號或秘密。
- IMAP 協定測試使用模擬回應，**尚未使用真實 Gmail 應用程式密碼，也未完成 Windows 新登入流程驗收**。support.google.com 官方說明仍遭環境代理 403，帳號資格以 Google 實際頁面為準。
- Windows 發布前，除原本清單外，需測試實際 IMAP 登入、重啟恢復、密碼撤銷／重建、學校帳號政策、超過 50 封、附件未下載、執行前後未讀及標籤不變。

## 原始版本驗證（2026-10-17，Linux／Python 3.12.14 與 3.13.5）

- 最新完整結果：**42 項通過、0 項失敗、0 項跳過**（啟用虛擬桌面）；Ruff 全部通過。
- 已安裝並鎖定依賴；`uv.lock` 及 pip hashes lockfiles 已生成。
- `python -m compileall -q gmail_summary` 可載入全部模組。
- 核心自動化測試：台北日期含結束日、查詢驗證、Gmail 三頁搜尋／去重／上限、完整正文請求、取消、讀取部分失敗、同主旨不同信件 ID、帳號隔離、人工修改、CSV BOM／引號／逗號／換行／公式注入／匯入無正文、設定秘密界線、keyring 明文後端拒絕、憑證更新成功／撤銷／暫時斷線、scope 檢查、缺 OAuth 設定、Demo 分析部分失敗與重試、queue、AI 傳送範圍／期限引句／正文截斷。
- 透過 Xorg dummy 虛擬桌面啟動真實 CustomTkinter UI，執行 Demo 搜尋、重複搜尋、全選分析、部分失敗重試、人工修改、篩選、完整／部分匯出、匯入、取消、關閉保存提示與主執行緒事件檢查。
- 深／淺模式切換、1100×740 視窗縮放在 Linux 虛擬桌面完成。
- 便攜 Python 3.12 的 Tk 9 僅能完成操作測試，CJK 顯示有問題；改用系統 Python 3.13.5＋Tk 8.6 後，重新執行全部測試並實際檢視繁體中文 Demo 畫面（`docs/demo.png`）。
- 已提供 Windows GitHub Actions 測試及 onedir artifact 工作流程；本次僅建立檔案，尚未在 GitHub 執行。
- Gmail 與 AI 的連線測試使用 Mock／固定回應；不宣稱為真實帳號端到端測試。

以實際最後一輪 `pytest` 輸出的數量與結果為準；沒有顯示器時 UI 測試會 skip，不可計為通過。

## 尚未驗證

- 真實 Windows 10／11 的 Tk 視窗、系統瀏覽器、100／125／150／200% DPI、跨螢幕縮放、繁體中文輸入法、Windows Credential Manager、CSV Excel 實際開啟。
- Windows `start.bat`、`build_windows.bat`、PyInstaller onedir／onefile 的原生執行；Linux 不產生假的 Windows exe。
- 真實 Google Desktop client、同意畫面、Gmail 讀信、真實 refresh token／撤銷與限額。
- 真實 AI 模型、API key、付費配額與供應商資料保留行為。
- Google 官方網頁現行政策查核（環境代理 403）、OAuth 驗證、安全評估、Google 正式公開發布。

## 發布前 Windows 人工驗收

1. 全新標準權限 Windows 使用者，安裝 Python 3.12 並執行 `start.bat --demo`；再次啟動可沿用依賴。
2. Demo：日期／寄件者／主旨／未讀篩選；全選、多選、勾選；執行分析、取消、部分失敗後重試；確認未改 Gmail 或呼叫真實模型。
3. 調整視窗及 DPI、深淺模式；確認 Treeview、中文、捲軸、按鈕與摘要詳細區可用。
4. 人工修改分類／摘要／待辦／期限，執行分類新增／改名／刪除，確認匯出有修改結果；用 Excel 檢查 BOM、換行、逗號、引號，以及 `=、+、-、@` 開頭文字沒有被當成公式。
5. CSV 匯入後查看及編輯；缺正文不得分析。切換不同帳號、登出、關閉的保存／捨棄／取消均正確。
6. 由開發者預先部署 OAuth 設定；登入、拒絕 Gmail scope、重啟自動恢復、access token 更新、撤銷授權、暫時斷線重試；確認不收密碼且 Token 僅見於 Credential Manager。
7. 實際 Gmail 取得超過 100 封，檢查分頁；巢狀 MIME／純文字／HTML／正文附件限制；比較執行前後未讀、標籤等未變動。
8. 設定真實模型；檢查傳送確認與範圍；測試模型錯誤、缺額度、無效 key、取消、重試，以及日期／金額／人物不被補造。人工檢查繁體中文摘要與待辦品質。
9. 執行 onedir 包裝並在**未安裝 Python**的乾淨 Windows 機測試 Demo 與真實登入。檢查不含開發機秘密、Token、API key、信件或設定；由安裝程式預先提供 desktop client metadata。
10. onedir 驗收後再產生 onefile，重複離線 Demo、OAuth 設定路徑、keyring、中文及防毒／簽章驗收。
