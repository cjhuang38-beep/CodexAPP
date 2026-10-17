# 開發者 OAuth 設定與 Google 發布要求

## 查核狀態（2026-10-17）

本次嘗試讀取下列 Google 官方網頁，環境代理全部回覆 **403 Forbidden**。因此下列發布說明是待開發者依官方現行政策再次核對的清單，**不宣稱本次已完成即時政策查核、Google 驗證或正式發布**。程式使用的已鎖版官方 `google-auth-oauthlib==1.2.3` 實作已在本機檢查：InstalledAppFlow 以系統瀏覽器登入、loopback 回呼、state 驗證、PKCE（明確啟用），不使用已淘汰的 OOB 手動貼碼流程。授權範圍也可在已安裝的 Gmail API 官方 discovery 文件中確認。

必讀的官方文件：

- [OAuth 2.0 for Mobile & Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [OAuth restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [OAuth 2.0 policies](https://developers.google.com/identity/protocols/oauth2/policies)
- [OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)
- [Workspace API user data / developer policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)
- [Gmail search and filtering](https://developers.google.com/workspace/gmail/api/guides/filtering)

若在此受限雲端環境繼續查核，需先在環境網路設定允許 `developers.google.com`。Windows 端也需可用的系統瀏覽器與 loopback 連線。

## 由開發者準備，非終端使用者操作

1. 在開發者擁有的 Google Cloud 專案啟用 Gmail API。
2. 設定 Google Auth Platform／OAuth 同意畫面的應用程式名稱、支援聯絡方式、品牌、受眾與必要政策連結。
3. 建立 **Desktop app** 類型 OAuth 用戶端。不要使用 Web app、服務帳號、裝置碼或自行偽造密碼畫面。
4. 在資料存取設定僅要求 `https://www.googleapis.com/auth/gmail.readonly`。這已允許讀取正文、訊息中繼資料與 Gmail profile（用來顯示目前 Email）；不另外要求 `mail.google.com`、`gmail.modify`、寄信或刪信權限，也沒有額外要求 OpenID profile scope。
5. 下載該桌面用戶端的 installed 格式設定，在開發機以 **`oauth_client.json`** 放於專案根目錄，或設定 `GMAIL_SUMMARY_OAUTH_CLIENT` 指向由開發者管理的路徑。兩者自動讀取，不提供終端使用者匯入按鈕。
6. 安裝版由開發者／安裝程式將設定預先部署於 `GmailSummary.exe` 旁的 `oauth_client.json`。onefile 也從 exe 旁讀取，不依賴暫存解壓目錄。設定不嵌入 exe，正式 OAuth 設定已加入 Git 忽略。
7. `config/oauth_client.example.json` 只是無秘密格式範例，不能拿佔位 client ID 登入；請使用自己實際建立的 Desktop client 完整設定。不要把 Web／服務帳號私人金鑰、Token、模型 API key 放進這個檔案。

Google 桌面 OAuth 用戶端屬於 public client，分發至終端機的 client metadata（Google desktop JSON 可能包含名稱為 `client_secret` 的欄位）不能被當成可靠的機密。App 需要它來依官方 Desktop 流程交換 token，但不應把開發者真正的服務端秘密嵌入桌面程式。若組織政策不允許分發該 desktop metadata，應先另行設計經審查的授權架構，不能藉混淆 exe 宣稱保密。此專案沒有內建任何實際 client ID／secret。

使用者只需按「使用 Google 帳號登入」，在系統預設瀏覽器選擇帳號並同意 Gmail 唯讀權限。Google 登入本身不代表可讀信；只在成功取得對應 Gmail scope 後繼續。

## 授權與憑證行為

- 官方 InstalledAppFlow：`host=127.0.0.1`、`port=0`，使用隨機本機 loopback 連接埠。回呼只綁 loopback，不綁所有介面；系統瀏覽器打開 Google 官方授權頁。
- Google 同意流程同一次要求 `gmail.readonly`、offline refresh token，以及帳號選擇。Gmail 授權未勾選或遭拒絕會停止，不虛報連線成功。
- 限定 `auth_uri=https://accounts.google.com/o/oauth2/auth`、`token_uri=https://oauth2.googleapis.com/token`，避免錯誤用戶端設定把憑證導向別處。
- Token JSON（包含 refresh token）只保存在 OS keyring 的 `GmailSummaryDesktop / google-oauth-current`，不寫入 `token.json`。
- 重啟時從 keyring 恢復；access token 過期自動更新。`invalid_grant` 等不可更新授權會移除本機 token 並要求登入。暫時網路或可重試伺服器錯誤保留 refresh token，不冒然要求新憑證。
- 登出清除本機 Token；需全面撤銷授權，使用者另到 Google 帳號第三方連線管理頁移除本 App。
- 本 App 不使用嵌入式瀏覽器、不收 Google 密碼、不讀瀏覽器 Cookie、不跳過同意畫面。
- 已取消的搜尋／分析會停止後續工作；正在等待的官方瀏覽器授權最多 180 秒才釋放 loopback server。取消後不保存此次授權結果。網路呼叫不使用不安全的強制終止執行緒。

## 測試／正式公開發行檢查清單

以下都必須由開發者依發布當下官方文件確認，不能因 Demo 通過就視為已完成：

- External／Testing 受眾：加入測試使用者，確認 Google 對測試帳號數、未驗證警示與 refresh token 生命週期的限制。Gmail scope 在外部 Testing 模式常有約七天的 refresh token 到期規則；以當下官方文件為準。
- `gmail.readonly` 是 **restricted scope**。外部公開應用通常需進行品牌／身分及 restricted-scope OAuth 驗證；組織內部或其他例外是否適用，需由 Google 政策判定。
- 建立公開且符合實際行為的首頁、隱私政策、支援／刪除資料方式、驗證網域及授權示範影片／用途說明。清楚描述唯讀讀信與使用者主動送至 AI 的資料範圍。
- 本 App 可將 Gmail 正文傳到使用者設定的外部 AI 服務。這是重要資料流，**不能因為是桌面 App 或不使用資料庫，就自行宣稱免安全評估**。restricted data 被送到或儲存在外部伺服器時，可能需要 Google 指定安全評估；應按完整架構向 Google 確認。
- 確認 Workspace／Gmail API Limited Use 規範、AI 處理用途、第三方傳輸條件、供應商保留／刪除政策，及不得將 Workspace 使用者資料拿來訓練一般化模型的限制。介面一次確認不等於已符合全部政策；正式發行需確認供應商合約與實際資料用途。
- OpenAI 相容網址是使用者指定的 HTTPS 目的地。正式發行方需決定允許的供應商政策並完成必要評估；此專案不為任意第三方網址背書。
- 正式測試 scope 實際授予、拒絕授權、帳號切換、refresh、撤銷、403 API 未啟用及限額情況。
- Windows Credential Manager 使用者設定檔必須可用；對安全 keyring 的拒絕不可透過明文檔案或關閉 TLS 驗證繞過。
- 針對 exe 做 Windows 程式碼簽章、發行掃描、onboarding／解除安裝與隱私驗收。不得把任何私人憑證、使用者 Token、模型 key、匯出 CSV 或使用者資料打包。

本次沒有實際 OAuth 用戶端、Google 測試帳號或模型 API key，也未完成 Google 驗證。正式公開發行仍由專案擁有者完成以上工作。
