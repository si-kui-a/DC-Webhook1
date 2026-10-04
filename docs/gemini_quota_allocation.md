# GEMINI_API_KEY 配額盤點與重新分配(2026-07-31)

## 背景

`GEMINI_API_KEY` 是整個 ip 專案共用同一把,免費層配額實測為
**每日20次**(`gemini-3.6-flash`,2026-07-31用真實請求測出 HTTP 429
`RESOURCE_EXHAUSTED`,quotaId `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
不是理論推測)。這份文件盤點目前有哪些功能在耗用這把配額、估算每日呼叫量、
並提出重新分配提案。

## 現況盤點(全部呼叫點)

| 功能(ai_insight.py函式) | 用途 | 觸發排程 | 估算呼叫量/天 |
|---|---|---|---|
| `get_translation_and_sentiment` | fed(Fed新聞)英文→中文翻譯+利多利空判斷 | IntelPusher-FedDaily,平日09:00 | **不封頂**,每則新抓到的新聞各呼叫一次(0~數則不等) |
| `get_translation_and_sentiment` | tsmc(台積電新聞)同上 | 併入 tsmc_digest 前置抓取 | **不封頂**,同上 |
| `build_channel_digest` | 7個晚間彙整頻道各自的敘事統整(us_stock/crypto/macro_tech/geopolitics/tsmc/cbc/semi_supply_chain) | 全部20:00 | **7**(每頻道每次執行固定1次,不隨當日項目數增加) |
| `build_meta_summary` | 2個大總結頻道(tw_stock_meta/crypto_meta) | 20:30 | **2** |
| `build_trade_decision` | tw_stock_portfolio 交易決策 | 平日14:00 | **1**(平日) |
| `build_trade_decision` | crypto_futures_portfolio 交易決策 | **已改事件觸發**(2026-07-31,見check_triggers.py) | 目標 **0~3**(視數值面/消息面/市場面觸發頻率,保底1週至少1次) |
| `build_trade_decision` | crypto_discretionary_portfolio 交易決策 | **已改事件觸發**(2026-07-31,同一支check_triggers.py) | 目標 **0~3**,同上 |
| `classify_internships` | 實習職缺語意消歧 | 每天09:00,僅關鍵字命中才呼叫 | **0~1**(已刻意做成有上限) |

**resume_matcher.py/resume_bot.py** 也用同一把key,但屬使用者主動觸發(CLI/Discord互動),不是排程批次,不計入下面的「每日固定消耗」估算(用量完全取決於使用頻率,設計上本來就低成本)。

## 問題(發現時的狀態,已全部處理完成,見下方「已完成」)

1. **`crypto_discretionary_portfolio` 每小時呼叫,24次/天,單獨就超過整包20次配額。**
   這是這次盤點最大的發現——先前只處理了`crypto_futures_portfolio`(當時判斷依據是
   「這是全專案呼叫量最高的地方」),但實際查排程才發現`crypto_discretionary_portfolio`
   用的是同一組`-RepetitionInterval 1小時`設定,並沒有真的比較低頻率,先前
   「呼叫頻率本來就低,非本次優化目標」的假設是錯的。
2. **fed/tsmc翻譯沒有上限**,理論上如果單次執行抓到多篇新文章,單次執行就可能打完
   一整天的配額。
3. 兩項加總的worst case（24 + 不封頂的fed/tsmc + 其餘固定10)遠超過20次/天,
   代表目前**每天很大比例的呼叫都在額度用盡後靜默失敗**,退回各自的降級路徑
   (英文原文摘要/純規則分類/不呼叫AI直接hold)——功能上安全(不會半套執行),
   但AI原本要提供的判斷力大部分時間沒有真的生效,使用者可能誤以為在運作。

## 已完成(2026-07-31,使用者確認後全部套用)

| 項目 | 原狀 | 處理結果 |
|---|---|---|
| `crypto_discretionary_portfolio` | 每小時(24/天) | 已改用`check_triggers.py`事件觸發(跟`crypto_futures_portfolio`同一支腳本,各自獨立一筆`portfolio_trigger`)。自我複盤依據：現貨/不可槓桿風險本來就比合約低,結構上完全適用同一套市場面/消息面資料來源,拉開評估頻率更站得住腳 |
| fed/tsmc翻譯 | 不封頂 | 已加上限(`main.py::TRANSLATION_CAP_PER_RUN = 3`),單次執行最多翻譯前3則最新文章,其餘退回英文抽取式摘要 |
| 7個晚間彙整頻道 | 各1次/天,共7 | 維持不變(AI真正發揮敘事整合價值的地方,不建議砍) |
| 2個大總結頻道 | 各1次/天,共2 | 維持不變,同上 |
| `tw_stock_portfolio` | 平日1次/天 | 維持不變,頻率已經很低 |
| 實習語意消歧 | ≤1次/天 | 維持不變,已經是設計最保守的一個 |

**重新分配後預估**：crypto_futures(~2) + crypto_discretionary(~2) +
tw_stock(1) + 彙整頻道(7) + 大總結(2) + 實習(1) + fed/tsmc(封頂後~3) ≈
**18/天**,落在20次配額內,留一點餘裕。

排程異動需要在提權PowerShell執行`scripts/apply_event_triggered_crypto.ps1`
才會真正生效(見該腳本註解),schema/程式碼異動本身已經全部完成並測試過。

## 2026-07-31追加：實測發現的落差與後續修正

上面的估算(~18/天)是理論值，實際盤點`work/activity.log`裡「額度用盡」
的每日次數發現：2026-07-29有2次、2026-07-30有9次、**2026-07-31當天
飆到67次**——原因是`crypto_futures_portfolio`/`crypto_discretionary_
portfolio`當天剛啟用事件觸發，`check_triggers.py`的「首次執行」邏輯
沒有退避機制：AI失敗時觸發條件不會被寫入，下一個20分鐘週期又判定成
「首次執行」再打一次，一個上午重試超過14次。已修正(加2小時退避，見
`check_triggers.py`)，預期明天起額度消耗會回到接近~18/天的估算值。

同時`classify_internships`(實習語意消歧)已於2026-07-31完全移除，改用
純規則(`internship_util._is_semantic_noise()`)，原本估算的「實習0~1」
現在是穩定的**0**，重新分配後預估降到約**17/天**。

## 2026-10-04 更新：模擬持倉完全不用 AI

上表三列 `build_trade_decision` 與 AI 安全閥（`assess_stop_loss`）都已移除：tw_stock 從
2026-08-06 起是規則式定期定額，兩個加密貨幣帳戶從 2026-10-04 起由 `rule_engine.py` 的規則
決策（含固定停損與強制平倉模擬），`check_triggers.py` 改成每輪直接跑規則。模擬持倉的 AI
用量為 **0**；彙整頻道、大總結與翻譯不受影響。
