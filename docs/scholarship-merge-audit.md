---
name: scholarship-merge-audit
description: "File audit for scholarship-monitor → intel-pusher merge (2026-07-28)"
metadata:
  type: reference
---

# 獎學金合併檔案審計報告

**日期**: 2026-07-28
**來源**: `C:\Projects\scholarship-monitor\scholarship-monitor`
**目的**: 歸檔至 `C:\Projects\intel-pusher\legacy\scholarship-monitor` 後的檔案分類

---

## 可刪除者（已移植完成，不再需要）

這些檔案的功能已完全移植到 intel-pusher，可安全從 legacy 中移除：

| 檔案 | 已移植至 | 說明 |
|------|----------|------|
| `config/rules.json` | `config/scholarship_keywords.json` | 關鍵字權重 + threshold |
| `config/exclude_terms.json` | `config/scholarship_exclude.json` | 排除詞彙字典（含文件說明） |
| `config/profile.json` | `config/scholarship_profile.json` | 使用者個人條件設定 |
| `src/ruleEngine.js` | `scrapers/scholarship_util.py` | 關鍵字計分 + 排除規則（Python 版功能對等） |
| `src/crawler/sources/daad.js` | `scrapers/scholarship_daad.py` | DAAD 爬蟲 |
| `src/crawler/sources/efg.js` | `scrapers/scholarship_efg.py` | EFG 爬蟲 |
| `src/crawler/sources/moe.js` | `scrapers/scholarship_moe.py` | MOE 爬蟲 |
| `src/crawler/sources/thu.js` | `scrapers/scholarship_thu.py` | THU 爬蟲 |
| `src/crawler/index.js` | `main.py` → `run_scholarship()` | 爬蟲排程協調 |
| `src/crawler/base.js` | — | 爬蟲基底類別（Python 版為獨立函式） |
| `src/crawler/runner.js` | — | 爬蟲執行器（Python 版為 for 迴圈） |
| `src/index.js` | `main.py` | 入口點 |
| `src/scheduler.js` | `intelpusher-scholarship-admin.xml` | 排程（Windows Task Scheduler） |
| `src/db.js` | `db.py` | 資料庫存取層 |
| `data/db.json` | `data.db` | JSON 儲存 → SQLite |
| `data/runs.json` | `db.py` 的 run_log 表 | 執行紀錄 |
| `data/sources.json` | `db.py` 的 source 表 | 來源管理 |
| `data/crawl.log` | `work/activity.log` | 活動日誌 |
| `work/*.log` | — | 執行日誌（應留在 .gitignore） |
| `task.xml` | `intelpusher-scholarship-admin.xml` | Task Scheduler 設定 |

## 有疑慮者（功能未完全移植或不確定是否保留）

| 檔案 | 疑慮 | 建議 |
|------|------|------|
| `src/discordBot.js` | **40+ 互動指令**（/查詢獎學金、/update、/rule 等），未移植到 Python | ⚠️ 保留。這是 scholarship-monitor 最有價值的部分，需決定是否移植到 intel-pusher 或獨立運作 |
| `src/report/format.js` | 報告格式化（含地區分類、截止日過濾） | ⚠️ 部分功能已移植到 `_build_scholarship_batch()`，但完整 fmtItem/filterByDeadline 未移植 |
| `src/report/generateReport.js` | 完整報告生成（含 deadline/score/region 維度） | ⚠️ 未移植。未來如需對等報告生成需從此參考 |
| `src/sourceManager.js` | 來源 CRUD 管理 | ⚠️ 未移植。intel-pusher 目前靠 SOURCE_REGISTRY 靜態字典，無動態管理 |
| `src/sourceAI.js` | AI 輔助來源驗證 | ❓ 依使用者「AI 僅限來源合法性判斷」限制，此功能可能不再需要 |
| `src/crawler/linkExtractor.js` | 從既有來源頁面抽候選外部連結 | ⚠️ 未移植。未來如需「延伸擴充來源」功能需參考 |
| `src/crawler/sourceValidator.js` | 候選來源批次驗證（反爬/SPA/GenericCrawler） | ⚠️ 未移植。同上 |
| `src/crawler/detailParser.js` | 詳情頁解析（deadline/amount/contact 等） | ⚠️ 未移植。目前 intel-pusher 不擷取細節欄位 |
| `src/logger.js` | 結構化活動紀錄（含 activity/error 分級） | ⚠️ 未移植。intel-pusher 用 Python logging |
| `src/runLog.js` | 執行紀錄管理 | ❓ 已部分涵蓋於 db.py 的 delivery_log |
| `config/sources.seed.json` | 種子來源清單 | ⚠️ intel-pusher 的 SOURCE_REGISTRY 已內建 |
| `reports/Status_20260704.md` | 舊狀態報告 | 💡 可保留作為歷史參考 |
| `run-crawl.bat` | Windows 批次執行腳本 | 💡 `python main.py --scholarship` 已取代 |

## 可保留者（應完整保留作為參考與文件）

| 檔案 | 原因 |
|------|------|
| `README.md` | 專案說明文件 |
| `CLAUDE.md` | Claude Code 專案指令 |
| `Meta_Dev_Knowledge.md` | 開發知識庫（含 Universal Principle 等重要設計決策） |
| `Meta_User_Feedback.md` | 使用者反饋紀錄 |
| `docs/RUNBOOK.md` | 營運手冊 |
| `docs/scheduled-task-setup-guide.md` | 排程設定指南 |
| `Dockerfile` | Docker 建置設定（如需 containerized bot） |
| `docker-compose.yml` | Docker Compose 設定 |
| `package.json` | Node.js 專案設定 |
| `package-lock.json` | 依賴鎖定（如需重現 Node.js 環境） |
| `.env.example` | 環境變數範本 |
| `.gitignore` | Git 忽略規則 |
| `scholarship-monitor-admin.xml` | Windows Task Scheduler 管理排程 |
| `config/sources.seed.json` | 種子來源設定（如需動態來源管理） |

## GitHub 倉庫狀態

| 倉庫 | URL | 狀態 |
|------|-----|------|
| intel-pusher | `git@github.com-intel-pusher:lilichen-F/DC-Webhook1.git` | ✅ 現行，已包含完整 legacy 歸檔 |
| scholarship-monitor | `https://github.com/lilichen-F/scholarship-monitor-token.git` | 🔴 建議封存（archive），不再主動推送 |

## 合併後檔案結構

```
C:\Projects\intel-pusher\
├── main.py                          ← 入口（含 run_source/run_scholarship）
├── db.py / schema.sql               ← SQLite 統一資料層
├── scrapers/
│   ├── scholarship_daad.py          ← 新版 Python 爬蟲
│   ├── scholarship_moe.py
│   ├── scholarship_thu.py
│   ├── scholarship_efg.py
│   └── scholarship_util.py          ← 關鍵字計分 + 排除規則
├── config/
│   ├── scholarship_keywords.json     ← 關鍵字權重（單一真相來源）
│   ├── scholarship_profile.json      ← 使用者個人條件
│   └── scholarship_exclude.json      ← 排除詞彙字典
├── legacy/
│   └── scholarship-monitor/          ← 原始 Node.js 專案歸檔
└── intelpusher-scholarship-admin.xml ← Windows 排程
```
