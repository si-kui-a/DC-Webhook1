#!/bin/bash
# backup.sh — 本地 cron 觸發，本地 git 指令 push 到 GitHub 私有倉庫。
# GitHub 不執行任何運算，只作為異地儲存目的地，符合「最低算力本地運行」原則。
#
# 前置需求（僅需設定一次）：
#   1. 已在此目錄執行過 `git init` 且已設定 remote origin（私有倉庫）
#   2. 已設定 fine-grained PAT 或 SSH deploy key，且僅有該倉庫的 push 權限
#
# 建議排程：crontab 每日凌晨執行一次，見 crontab.example

set -e
cd "$(dirname "$0")"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 開始備份"

# 1. 加密含 PII 的檔案（產生/更新 .enc）
# 用專案 venv 的 python，不要用系統 python3——cryptography 只裝在 venv 裡，
# 系統 python3（Windows Store App Execution Alias）沒有這個套件，
# 直接呼叫 python3 會在排程執行時失敗（已在首次手動試跑時實測到這個錯誤）。
"$(dirname "$0")/venv/Scripts/python.exe" encrypt_backup.py

# 2. 只加入不含明碼個資的檔案；.env、data.db、原始 .md/.json 明碼版本
#    透過 .gitignore 排除，此處不重複列出以免兩處清單不同步。
#    intel-pusher 目前沒有 PII_FILES（見 encrypt_backup.py），所以沒有
#    對應的 .enc 檔案要加；等這個專案真的產生個資檔案時再加進來。
git add scrapers/ main.py db.py push_webhook.py encrypt_backup.py schema.sql \
        README.md requirements.txt crontab.example backup.sh \
        .gitignore .env.example Meta_Dev_Knowledge.md 2>/dev/null || true

# 3. 若無變更，git commit 會失敗，用 || true 避免中斷腳本（不視為錯誤）
git commit -m "auto backup $(date '+%Y-%m-%d %H:%M')" --allow-empty-message 2>/dev/null || true

# 4. push；失敗不應中斷主要推播流程，只記錄
if git push origin main 2>>logs/backup_errors.log; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 備份完成並已推送"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 備份 push 失敗，詳見 logs/backup_errors.log"
fi
