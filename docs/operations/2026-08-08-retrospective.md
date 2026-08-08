# 2026-08-08 操作檢討與反射性流程

## 已完成

- 轉錄批次已完成並歸檔；未把低可信逐字稿提升為方法論內容。
- `ops/run_task.ps1`、`ops/healthcheck.ps1` 已建立並接入 `CheckTriggers`、`ResumeBot`。
- 23 個排程曾完成測試性觸發；原有每日排程未改變。
- AI fallback 已上線：Gemini 不可用時採安全 HOLD，不開倉、不平倉。
- 投資組合推播已改為動作／資產／持倉分區格式。
- 通用控制層模板已納入 main。
- UTF-8 與離線 Embed 測試已建立；CI PR #31 待審查。

## 未完成與評估

| 項目 | 狀態 | 評估 | 下一步 |
|---|---|---|---|
| PR #31 CI | 待合併 | 低風險、高價值 | 審查後合併 |
| `build_portfolio_embed` 重複定義 | 未處理 | 中風險、非立即阻塞 | 先建立 UTF-8 安全分支，再最小刪除 |
| 正式分支同步 main | 未處理 | 高風險；目前 ahead 5 / behind 9 | 只 cherry-pick 必要 commit |
| 新聞／批次頻道格式 | 未全面套用 | 需依語義分開設計 | 先做離線 sample，再逐頻道 |
| `RealestateReport` | 未解決 | 曾回傳 exit code 2 | 先補 wrapper stderr 與 dry-run |
| Gemini 格式穩定性 | 降級可運作 | AI 不得成為核心依賴 | schema、timeout、cooldown |
| `ResumeBot` 生命週期 | 暫以排程維持 | 長期不可靠 | 評估 Windows Service/watchdog |
| 備份可還原性 | 未完成 | 目前只有日誌，未證明可還原 | 驗證遠端 SHA 並做還原演練 |

## 今日教訓

1. 先確認實際執行分支、commit 與排程 Action，再做修改。
2. 不把程序 exit code 當成業務成功；核心結果與 AI 結果必須分開。
3. 全量同時觸發不是有效穩定性測試，會放大 API、SQLite、Discord 競爭。
4. AI 是可選增強；核心流程必須在沒有 Gemini 時仍完成。
5. 測試觸發必須考慮 event_id 去重，避免重複推播。
6. Windows PowerShell 直接改含中文的 Python 檔案有編碼風險；優先使用 UTF-8-aware patch。
7. 發現 patch／測試失敗時，先回復到最後可編譯版本，不把半成品送入正式環境。
8. PR 應小範圍、單一目的；不要一次合併不相關功能。

## 每次執行前的反射性檢查

- [ ] 工作樹乾淨，確認目前 branch、HEAD、upstream。
- [ ] 確認目標檔案編碼為 UTF-8，禁止未指定編碼寫入。
- [ ] 先做離線／dry-run，再做正式推播。
- [ ] 確認 AI 關閉時核心仍可完成。
- [ ] 確認任務有 run_id、stdout、stderr、exit code 與業務狀態。
- [ ] 確認不會與既有排程重疊，並有去重策略。
- [ ] 修改排程前先匯出 XML 備份。
- [ ] 修改後檢查實際 Action、LastRun、LastResult 與日誌。
- [ ] 備份操作必須驗證遠端 SHA 或實際還原，不接受只有成功訊息。
- [ ] 未完成項目必須保留「未完成」標記，不得以推測代替證據。
