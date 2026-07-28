# Meta User Feedback

本檔案記錄使用者對本專案（scholarship-monitor）協作方式的偏好與回饋。**僅限本專案**，
不與其他專案（例如使用者的 Android 專案 HabitTimeline）共用——見下方「各專案獨立維護
知識庫」記錄。

---

## [2026-07-05] 各專案獨立維護知識庫（Meta_Dev_Knowledge.md / Meta_User_Feedback.md）

**決策**：`Meta_Dev_Knowledge.md`、`Meta_User_Feedback.md` 採「每個專案各自維護一份」，
不做跨專案共用。

**背景**：使用者的 userPreferences 治理範本原本是為 Android 專案（HabitTimeline）設計，
其中提到的 `Meta_Dev_Knowledge.md`/`Meta_User_Feedback.md` 路徑指向 `C:\Projects\`
根目錄（`C:\Projects\Meta_Dev_Knowledge.md`、`C:\Projects\Meta_User_Feedback.md`），
這兩份檔案實際內容是 HabitTimeline 專案的知識庫（Room DB、Compose、AlarmManager 等），
與本專案（Node.js + Discord Bot 獎學金監測系統）完全無關。曾有一輪任務的 PRE 指示要求
「Consult @C:\Projects\Meta_Dev_Knowledge.md」，經檢查後發現這是路徑上的混淆——治理範本
是跨專案共用的通用結構，但範本裡提到的「Meta 檔案路徑」不應該跟著範本一起共用，
每個專案要有自己獨立的一份。

**核實結果**：檢查 `C:\Projects\Meta_Dev_Knowledge.md`、`C:\Projects\Meta_User_Feedback.md`
全文內容，確認**沒有**被本專案的任何一輪任務誤寫入過（搜尋「scholarship」「獎學金」
「discordBot」「exclude_terms」「ruleEngine」等本專案關鍵字，全部 0 筆命中）——過去
所有輪次的 `Meta_Dev_Knowledge.md` 寫入動作，實際上都正確寫在本專案內部的
`C:\Projects\scholarship-monitor\scholarship-monitor\Meta_Dev_Knowledge.md`，沒有
污染到 HabitTimeline 專案的知識庫。

**往後規則**：本專案的 `Meta_Dev_Knowledge.md`/`Meta_User_Feedback.md` 絕對路徑固定為：
- `C:\Projects\scholarship-monitor\scholarship-monitor\Meta_Dev_Knowledge.md`
- `C:\Projects\scholarship-monitor\scholarship-monitor\Meta_User_Feedback.md`

未來任何任務指示如果又出現指向 `C:\Projects\` 根目錄的 Meta 檔案路徑，應視為治理範本
本身沿用了 Android 專案的舊路徑設定，需要主動核對並修正到本專案內部路徑，不得照單全收。

---

## [2026-07-05] AI 使用限制的解除需要逐次明確授權，且需要真的可用

**背景**：本專案從第一輪開始就明確設定「不得引入外部 AI API、不得引入本地常駐 AI
模型」的硬性限制。使用者於本輪明確決定針對「來源自動發現」這一項功能解除限制，
但過程中出現一次「宣稱已完成 API key 設定」但實際 `.env` 裡沒有對應金鑰的落差
（詳見對應輪次的報告），經確認後使用者才補上真實金鑰。

**教訓**：使用者若表示「已經完成某項前置設定」，仍應實際查證（讀取 `.env`、跑最小
可行的連線測試），不能只憑任務描述的文字宣稱就假設已經就緒——即使使用者明確授權
解除限制，「授權」與「前置條件真的具備」是兩件事，需要分開驗證。實測後也發現金鑰
存在有效性（`ListModels` 可正常呼叫），但仍可能卡在額度/帳單設定（`generateContent`
回報 429 quota exceeded），代表「key 存在」「key 有效（能通過驗證）」「key 有配額可以
實際使用」是三個層次，都要個別確認，不能因為前一項通過就跳過後面的檢查。
