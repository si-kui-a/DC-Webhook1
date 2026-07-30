# intel-pusher — Project Context
# 安裝位置: C:\Projects\10-501_Intel_Pusher_股票情報推播機器人\CLAUDE.md

## 類別: 金融/交易類 → 見 C:\Users\User\.claude\categories\financial-trading.md

## Stack
純Python(stdlib優先) + SQLite + Discord/Telegram Webhook + Windows Task Scheduler
排程部署腳本: scripts/setup_scheduled_tasks.ps1(見Meta_Dev_Knowledge.md PAT-14)

## 開發知識庫
Meta_Dev_Knowledge.md 記錄所有已踩過的坑/設計決策，改動前先查是否已有相關PAT條目。

## Env
- .env 存放全部Discord webhook/GEMINI_API_KEY/DISCORD_BOT_TOKEN，AI一律不讀，只讀.env.example
- data.db 為SQLite主資料庫，不進版控
