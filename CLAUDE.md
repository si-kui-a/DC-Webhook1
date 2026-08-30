# intel-pusher — Project Context
# 安裝位置: C:\Projects\10-501_Intel_Pusher_股票情報推播機器人\CLAUDE.md

## 類別: 金融/交易類 → 見 C:\Users\User\.claude\categories\financial-trading.md

## Stack
純Python(stdlib優先) + SQLite + Discord/Telegram Webhook + Windows Task Scheduler
排程部署腳本: scripts/setup_scheduled_tasks.ps1(見Meta_Dev_Knowledge.md PAT-14)

## 開發知識庫
Meta_Dev_Knowledge.md 記錄所有已踩過的坑/設計決策，改動前先查是否已有相關PAT條目。

**查證順序（2026-08-30訂定，移植自wordpress-builder-playbook repo的
同類規則）**：不確定的做法先查`Meta_Dev_Knowledge.md`有沒有現成PAT
條目，內部真的沒有才查外部（官方文件/WebSearch）；查證後證實真實
可用有益處的做法，直接補一則新PAT條目，不用另外問要不要記錄——查證
跟記錄是同一動作的前後兩段。

**機械複查**：`python scripts/dev_knowledge_audit.py`——PAT編號連續性
/跨檔案PAT引用完整性/過時關鍵字候選/Python檔案篇幅離群值，幾秒鐘跑完
（純stdlib，不用裝套件）。只找候選不判斷對錯，人工/AI逐一確認後才動手
改。不進CI，累積多輪修改後或懷疑文件過時時手動觸發即可，不用每次
commit都跑。

## Env
- .env 存放全部Discord webhook/GEMINI_API_KEY/DISCORD_BOT_TOKEN，AI一律不讀，只讀.env.example
- data.db 為SQLite主資料庫，不進版控
